// The adb transport protocol (AOSP packages/modules/adb/protocol.txt) over any byte transport: the connection
// handshake with RSA authentication, and streams (OPEN / OKAY / WRTE / CLSE).
//
// A transport is an object with
//   read(length)  -> Promise<Uint8Array>   one read of at most `length` bytes; rejects when the device is gone
//   write(bytes)  -> Promise<void>         one write (the USB transport adds the zero-length packet itself)
//   close()       -> Promise<void>
//   framed        true when every read returns exactly one transfer of the device (USB): adbd sends a packet as two
//                 transfers, the 24-byte header and the payload
//   headerReadSize  how much to ask for when a header is expected (USB: the endpoint's packet size)
// usb.js makes one from a WebUSB device (the browser's, or the `usb` package's in Node); the tests use an in-memory
// pair, and tests/web/tcp-transport.mjs a TCP socket to a real adbd.
//
// Nothing a device sends is trusted: a header whose magic does not match, a payload longer than what was agreed, an
// answer for a stream that does not exist - each ends the connection with a "protocol" error instead of being
// guessed at.
import { concat, readU32le, text, u32le, utf8 } from "./bytes.js";
import { AdbError } from "./errors.js";
import { publicKeyLine, signToken } from "./key.js";

const cmd = (s) => (s.charCodeAt(0) | (s.charCodeAt(1) << 8) | (s.charCodeAt(2) << 16) | (s.charCodeAt(3) << 24)) >>> 0;
export const CMD = {
  CNXN: cmd("CNXN"), AUTH: cmd("AUTH"), OPEN: cmd("OPEN"), OKAY: cmd("OKAY"), CLSE: cmd("CLSE"), WRTE: cmd("WRTE"),
  STLS: cmd("STLS"),
};
const CMD_NAME = Object.fromEntries(Object.entries(CMD).map(([k, v]) => [v, k]));
export const AUTH_TOKEN = 1, AUTH_SIGNATURE = 2, AUTH_RSAPUBLICKEY = 3;
export const A_VERSION = 0x01000001;          // from this version on the payload checksum is not checked
export const MAX_PAYLOAD = 256 * 1024;        // what this client accepts in one packet
const MIN_PAYLOAD = 4096;                     // adb's first version
export const HEADER_SIZE = 24;
// what this client can do; a feature counts only when the device names it too
export const FEATURES = ["shell_v2", "cmd"];

export function checksum(payload) {
  let s = 0;
  for (let i = 0; i < payload.length; i++) s = (s + payload[i]) >>> 0;
  return s;
}

export function encodeHeader(command, arg0, arg1, payload) {
  const h = new Uint8Array(HEADER_SIZE);
  const v = new DataView(h.buffer);
  v.setUint32(0, command, true);
  v.setUint32(4, arg0 >>> 0, true);
  v.setUint32(8, arg1 >>> 0, true);
  v.setUint32(12, payload.length, true);
  // always sent: a device from before A_VERSION checks it, a newer one ignores the field
  v.setUint32(16, checksum(payload), true);
  v.setUint32(20, (command ^ 0xffffffff) >>> 0, true);
  return h;
}

export function decodeHeader(h) {
  if (h.length !== HEADER_SIZE) return null;
  const command = readU32le(h, 0);
  if (readU32le(h, 20) !== ((command ^ 0xffffffff) >>> 0)) return null;
  return { command, arg0: readU32le(h, 4), arg1: readU32le(h, 8), length: readU32le(h, 12), check: readU32le(h, 16) };
}

// "device::ro.product.name=x;ro.product.model=y;features=a,b" -> { state, props, features }
export function parseBanner(payload) {
  let s = text(payload);
  const nul = s.indexOf("\0");
  if (nul >= 0) s = s.slice(0, nul);
  const m = /^([a-z]*):([^:]*):(.*)$/s.exec(s);
  const out = { state: m ? m[1] : "", props: {}, features: new Set(), raw: s };
  if (!m) return out;
  for (const part of m[3].split(";")) {
    const eq = part.indexOf("=");
    if (eq <= 0) continue;
    const k = part.slice(0, eq), v = part.slice(eq + 1);
    if (k === "features") v.split(",").filter(Boolean).forEach((f) => out.features.add(f));
    else out.props[k] = v;
  }
  return out;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  promise.catch(() => {});   // a rejection nobody waits for is not an unhandled one
  return { promise, resolve, reject };
}

export class AdbStream {
  constructor(conn, localId, service) {
    this.conn = conn;
    this.localId = localId;
    this.remoteId = 0;
    this.service = service;
    this.opened = deferred();
    this.chunks = [];          // received, not yet read
    this.waiter = null;        // a read() waiting for the next chunk
    this.ack = null;           // a write() waiting for OKAY
    this.writeChain = Promise.resolve();
    this.ended = false;        // the device closed, or the connection is gone
    this.error = null;
    this.localClosed = false;
  }

  // The next chunk the device sent, or null at the end of the stream. The OKAY for a chunk is sent when it is taken
  // here, so a slow consumer (a file being written) holds the device back instead of filling memory.
  async read() {
    for (;;) {
      if (this.chunks.length) {
        const c = this.chunks.shift();
        if (!this.ended && !this.localClosed) await this.conn._send(CMD.OKAY, this.localId, this.remoteId);
        return c;
      }
      if (this.error) throw this.error;
      if (this.ended || this.localClosed) return null;
      this.waiter = deferred();
      await this.waiter.promise;
    }
  }

  // Sends data, in packets of at most the agreed payload, each acknowledged by the device before the next.
  write(data) {
    const run = async () => {
      const max = this.conn.maxPayload;
      for (let o = 0; o < data.length; o += max) {
        if (this.error) throw this.error;
        if (this.ended || this.localClosed) throw new AdbError("closed", `the device closed ${this.service}`);
        this.ack = deferred();
        const ack = this.ack;
        await this.conn._send(CMD.WRTE, this.localId, this.remoteId, data.subarray(o, o + max));
        await ack.promise;
      }
    };
    const p = this.writeChain.then(run);
    this.writeChain = p.catch(() => {});
    return p;
  }

  async close() {
    if (this.localClosed) return;
    this.localClosed = true;
    this.conn.streams.delete(this.localId);
    if (this.waiter) this.waiter.resolve();
    if (this.ack) this.ack.reject(new AdbError("closed", "the stream was closed"));
    if (!this.ended && this.remoteId) {
      try { await this.conn._send(CMD.CLSE, this.localId, this.remoteId); } catch (e) { /* the device is gone */ }
    }
  }

  _end(error) {
    this.ended = true;
    if (error && !this.error) this.error = error;
    if (this.waiter) this.waiter.resolve();
    if (this.ack) this.ack.reject(error || new AdbError("closed", `the device closed ${this.service}`));
    this.opened.reject(error || new AdbError("remote", `the device refused ${this.service}`));
  }
}

export class AdbConnection {
  // options: key (adb/key.js), hostName, onAuth(state) with state "signature" | "public-key", log(line)
  constructor(transport, options = {}) {
    this.transport = transport;
    this.key = options.key || null;
    this.onAuth = options.onAuth || (() => {});
    this.log = options.log || (() => {});
    this.maxPayload = MIN_PAYLOAD;
    this.version = 0;
    this.banner = null;
    this.features = new Set();
    this.streams = new Map();
    this.nextId = 1;
    this.closed = false;
    this.closeError = null;
    this.spareAnswers = 0;
    this._onStray = null;
    this.strayWaitMs = options.strayWaitMs === undefined ? 700 : options.strayWaitMs;
    this.sendChain = Promise.resolve();
    this.onClose = options.onClose || (() => {});
    this.connected = false;
  }

  _send(command, arg0, arg1, payload = new Uint8Array(0)) {
    const run = async () => {
      if (this.closed) throw this.closeError || new AdbError("disconnected", "the connection is closed");
      try {
        await this.transport.write(encodeHeader(command, arg0, arg1, payload));
        if (payload.length) await this.transport.write(payload);
      } catch (e) {
        const err = e instanceof AdbError ? e : new AdbError("disconnected", "the device stopped answering", e);
        this._fail(err);
        throw err;
      }
    };
    const p = this.sendChain.then(run);
    this.sendChain = p.catch(() => {});
    return p;
  }

  async _readFull(length) {
    const parts = [];
    let have = 0;
    while (have < length) {
      const c = await this.transport.read(length - have);
      if (c.length > length - have) throw new AdbError("protocol", "the device sent more than the packet says");
      parts.push(c);
      have += c.length;
    }
    return parts.length === 1 ? parts[0] : concat(...parts);
  }

  // The next packet. handshake: what is not a packet header is skipped (a device still sending the rest of an
  // earlier session), a limited number of times; afterwards it ends the connection.
  async _readPacket(handshake) {
    let header = null;
    for (let skipped = 0, empty = 0; ; skipped++) {
      let raw;
      if (this.transport.framed) raw = await this.transport.read(Math.max(HEADER_SIZE, this.transport.headerReadSize || HEADER_SIZE));
      else raw = await this._readFull(HEADER_SIZE);
      // a zero-length transfer where a header is due (the end mark of a payload that filled its last USB packet)
      if (this.transport.framed && raw.length === 0 && empty++ < 8) { skipped--; continue; }
      header = decodeHeader(raw);
      if (header && CMD_NAME[header.command]) break;
      if (!handshake || skipped >= 64) throw new AdbError("protocol", "the device sent something that is not an adb packet");
      if (this._onStray) this._onStray(raw);
    }
    if (header.length > MAX_PAYLOAD) {
      throw new AdbError("protocol", `the device announced a packet of ${header.length} bytes; the limit is ${MAX_PAYLOAD}`);
    }
    const payload = header.length ? await this._readFull(header.length) : new Uint8Array(0);
    if (this.version && this.version < A_VERSION && checksum(payload) !== header.check) {
      throw new AdbError("protocol", "a packet from the device has a wrong checksum");
    }
    return { ...header, payload };
  }

  // The handshake. Resolves with the device's banner once the device says CNXN; while the device waits for someone
  // to accept the key, this waits too (close() ends it).
  async connect(hostName = "mu300-web") {
    const banner = utf8(`host::features=${FEATURES.join(",")}`);
    let signs = 0, keySent = false;
    // A payload that comes where a header is due, and then nothing: the header of the device's answer was lost on
    // the way. That happens over USB when the two ends of the bulk endpoint are out of step at the start of a
    // session (the session before ended without a goodbye: a closed tab, a browser that was quit; measured on an
    // F50, FINDINGS 72): exactly one packet is dropped, and the device then waits for a host that waits for the
    // device. The device has taken our CNXN, so it is asked once more; adbd answers a second CNXN like the first,
    // and this time the answer arrives whole. What an earlier session left behind looks the same for a moment, but
    // the real answer follows it at once: so the question is repeated only after a pause in which nothing came.
    let asked = 1, stray = null;
    const settle = () => { if (stray) { clearTimeout(stray); stray = null; } };
    this._onStray = () => {
      if (asked >= 2 || signs || stray) return;
      stray = setTimeout(() => {
        stray = null;
        if (this.closed || this.connected || asked >= 2 || signs) return;
        asked++;
        this.log("the device's answer came without its header: asking again");
        this._send(CMD.CNXN, A_VERSION, MAX_PAYLOAD, banner).catch(() => {});
      }, this.strayWaitMs);
    };
    try {
      await this._send(CMD.CNXN, A_VERSION, MAX_PAYLOAD, banner);
      for (;;) {
        const p = await this._readPacket(true);
        settle();
        if (p.command === CMD.CNXN) {
          this.version = Math.min(p.arg0, A_VERSION);
          if (p.arg1 < MIN_PAYLOAD) throw new AdbError("protocol", "the device announced an impossible packet size");
          this.maxPayload = Math.min(p.arg1, MAX_PAYLOAD);
          this.banner = parseBanner(p.payload);
          for (const f of FEATURES) if (this.banner.features.has(f)) this.features.add(f);
          break;
        }
        if (p.command === CMD.STLS) throw new AdbError("unsupported", "the device asks for adb over TLS, which this page does not do");
        if (p.command !== CMD.AUTH) continue;          // anything else before CNXN belongs to an earlier session
        if (p.arg0 !== AUTH_TOKEN) continue;
        if (!this.key) throw new AdbError("auth", "the device asks for a key and this page has none");
        if (signs < asked) {
          signs++;
          this.onAuth("signature");
          await this._send(CMD.AUTH, AUTH_SIGNATURE, 0, signToken(this.key, p.payload));
        } else if (!keySent) {
          keySent = true;
          this.onAuth("public-key");
          await this._send(CMD.AUTH, AUTH_RSAPUBLICKEY, 0, utf8(publicKeyLine({ ...this.key, name: hostName }) + "\0"));
        }
        // a token after the public key: the device is still waiting for someone to accept it
      }
    } catch (e) {
      settle();
      const err = e instanceof AdbError ? e : new AdbError("disconnected", "the device stopped answering", e);
      this._fail(err);
      throw err;
    }
    settle();
    this._onStray = null;
    this.spareAnswers = asked - 1;
    this.connected = true;
    this._loop();
    return this.banner;
  }

  async _loop() {
    try {
      for (;;) {
        const p = await this._readPacket(false);
        const s = this.streams.get(p.arg1);
        switch (p.command) {
          case CMD.OKAY:
            if (!s) break;                              // an answer for a stream closed here in the meantime
            if (!s.remoteId) {
              if (!p.arg0) throw new AdbError("protocol", "the device opened a stream without an id");
              s.remoteId = p.arg0;
              s.opened.resolve(s);
            } else if (p.arg0 !== s.remoteId) {
              throw new AdbError("protocol", "the device acknowledged a stream with another id");
            } else if (s.ack) {
              const a = s.ack; s.ack = null; a.resolve();
            }
            break;
          case CMD.WRTE:
            if (!s || !s.remoteId || p.arg0 !== s.remoteId) {
              // data for nothing we have open: tell the device so, once
              if (p.arg0) await this._send(CMD.CLSE, p.arg1, p.arg0);
              break;
            }
            s.chunks.push(p.payload);
            if (s.waiter) { const w = s.waiter; s.waiter = null; w.resolve(); }
            break;
          case CMD.CLSE:
            if (!s) break;
            // arg0 is 0 for a refused OPEN, and old adbds send 0 for an ordinary close as well
            if (p.arg0 && s.remoteId && p.arg0 !== s.remoteId) break;
            this.streams.delete(p.arg1);
            if (!s.localClosed && s.remoteId) await this._send(CMD.CLSE, s.localId, s.remoteId);
            s._end(null);
            break;
          case CMD.CNXN:
            // the answer to a CNXN that was asked twice during the handshake (connect()): expected once
            if (this.spareAnswers > 0) { this.spareAnswers--; break; }
            throw new AdbError("protocol", "the device restarted the connection");
          case CMD.AUTH: case CMD.STLS:
            throw new AdbError("protocol", "the device restarted the connection");
          case CMD.OPEN:
            await this._send(CMD.CLSE, 0, p.arg0);       // this client offers no services to the device
            break;
          default:
            break;
        }
      }
    } catch (e) {
      this._fail(e instanceof AdbError ? e : new AdbError("disconnected", "the device stopped answering", e));
    }
  }

  _fail(err) {
    if (this.closed) return;
    this.closed = true;
    this.closeError = err;
    for (const s of this.streams.values()) s._end(err);
    this.streams.clear();
    Promise.resolve(this.transport.close()).catch(() => {});
    try { this.onClose(err); } catch (e) { /* the listener's problem */ }
  }

  // Opens a service ("shell,v2,raw:id", "sync:", "reboot:"); rejects with "remote" when the device refuses it.
  async open(service) {
    if (this.closed) throw this.closeError || new AdbError("disconnected", "the connection is closed");
    if (!this.connected) throw new AdbError("protocol", "open() before connect()");
    const payload = utf8(service + "\0");
    if (payload.length > this.maxPayload) throw new AdbError("protocol", "the command is longer than one adb packet");
    const id = this.nextId++;
    if (this.nextId > 0x7fffffff) this.nextId = 1;
    const s = new AdbStream(this, id, service);
    this.streams.set(id, s);
    await this._send(CMD.OPEN, id, 0, payload);
    return s.opened.promise;
  }

  async close() {
    this._fail(new AdbError("disconnected", "the connection was closed by this page"));
  }
}
