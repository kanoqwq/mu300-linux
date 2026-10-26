// A scripted adb daemon for the tests: the device side of the protocol, written from AOSP's protocol.txt and
// SYNC.TXT on its own (it shares no code with the client under test, and checks the client's RSA signature with
// Node's crypto), with switches for what a broken or hostile device does.
//
//   const dev = new FakeAdbd({ shell: (cmd) => ({ stdout: "hi\n", exitCode: 0 }) });
//   const conn = new AdbConnection(dev.transport(), { key });
//
// options:
//   auth        "open" (no key asked) | "keys" (only `authorized` public key lines get in)
//   authorized  public key lines ("BASE64 name") that are accepted
//   acceptNewKey  true: a key sent with AUTH RSAPUBLICKEY is accepted (the user tapped "allow") after acceptDelayMs
//   features    what the banner names (default shell_v2,cmd)
//   maxPayload, version   what CNXN announces
//   shell(cmd, { stdin })  -> { stdout, stderr, exitCode } | Promise of it | async generator yielding
//                 { out } / { err } / { wait: ms } and returning the exit code
//   files       Map path -> Uint8Array: the device's files for sync
//   sendFail(path) -> message | undefined   a push to path is refused with that message
//   mutate(packet, n) -> undefined | transfers[]   replace what the device sends for its n-th packet
//   dieAfterHostBytes   the transport breaks (as a pulled cable does) once the host has written that many bytes
//   dieAfterDeviceBytes the same once the device has sent that many
import crypto from "node:crypto";

const C = { CNXN: 0x4e584e43, AUTH: 0x48545541, OPEN: 0x4e45504f, OKAY: 0x59414b4f, CLSE: 0x45534c43, WRTE: 0x45545257 };
const te = new TextEncoder(), td = new TextDecoder();
const cat = (...a) => Buffer.concat(a.map((x) => Buffer.from(x.buffer ? Buffer.from(x.buffer, x.byteOffset, x.byteLength) : x)));
const le32 = (v) => { const b = Buffer.alloc(4); b.writeUInt32LE(v >>> 0); return b; };

export function packet(cmd, arg0, arg1, payload = Buffer.alloc(0)) {
  const h = Buffer.alloc(24);
  h.writeUInt32LE(cmd, 0); h.writeUInt32LE(arg0 >>> 0, 4); h.writeUInt32LE(arg1 >>> 0, 8);
  h.writeUInt32LE(payload.length, 12);
  let sum = 0; for (const b of payload) sum = (sum + b) >>> 0;
  h.writeUInt32LE(sum, 16); h.writeUInt32LE((cmd ^ 0xffffffff) >>> 0, 20);
  return payload.length ? [h, Buffer.from(payload)] : [h];
}

// the RSA public key of an Android key line (base64 of len | n0inv | n LE | rr LE | e), as a Node KeyObject
export function nodeKeyFromLine(line) {
  const raw = Buffer.from(line.split(" ")[0], "base64");
  if (raw.length !== 524 || raw.readUInt32LE(0) !== 64) throw new Error("not an Android RSA-2048 public key");
  const n = Buffer.from(raw.subarray(8, 264)).reverse();
  const e = raw.readUInt32LE(520);
  const b64u = (b) => Buffer.from(b).toString("base64url");
  const eb = Buffer.alloc(4); eb.writeUInt32BE(e);
  return crypto.createPublicKey({ key: { kty: "RSA", n: b64u(n), e: b64u(eb.subarray(eb.findIndex((x) => x !== 0))) }, format: "jwk" });
}

// does `signature` sign the 20-byte token the way adb does (PKCS#1 v1.5 with the token as a SHA-1 digest)?
export function verifyAdbSignature(line, token, signature) {
  const key = nodeKeyFromLine(line);
  let em;
  try { em = crypto.publicDecrypt({ key, padding: crypto.constants.RSA_NO_PADDING }, Buffer.from(signature)); } catch (e) { return false; }
  const digestInfo = Buffer.from("3021300906052b0e03021a05000414", "hex");
  const want = Buffer.concat([Buffer.from([0, 1]), Buffer.alloc(256 - 3 - digestInfo.length - 20, 0xff), Buffer.from([0]), digestInfo, Buffer.from(token)]);
  return em.equals(want);
}

export class FakeAdbd {
  constructor(options = {}) {
    this.o = { auth: "open", authorized: [], acceptNewKey: false, acceptDelayMs: 0, features: ["shell_v2", "cmd"],
      maxPayload: 256 * 1024, version: 0x01000001, files: new Map(), ...options };
    this.files = this.o.files;
    this.out = [];              // transfers waiting for the host
    this.waiter = null;
    this.inbuf = Buffer.alloc(0);
    this.dead = false;
    this.online = false;
    this.token = null;
    this.streams = new Map();   // device id -> { hostId, service, onData, pendingAck }
    this.nextId = 100;
    this.sentPackets = 0;
    this.hostBytes = 0;
    this.deviceBytes = 0;
    this.log = [];              // [dir, cmd name, arg0, arg1, length]
    this.shellCalls = [];
    this.keysSeen = [];
    this.zeroWrites = 0;
  }

  transport() {
    const self = this;
    return {
      framed: true, headerReadSize: 512,
      async read(length) {
        for (;;) {
          if (self.out.length) {
            const t = self.out[0];
            if (t.length <= length) { self.out.shift(); return new Uint8Array(t); }
            self.out[0] = t.subarray(length);
            return new Uint8Array(t.subarray(0, length));
          }
          if (self.dead) { const e = new Error("The device was disconnected."); e.name = "NetworkError"; throw e; }
          await new Promise((r) => { self.waiter = r; });
        }
      },
      async write(bytes) {
        if (self.dead) { const e = new Error("The device was disconnected."); e.name = "NetworkError"; throw e; }
        if (!bytes.length) { self.zeroWrites++; return; }
        self.hostBytes += bytes.length;
        self.inbuf = Buffer.concat([self.inbuf, Buffer.from(bytes)]);
        if (self.o.dieAfterHostBytes !== undefined && self.hostBytes >= self.o.dieAfterHostBytes) { self.unplug(); return; }
        // never waited for: a real device takes the bytes and answers when it answers
        try { self._drain(); } catch (e) { self.error = e; self.unplug(); }
      },
      async close() { self.unplug(); },
    };
  }

  unplug() {
    this.dead = true;
    if (this.waiter) { const w = this.waiter; this.waiter = null; w(); }
  }

  _queue(transfers) {
    for (const t of transfers) {
      if (this.dead) return;
      this.deviceBytes += t.length;
      this.out.push(Buffer.from(t));
      if (this.o.dieAfterDeviceBytes !== undefined && this.deviceBytes >= this.o.dieAfterDeviceBytes) { this.unplug(); return; }
    }
    if (this.waiter) { const w = this.waiter; this.waiter = null; w(); }
  }

  send(cmd, arg0, arg1, payload = Buffer.alloc(0)) {
    const n = this.sentPackets++;
    this.log.push(["dev", cmd, arg0, arg1, payload.length]);
    let transfers = packet(cmd, arg0, arg1, payload);
    if (this.o.mutate) {
      const m = this.o.mutate({ cmd, arg0, arg1, payload, name: Object.keys(C).find((k) => C[k] === cmd) }, n);
      if (m) transfers = m;
    }
    this._queue(transfers);
  }

  _drain() {
    while (this.inbuf.length >= 24) {
      const len = this.inbuf.readUInt32LE(12);
      if (this.inbuf.length < 24 + len) return;
      const p = { cmd: this.inbuf.readUInt32LE(0), arg0: this.inbuf.readUInt32LE(4), arg1: this.inbuf.readUInt32LE(8),
        check: this.inbuf.readUInt32LE(16), magic: this.inbuf.readUInt32LE(20), payload: this.inbuf.subarray(24, 24 + len) };
      this.inbuf = this.inbuf.subarray(24 + len);
      this.log.push(["host", p.cmd, p.arg0, p.arg1, p.payload.length]);
      let sum = 0; for (const b of p.payload) sum = (sum + b) >>> 0;
      if (p.magic !== ((p.cmd ^ 0xffffffff) >>> 0)) throw new Error("host sent a bad magic");
      if (p.check !== sum) throw new Error("host sent a bad checksum");
      this._handle(p);
    }
  }

  _banner() {
    return Buffer.from(`device::ro.product.name=${this.o.product || "MU300"};ro.product.model=${this.o.model || "F50"};ro.product.device=${this.o.device || "mu300"};features=${this.o.features.join(",")}`);
  }

  _goOnline() {
    this.online = true;
    this.send(C.CNXN, this.o.version, this.o.maxPayload, this.o.banner !== undefined ? Buffer.from(this.o.banner) : this._banner());
  }

  _challenge() {
    this.token = crypto.randomBytes(this.o.tokenSize || 20);
    this.send(C.AUTH, 1, 0, this.token);
  }

  _handle(p) {
    switch (p.cmd) {
      case C.CNXN:
        this.hostBanner = td.decode(p.payload);
        this.hostVersion = p.arg0; this.hostMax = p.arg1;
        this.online = false; this.streams.clear();
        if (this.o.auth === "open") this._goOnline(); else this._challenge();
        break;
      case C.AUTH:
        if (p.arg0 === 2) {
          const ok = this.o.authorized.some((line) => verifyAdbSignature(line, this.token, p.payload));
          this.signatureSeen = Buffer.from(p.payload);
          if (ok) this._goOnline(); else this._challenge();
        } else if (p.arg0 === 3) {
          const line = td.decode(p.payload).replace(/\0+$/, "");
          this.keysSeen.push(line);
          if (this.o.acceptNewKey) {
            this.o.authorized.push(line);
            if (this.o.acceptDelayMs) setTimeout(() => this._goOnline(), this.o.acceptDelayMs); else this._goOnline();
          }
        }
        break;
      case C.OPEN: {
        if (!this.online) return;
        const service = td.decode(p.payload).replace(/\0+$/, "");
        const id = this.nextId++;
        const refuse = () => this.send(C.CLSE, 0, p.arg0);
        const st = { hostId: p.arg0, id, service, acks: [], closed: false, queue: [], onData: null };
        if (service.startsWith("shell,v2,raw:") && this.o.features.includes("shell_v2")) this._shell(st, service.slice(13), true);
        else if (service.startsWith("shell:")) this._shell(st, service.slice(6), false);
        else if (service === "sync:") this._sync(st);
        else if (service.startsWith("reboot:")) { this.rebooted = service.slice(7); this.streams.set(id, st); this.send(C.OKAY, id, p.arg0); this.send(C.CLSE, id, p.arg0); if (this.o.rebootUnplugs !== false) setTimeout(() => this.unplug(), 0); return; }
        else return refuse();
        this.streams.set(id, st);
        this.send(C.OKAY, id, p.arg0);
        st.start();
        break;
      }
      case C.OKAY: {
        const st = this.streams.get(p.arg1);
        if (st && st.acks.length) st.acks.shift()();
        break;
      }
      case C.WRTE: {
        const st = this.streams.get(p.arg1);
        if (!st) { this.send(C.CLSE, 0, p.arg0); break; }
        if (!this.o.neverAck) this.send(C.OKAY, st.id, st.hostId);
        if (st.onData) {
          const d = Buffer.from(p.payload);
          st.chain = (st.chain || Promise.resolve()).then(() => st.onData(d)).catch((e) => { this.error = e; this.unplug(); });
        }
        break;
      }
      case C.CLSE: {
        const st = this.streams.get(p.arg1);
        if (st) { st.closed = true; this.streams.delete(p.arg1); for (const a of st.acks) a(); if (st.onClose) st.onClose(); }
        break;
      }
      default: break;
    }
  }

  // device -> host data on a stream, waiting for the host's OKAY after each packet
  async _write(st, data) {
    data = Buffer.from(data);
    const max = Math.min(this.o.maxPayload, this.hostMax || 4096);
    for (let o = 0; o < data.length; o += max) {
      if (st.closed || this.dead) return;
      const ack = new Promise((r) => st.acks.push(r));
      this.send(C.WRTE, st.id, st.hostId, data.subarray(o, o + max));
      await ack;
    }
  }

  _close(st) {
    if (st.closed) return;
    st.closed = true;
    this.streams.delete(st.id);
    this.send(C.CLSE, st.id, st.hostId);
  }

  _shell(st, cmd, v2) {
    let stdin = Buffer.alloc(0), stdinClosed = false;
    let rest = Buffer.alloc(0);
    const stdinWaiters = [];
    st.onData = (d) => {
      if (!v2) { stdin = Buffer.concat([stdin, d]); return; }
      rest = Buffer.concat([rest, d]);
      while (rest.length >= 5) {
        const len = rest.readUInt32LE(1);
        if (rest.length < 5 + len) break;
        if (rest[0] === 0) stdin = Buffer.concat([stdin, rest.subarray(5, 5 + len)]);
        if (rest[0] === 4) { stdinClosed = true; stdinWaiters.splice(0).forEach((w) => w()); }
        rest = rest.subarray(5 + len);
      }
    };
    const frame = (id, data) => (v2 ? Buffer.concat([Buffer.from([id]), le32(data.length), Buffer.from(data)]) : Buffer.from(data));
    st.start = async () => {
      let userCmd = cmd, rcMark = null;
      if (!v2) { const m = /; echo (MU300-ADB-RC=)\$\?$/.exec(cmd); if (m) { userCmd = cmd.slice(0, m.index); rcMark = m[1]; } }
      const call = { cmd: userCmd, v2, killed: false };
      this.shellCalls.push(call);
      st.onClose = () => { call.killed = true; };
      const ctx = { get stdin() { return stdin.toString(); }, waitStdin: () => (stdinClosed ? Promise.resolve() : new Promise((r) => stdinWaiters.push(r))), call };
      let rc = 0;
      try {
        const r0 = this.o.shell ? this.o.shell(userCmd, ctx) : { stdout: "", exitCode: 0 };
        // a handler may be async and still hand back a generator
        const r = r0 && typeof r0.then === "function" ? await r0 : r0;
        if (r && typeof r[Symbol.asyncIterator] === "function") {
          const it = r[Symbol.asyncIterator]();
          for (;;) {
            const { value, done } = await it.next();
            if (done) { rc = value ?? 0; break; }
            if (st.closed || this.dead) { call.killed = true; if (it.return) await it.return(); return; }
            if (value.wait) await new Promise((res) => setTimeout(res, value.wait));
            if (value.out !== undefined) await this._write(st, frame(1, Buffer.from(value.out)));
            if (value.err !== undefined) await this._write(st, frame(v2 ? 2 : 1, Buffer.from(value.err)));
            if (value.raw !== undefined) await this._write(st, Buffer.from(value.raw));
          }
        } else {
          const v = r || {};
          if (v.stdout) await this._write(st, frame(1, Buffer.from(v.stdout)));
          if (v.stderr) await this._write(st, frame(v2 ? 2 : 1, Buffer.from(v.stderr)));
          rc = v.exitCode ?? 0;
        }
      } catch (e) {
        await this._write(st, frame(v2 ? 2 : 1, Buffer.from(`sh: ${e.message}\n`)));
        rc = 127;
      }
      if (v2) { if (!this.o.noExitPacket) await this._write(st, Buffer.concat([Buffer.from([3]), le32(1), Buffer.from([rc & 0xff])])); }
      else if (rcMark) await this._write(st, Buffer.from(`${rcMark}${rc}\n`));
      this._close(st);
    };
  }

  _sync(st) {
    let buf = Buffer.alloc(0);
    let state = null;   // { path, mode, parts, failed }
    const reply = (name, n, extra = Buffer.alloc(0)) => this._write(st, Buffer.concat([Buffer.from(name), le32(n), extra]));
    st.start = () => {};
    st.onData = async (d) => {
      buf = Buffer.concat([buf, d]);
      for (;;) {
        if (buf.length < 8) return;
        const id = buf.subarray(0, 4).toString(), n = buf.readUInt32LE(4);
        if (id === "DONE") {
          buf = buf.subarray(8);
          if (!state) { await reply("FAIL", 12, Buffer.from("no file open")); continue; }
          const s = state; state = null;
          if (s.failed) { await reply("FAIL", Buffer.byteLength(s.failed), Buffer.from(s.failed)); if (this.o.failCloses !== false) this._close(st); continue; }
          this.files.set(s.path, new Uint8Array(Buffer.concat(s.parts)));
          this.modes = this.modes || new Map(); this.modes.set(s.path, s.mode); this.mtimes = this.mtimes || new Map(); this.mtimes.set(s.path, n);
          if (this.o.syncAnswer) await this._write(st, Buffer.from(this.o.syncAnswer)); else await reply("OKAY", 0);
          continue;
        }
        if (id === "QUIT") { buf = buf.subarray(8); this._close(st); return; }
        if (buf.length < 8 + n) return;
        const data = buf.subarray(8, 8 + n);
        buf = buf.subarray(8 + n);
        if (id === "STAT") {
          const f = this.files.get(data.toString());
          await this._write(st, Buffer.concat([Buffer.from("STAT"), le32(f ? 0o100644 : 0), le32(f ? f.length : 0), le32(f ? 1700000000 : 0)]));
        } else if (id === "SEND") {
          const spec = data.toString(); const comma = spec.lastIndexOf(",");
          state = { path: spec.slice(0, comma), mode: Number(spec.slice(comma + 1)), parts: [], failed: null };
          const why = this.o.sendFail && this.o.sendFail(state.path);
          if (why) { state.failed = why; if (this.o.failEarly) { await reply("FAIL", Buffer.byteLength(why), Buffer.from(why)); state.failed = null; state.discard = true; } }
        } else if (id === "DATA") {
          if (state && !state.failed && !state.discard) state.parts.push(Buffer.from(data));
        } else if (id === "RECV") {
          const path = data.toString();
          const f = this.files.get(path);
          if (!f) { const m = "open failed: No such file or directory"; await reply("FAIL", m.length, Buffer.from(m)); continue; }
          const hook = this.o.recvHook || ((chunks) => chunks);
          const chunks = [];
          for (let o = 0; o < f.length; o += 65536) chunks.push(Buffer.concat([Buffer.from("DATA"), le32(Math.min(65536, f.length - o)), Buffer.from(f.subarray(o, o + 65536))]));
          chunks.push(Buffer.concat([Buffer.from("DONE"), le32(0)]));
          for (const c of hook(chunks, path)) await this._write(st, c);
        } else {
          await reply("FAIL", 15, Buffer.from("unknown command"));
        }
      }
    };
  }
}

export { C as COMMANDS };
