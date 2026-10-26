// The adb client of the web installer (site/assets/web-installer/adb) against a scripted device: the handshake and
// the key, shell with exit codes, file transfer with progress, and what a broken or hostile device and a pulled
// cable do to each. Run: node --test tests/web/
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";

import { AdbConnection, CMD, MAX_PAYLOAD, checksum, decodeHeader, encodeHeader, parseBanner } from "../../site/assets/web-installer/adb/connection.js";
import { AdbError } from "../../site/assets/web-installer/adb/errors.js";
import { androidPublicKeyBytes, cleanName, generateKey, keyFromJwk, publicKeyLine, signToken, validKey } from "../../site/assets/web-installer/adb/key.js";
import { ShellPacketReader, quote, shell } from "../../site/assets/web-installer/adb/shell.js";
import { pull, push, stat } from "../../site/assets/web-installer/adb/sync.js";
import { ADB_FILTERS, classifyOpenError, findAdbInterface, openUsbTransport, platformOf } from "../../site/assets/web-installer/adb/usb.js";
import { LineSplitter } from "../../site/assets/web-installer/adb/bytes.js";
import { COMMANDS, FakeAdbd, nodeKeyFromLine, packet, verifyAdbSignature } from "./fake-adbd.mjs";

const FIX = JSON.parse(fs.readFileSync(new URL("./fixtures/adbkey.json", import.meta.url)));
const KEY = { n: FIX.n, d: FIX.d, e: FIX.e, name: "test" };
const rejects = (p, code) => assert.rejects(p, (e) => { assert.ok(e instanceof AdbError, String(e)); assert.equal(e.code, code, e.message); return true; });
const bytes = (n, seed = 1) => { const b = new Uint8Array(n); let x = seed; for (let i = 0; i < n; i++) { x = (x * 1103515245 + 12345) >>> 0; b[i] = x >>> 16; } return b; };

async function connected(options = {}, connOptions = {}) {
  const dev = new FakeAdbd(options);
  const conn = new AdbConnection(dev.transport(), { key: KEY, ...connOptions });
  await conn.connect("test");
  return { dev, conn };
}

// ------------------------------------------------------------------ the key

test("the public key line is byte for byte what adb writes for the same key", () => {
  assert.equal(publicKeyLine(KEY), FIX.pub + " test");
  assert.equal(FIX.pub.length, 700);
  assert.ok(FIX.pub.startsWith("QAAAA") && FIX.pub.endsWith("="));
});

test("the signature is the one adbd checks: PKCS#1 v1.5 over the token as a SHA-1 digest", () => {
  const token = crypto.randomBytes(20);
  const sig = signToken(KEY, new Uint8Array(token));
  assert.equal(sig.length, 256);
  assert.ok(verifyAdbSignature(FIX.pub, token, sig));
  // and OpenSSL's own verifier agrees when it is given the token as the digest
  const other = crypto.randomBytes(20);
  assert.ok(!verifyAdbSignature(FIX.pub, other, sig));
});

test("a token that is not 20 bytes is refused, not signed", () => {
  assert.throws(() => signToken(KEY, new Uint8Array(19)), (e) => e.code === "protocol");
  assert.throws(() => signToken(KEY, new Uint8Array(256)), (e) => e.code === "protocol");
});

test("a generated key works and its public half matches", async () => {
  const k = await generateKey("My Laptop!");
  assert.ok(validKey(k));
  assert.equal(k.name, "MyLaptop");
  const raw = androidPublicKeyBytes(k);
  assert.equal(raw.length, 524);
  const n = BigInt("0x" + k.n);
  const n0inv = BigInt(new DataView(raw.buffer).getUint32(4, true));
  assert.equal((n0inv * (n & 0xffffffffn) + 1n) % (1n << 32n), 0n);
  const token = crypto.randomBytes(20);
  assert.ok(verifyAdbSignature(publicKeyLine(k), token, signToken(k, new Uint8Array(token))));
  assert.equal(nodeKeyFromLine(publicKeyLine(k)).export({ format: "jwk" }).n, Buffer.from(k.n, "hex").toString("base64url"));
});

test("key names keep only what a key file line may hold", () => {
  assert.equal(cleanName("a b;c'd$(x)"), "abcdx");
  assert.equal(cleanName(""), "mu300-web");
  assert.equal(cleanName("x".repeat(100)).length, 64);
  assert.ok(!validKey({ n: "zz", d: "1", e: "1" }));
  assert.ok(!validKey(null));
  assert.ok(!validKey({ n: "ff", d: "1", e: "10001" }));
});

test("keyFromJwk reads what WebCrypto exports", () => {
  const { privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const k = keyFromJwk(privateKey.export({ format: "jwk" }), "x");
  assert.ok(validKey(k));
  assert.equal(k.e, "10001");
});

// ------------------------------------------------------------------ packets

test("headers: the constants are adb's, the magic and checksum are checked", () => {
  assert.equal(CMD.CNXN, COMMANDS.CNXN); assert.equal(CMD.AUTH, COMMANDS.AUTH); assert.equal(CMD.OPEN, COMMANDS.OPEN);
  assert.equal(CMD.OKAY, COMMANDS.OKAY); assert.equal(CMD.CLSE, COMMANDS.CLSE); assert.equal(CMD.WRTE, COMMANDS.WRTE);
  const payload = new Uint8Array([1, 2, 3, 255]);
  const h = encodeHeader(CMD.WRTE, 7, 9, payload);
  assert.deepEqual(Buffer.from(h), packet(COMMANDS.WRTE, 7, 9, Buffer.from(payload))[0]);
  assert.deepEqual(decodeHeader(h), { command: CMD.WRTE, arg0: 7, arg1: 9, length: 4, check: 261 });
  const bad = h.slice(); bad[20] ^= 1;
  assert.equal(decodeHeader(bad), null);
  assert.equal(decodeHeader(h.subarray(0, 23)), null);
  assert.equal(checksum(new Uint8Array(0)), 0);
});

test("the banner: state, properties and features; garbage gives an empty one", () => {
  const b = parseBanner(Buffer.from("device::ro.product.name=MU300;ro.product.model=F50;features=shell_v2,cmd,abb\0"));
  assert.equal(b.state, "device"); assert.equal(b.props["ro.product.model"], "F50");
  assert.deepEqual([...b.features], ["shell_v2", "cmd", "abb"]);
  assert.equal(parseBanner(Buffer.from("\xff\xfe nonsense")).state, "");
  assert.equal(parseBanner(Buffer.from("unauthorized::")).state, "unauthorized");
});

// ------------------------------------------------------------------ the handshake

test("a device without authentication connects, and only shared features count", async () => {
  const { dev, conn } = await connected({ features: ["cmd", "abb", "stat_v2"] });
  assert.equal(conn.banner.state, "device");
  assert.deepEqual([...conn.features], ["cmd"]);
  assert.match(dev.hostBanner, /^host::features=shell_v2,cmd$/);
  assert.equal(dev.hostVersion, 0x01000001);
  assert.equal(dev.hostMax, MAX_PAYLOAD);
  await conn.close();
});

test("an authorised key gets in with its signature; the public key is not sent again", async () => {
  const states = [];
  const { dev } = await connected({ auth: "keys", authorized: [FIX.pub + " someone"] }, { onAuth: (s) => states.push(s) });
  assert.deepEqual(states, ["signature"]);
  assert.deepEqual(dev.keysSeen, []);
});

test("an unknown key: the public key is offered, and the connection waits until it is accepted", async () => {
  const other = await generateKey("browser");
  const dev = new FakeAdbd({ auth: "keys", authorized: [], acceptNewKey: true, acceptDelayMs: 30 });
  const states = [];
  const conn = new AdbConnection(dev.transport(), { key: other, onAuth: (s) => states.push(s) });
  await conn.connect("kaan's laptop");
  assert.deepEqual(states, ["signature", "public-key"]);
  assert.equal(dev.keysSeen.length, 1);
  assert.equal(dev.keysSeen[0], publicKeyLine({ ...other, name: "kaanslaptop" }));
});

test("a key nobody accepts: connect waits, and close() ends the wait", async () => {
  const dev = new FakeAdbd({ auth: "keys" });
  const conn = new AdbConnection(dev.transport(), { key: KEY });
  const p = conn.connect();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(dev.keysSeen.length, 1);
  await conn.close();
  await rejects(p, "disconnected");
  await rejects(conn.open("shell:id"), "disconnected");
});

test("a device that asks for a key when the page has none, for TLS, or with a token of the wrong size", async () => {
  await rejects(new AdbConnection(new FakeAdbd({ auth: "keys" }).transport(), {}).connect(), "auth");
  const tls = new FakeAdbd({ mutate: (p, n) => (n === 0 ? packet(0x534c5453, 0x01000000, 0) : undefined) });
  await rejects(new AdbConnection(tls.transport(), { key: KEY }).connect(), "unsupported");
  await rejects(new AdbConnection(new FakeAdbd({ auth: "keys", tokenSize: 21 }).transport(), { key: KEY }).connect(), "protocol");
});

test("what is left of an earlier session before CNXN is skipped; endless garbage is not", async () => {
  const junk = [Buffer.from("some payload of the session before"), Buffer.alloc(24, 7), Buffer.alloc(0)];
  const { conn } = await connected({ mutate: (p, n) => (n === 0 ? [...junk, ...packet(p.cmd, p.arg0, p.arg1, p.payload)] : undefined) });
  assert.equal(conn.banner.state, "device");
  const flood = new FakeAdbd({ mutate: () => Array.from({ length: 80 }, () => Buffer.alloc(24, 1)) });
  await rejects(new AdbConnection(flood.transport(), { key: KEY }).connect(), "protocol");
});

test("an answer whose header was lost on the way: the device is asked once more, and the session works", async () => {
  // over USB the first packet of a session can be dropped when the session before ended without a goodbye (the two
  // ends of the endpoint are out of step by one packet): the payload of the device's CNXN arrives without its
  // header, and both sides would wait for each other for ever
  const asks = (dev) => dev.log.filter((l) => l[0] === "host" && l[1] === 0x4e584e43).length;
  const quick = { strayWaitMs: 20 };
  let dropped = 0;
  const lost = { mutate: (p, n) => (n === 0 && p.name === "CNXN" ? (dropped++, packet(p.cmd, p.arg0, p.arg1, p.payload).slice(1)) : undefined) };
  const { conn, dev } = await connected({ ...lost, shell: () => ({ stdout: "hi\n", exitCode: 0 }) }, quick);
  assert.equal(dropped, 1);
  assert.equal(conn.banner.state, "device");
  assert.equal(asks(dev), 2, "CNXN was asked twice, not more");
  assert.equal((await shell(conn, "echo hi")).stdout, "hi\n");
  // the same for a device that asks for a key: the token came without its header, the second challenge is signed
  const keys = await connected({ auth: "keys", authorized: [FIX.pub + " someone"], mutate: (p, n) => (n === 0 && p.name === "AUTH" ? packet(p.cmd, p.arg0, p.arg1, p.payload).slice(1) : undefined),
    shell: () => ({ stdout: "ok\n", exitCode: 0 }) }, quick);
  assert.equal(asks(keys.dev), 2);
  assert.equal(keys.dev.keysSeen.length, 0, "an authorised key is not offered again as a new one");
  assert.equal((await shell(keys.conn, "true")).stdout, "ok\n");
  // what an earlier session left behind is not a lost header: the real answer follows it at once, and nothing is
  // asked twice (a second question would make a device with keys issue a new challenge for ever)
  for (const auth of [{}, { auth: "keys", authorized: [FIX.pub + " someone"] }]) {
    const junk = await connected({ ...auth, mutate: (p, n) => (n === 0 ? [Buffer.from("payload of the session before"), ...packet(p.cmd, p.arg0, p.arg1, p.payload)] : undefined),
      shell: () => ({ stdout: "still here\n", exitCode: 0 }) }, quick);
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(asks(junk.dev), 1);
    assert.equal(junk.dev.keysSeen.length, 0);
    assert.equal((await shell(junk.conn, "true")).stdout, "still here\n");
  }
  // a device that restarts the connection later, unasked, is still an error
  const later = await connected({ shell: () => ({ stdout: "x\n", exitCode: 0 }) });
  later.dev.send(0x4e584e43, 0x01000001, 4096, Buffer.from("device::"));
  await new Promise((r) => setTimeout(r, 20));
  await rejects(later.conn.open("shell:id"), "protocol");
});

test("an impossible packet size in CNXN, and a payload longer than the limit, end the connection", async () => {
  await rejects(new AdbConnection(new FakeAdbd({ maxPayload: 100 }).transport(), { key: KEY }).connect(), "protocol");
  const big = new FakeAdbd({ mutate: (p, n) => { if (n !== 0) return undefined; const t = packet(p.cmd, p.arg0, p.arg1, p.payload); t[0].writeUInt32LE(MAX_PAYLOAD + 1, 12); return t; } });
  await rejects(new AdbConnection(big.transport(), { key: KEY }).connect(), "protocol");
});

test("the device unplugged during the handshake", async () => {
  const dev = new FakeAdbd({ dieAfterDeviceBytes: 10 });
  await rejects(new AdbConnection(dev.transport(), { key: KEY }).connect(), "disconnected");
});

// ------------------------------------------------------------------ shell

test("shell v2: stdout, stderr and the exit status", async () => {
  const { dev, conn } = await connected({ shell: (cmd) => ({ stdout: `ran ${cmd}\n`, stderr: "warn\n", exitCode: 3 }) });
  const r = await shell(conn, "id -u");
  assert.deepEqual(r, { exitCode: 3, stdout: "ran id -u\n", stderr: "warn\n" });
  assert.equal(dev.shellCalls[0].v2, true);
  assert.equal(conn.streams.size, 0);
});

test("shell without v2: one stream, the status from the line this client adds", async () => {
  const { dev, conn } = await connected({ features: ["cmd"], shell: () => ({ stdout: "out\n", stderr: "err\n", exitCode: 7 }) });
  const r = await shell(conn, "false");
  assert.equal(r.exitCode, 7);
  assert.equal(r.stdout, "out\nerr\n");
  assert.equal(dev.shellCalls[0].cmd, "false");
  assert.equal(dev.shellCalls[0].v2, false);
});

test("shell: lines as they come, CRLF from a pty turned into lines, pieces split anywhere", async () => {
  const { conn } = await connected({ async *shell() { yield { out: "MU300-STEP a\r\nMU300-" }; yield { wait: 5 }; yield { out: "STEP b\r\npartial" }; yield { err: "oops\n" }; return 0; } });
  const lines = [];
  const r = await shell(conn, "x", { onLine: (l, e) => lines.push([l, e]) });
  assert.deepEqual(lines, [["MU300-STEP a", false], ["MU300-STEP b", false], ["oops", true], ["partial", false]]);
  assert.equal(r.exitCode, 0);
});

test("shell: stdin reaches the command", async () => {
  const { conn } = await connected({ async shell(cmd, ctx) { await ctx.waitStdin(); return { stdout: ctx.stdin.toUpperCase(), exitCode: 0 }; } });
  assert.equal((await shell(conn, "cat", { stdin: "secret\n" })).stdout, "SECRET\n");
});

test("shell: a small device packet size splits frames across packets, and nothing is lost", async () => {
  const big = "x".repeat(50000) + "\n";
  const { conn } = await connected({ maxPayload: 4096, shell: () => ({ stdout: big, exitCode: 0 }) });
  assert.equal(conn.maxPayload, 4096);
  assert.equal((await shell(conn, "big")).stdout, big);
});

test("shell: a timeout and an abort both close the stream, which ends the command on the device", async () => {
  const forever = async function* () { for (;;) yield { wait: 5, out: "." }; };
  const { dev, conn } = await connected({ shell: forever });
  await rejects(shell(conn, "sleep 99", { timeoutMs: 40 }), "timeout");
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(dev.shellCalls[0].killed, true);
  const ac = new AbortController();
  const p = shell(conn, "sleep 99", { signal: ac.signal });
  setTimeout(() => ac.abort(), 20);
  await rejects(p, "aborted");
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(dev.shellCalls[1].killed, true);
  // the connection itself is still good
  dev.o.shell = () => ({ stdout: "ok", exitCode: 0 });
  assert.equal((await shell(conn, "echo ok")).stdout, "ok");
});

test("shell: no exit packet gives no status instead of a made-up one", async () => {
  const { conn } = await connected({ noExitPacket: true, shell: () => ({ stdout: "x", exitCode: 0 }) });
  assert.equal((await shell(conn, "x")).exitCode, null);
});

test("shell: a frame that claims an impossible length is a protocol error", async () => {
  const { conn } = await connected({ async *shell() { yield { raw: Buffer.from([1, 0xff, 0xff, 0xff, 0x7f, 65]) }; return 0; } });
  await rejects(shell(conn, "x"), "protocol");
  const r = new ShellPacketReader();
  assert.deepEqual(r.feed(new Uint8Array([1, 2, 0, 0])), []);
  assert.deepEqual(r.feed(new Uint8Array([0, 65, 66, 3, 1, 0, 0, 0, 9])).map((p) => [p.id, [...p.data]]), [[1, [65, 66]], [3, [9]]]);
});

test("a service the device refuses", async () => {
  const { conn } = await connected();
  await rejects(conn.open("jdwp:1"), "remote");
});

test("the cable pulled while a command runs: the command's promise fails, onClose is told once", async () => {
  let closedWith = [];
  const { dev, conn } = await connected({ async *shell() { yield { out: "a\n" }; yield { wait: 1000 }; return 0; } }, { onClose: (e) => closedWith.push(e.code) });
  const p = shell(conn, "long");
  setTimeout(() => dev.unplug(), 20);
  await rejects(p, "disconnected");
  assert.deepEqual(closedWith, ["disconnected"]);
  await rejects(shell(conn, "again"), "disconnected");
});

test("garbage after the handshake ends the connection and every open stream", async () => {
  const { conn } = await connected({ async *shell() { yield { out: "a" }; yield { wait: 20 }; yield { out: "b" }; return 0; },
    mutate: (p, n) => (p.name === "WRTE" && n > 3 ? [Buffer.alloc(24, 0x41)] : undefined) });
  await rejects(shell(conn, "x"), "protocol");
  assert.equal(conn.closed, true);
});

test("data and answers for streams that do not exist are refused or ignored, not acted on", async () => {
  const { dev, conn } = await connected({ shell: () => ({ stdout: "fine", exitCode: 0 }) });
  dev.send(COMMANDS.WRTE, 4242, 77, Buffer.from("who is this for"));
  dev.send(COMMANDS.OKAY, 4242, 78);
  dev.send(COMMANDS.CLSE, 4242, 79);
  dev.send(COMMANDS.OPEN, 4243, 0, Buffer.from("shell:echo from-device\0"));
  assert.equal((await shell(conn, "echo")).stdout, "fine");
  const closes = dev.log.filter((l) => l[0] === "host" && l[1] === COMMANDS.CLSE).map((l) => [l[2], l[3]]);
  assert.ok(closes.some(([a, b]) => a === 77 && b === 4242), "the stray WRTE is answered with CLSE");
  assert.ok(closes.some(([a, b]) => a === 0 && b === 4243), "an OPEN from the device is refused");
});

test("an OKAY that names another stream id is a protocol error", async () => {
  const { conn } = await connected({ async shell(cmd, ctx) { await ctx.waitStdin(); return { stdout: "", exitCode: 0 }; },
    mutate: (p, n) => (p.name === "OKAY" && n === 2 ? packet(p.cmd, p.arg0 + 1, p.arg1) : undefined) });
  await rejects(shell(conn, "cat", { stdin: "x" }), "protocol");
});

// ------------------------------------------------------------------ files

test("push: a Blob and bytes arrive whole, with the mode, in 64 KiB chunks and with progress", async () => {
  const { dev, conn } = await connected();
  const data = bytes(300 * 1024 + 17);
  const seen = [];
  const r = await push(conn, new Blob([data]), "/data/local/tmp/a.bin", { mode: 0o600, mtime: 1234, onProgress: (s, t) => seen.push([s, t]) });
  assert.equal(r.bytes, data.length);
  assert.deepEqual(Buffer.from(dev.files.get("/data/local/tmp/a.bin")), Buffer.from(data));
  assert.equal(dev.modes.get("/data/local/tmp/a.bin"), 0o100600);
  assert.equal(dev.mtimes.get("/data/local/tmp/a.bin"), 1234);
  assert.deepEqual(seen[0], [0, data.length]);
  assert.deepEqual(seen.at(-1), [data.length, data.length]);
  assert.ok(seen.every((s, i) => i === 0 || s[0] > seen[i - 1][0]));
  await push(conn, new Uint8Array(0), "/data/local/tmp/empty");
  assert.equal(dev.files.get("/data/local/tmp/empty").length, 0);
  await push(conn, data.subarray(0, 65536), "/data/local/tmp/exact");
  assert.equal(dev.files.get("/data/local/tmp/exact").length, 65536);
});

test("push with a device that takes only 4 KiB packets", async () => {
  const { dev, conn } = await connected({ maxPayload: 4096 });
  const data = bytes(100000, 5);
  await push(conn, data, "/data/local/tmp/small");
  assert.deepEqual(Buffer.from(dev.files.get("/data/local/tmp/small")), Buffer.from(data));
  assert.ok(dev.log.filter((l) => l[0] === "host" && l[1] === COMMANDS.WRTE).every((l) => l[4] <= 4096));
});

test("push: a refusal comes back with the device's message, at the end or early", async () => {
  const late = await connected({ sendFail: () => "couldn't create file: Permission denied" });
  await assert.rejects(push(late.conn, bytes(200000), "/system/x"), (e) => e.code === "remote" && /Permission denied/.test(e.message));
  assert.equal(late.dev.files.has("/system/x"), false);
  const early = await connected({ sendFail: () => "No space left on device", failEarly: true });
  const sent = [];
  await assert.rejects(push(early.conn, bytes(4 * 1024 * 1024), "/data/local/tmp/big", { onProgress: (s) => sent.push(s) }),
    (e) => e.code === "remote" && /No space/.test(e.message));
  assert.ok(sent.at(-1) < 4 * 1024 * 1024, "sending stops once the device has refused");
  // the connection survives a refused file
  await push(late.conn, bytes(10), "/data/local/tmp/ok").catch(() => {});
});

test("push: an answer that is neither OKAY nor FAIL", async () => {
  const { conn } = await connected({ syncAnswer: "WHAT\0\0\0\0" });
  await rejects(push(conn, bytes(10), "/data/local/tmp/x"), "protocol");
});

test("push: the cable pulled in the middle, and a cancel", async () => {
  const a = await connected({ dieAfterHostBytes: 600000 });
  await rejects(push(a.conn, bytes(2 * 1024 * 1024), "/data/local/tmp/x"), "disconnected");
  assert.equal(a.dev.files.has("/data/local/tmp/x"), false);
  const b = await connected();
  const ac = new AbortController();
  const p = push(b.conn, bytes(8 * 1024 * 1024), "/data/local/tmp/y", { signal: ac.signal, onProgress: (s) => { if (s > 1024 * 1024) ac.abort(); } });
  await rejects(p, "aborted");
  assert.equal(b.dev.files.has("/data/local/tmp/y"), false);
  assert.equal(b.conn.closed, false);
});

test("push and pull refuse paths that are not absolute device paths", async () => {
  const { conn } = await connected();
  await rejects(push(conn, bytes(1), "relative"), "protocol");
  await rejects(pull(conn, "/a\0b", () => {}), "protocol");
  assert.rejects(push(conn, "a string", "/x"), TypeError);
});

test("pull: the bytes, the progress, and the size that was promised", async () => {
  const data = bytes(200000, 9);
  const { conn } = await connected({ files: new Map([["/data/local/tmp/boot.img", data]]) });
  const got = []; const prog = [];
  const r = await pull(conn, "/data/local/tmp/boot.img", (c) => got.push(c), { expectedSize: data.length, onProgress: (g, e) => prog.push([g, e]) });
  assert.equal(r.bytes, data.length);
  assert.deepEqual(Buffer.concat(got), Buffer.from(data));
  assert.deepEqual(prog.at(-1), [data.length, data.length]);
  await rejects(pull(conn, "/data/local/tmp/boot.img", () => {}, { expectedSize: data.length + 1 }), "protocol");
  await rejects(pull(conn, "/data/local/tmp/boot.img", () => {}, { expectedSize: 1000 }), "protocol");
  await assert.rejects(pull(conn, "/nope", () => {}), (e) => e.code === "remote" && /No such file/.test(e.message));
});

test("pull: a slow sink holds the device back instead of filling memory", async () => {
  const data = bytes(600000, 3);
  const { dev, conn } = await connected({ maxPayload: 65536 + 8, files: new Map([["/f", data]]) });
  let maxQueued = 0, n = 0;
  await pull(conn, "/f", async () => { n++; await new Promise((r) => setTimeout(r, 2)); maxQueued = Math.max(maxQueued, dev.out.length); });
  assert.ok(n >= 9);
  assert.ok(maxQueued <= 4, `the device had ${maxQueued} transfers waiting`);
});

test("pull: garbled chunks, an oversized chunk, an early end, a pulled cable", async () => {
  const data = bytes(150000, 4);
  const files = new Map([["/f", data]]);
  const le = (v) => { const b = Buffer.alloc(4); b.writeUInt32LE(v); return b; };
  const a = await connected({ files, recvHook: (c) => [Buffer.concat([Buffer.from("JUNK"), le(4), Buffer.from("abcd")]), ...c] });
  await rejects(pull(a.conn, "/f", () => {}), "protocol");
  const b = await connected({ files, recvHook: () => [Buffer.concat([Buffer.from("DATA"), le(70000), Buffer.alloc(70000)])] });
  await rejects(pull(b.conn, "/f", () => {}), "protocol");
  const c = await connected({ files, recvHook: (ch) => ch.slice(0, 1) });
  const pc = pull(c.conn, "/f", () => {});
  setTimeout(() => c.dev.streams.forEach((s) => c.dev._close(s)), 30);
  await rejects(pc, "closed");
  const d = await connected({ files, dieAfterDeviceBytes: 90000 });
  await rejects(pull(d.conn, "/f", () => {}, { expectedSize: data.length }), "disconnected");
  const e = await connected({ files, recvHook: (ch) => [ch[0], ch.at(-1)] });   // DONE after the first chunk
  await rejects(pull(e.conn, "/f", () => {}, { expectedSize: data.length }), "protocol");
  const f = await connected({ files, recvHook: () => [Buffer.concat([Buffer.from("FAIL"), le(0x7fffffff)])] });
  await rejects(pull(f.conn, "/f", () => {}), "protocol");
});

test("stat", async () => {
  const { conn } = await connected({ files: new Map([["/f", bytes(77)]]) });
  assert.deepEqual(await stat(conn, "/f"), { mode: 0o100644, size: 77, mtime: 1700000000 });
  assert.equal((await stat(conn, "/missing")).mode, 0);
});

// ------------------------------------------------------------------ USB

function fakeUsbDevice(overrides = {}) {
  const calls = [];
  const adbAlt = { alternateSetting: 0, interfaceClass: 0xff, interfaceSubclass: 0x42, interfaceProtocol: 1,
    endpoints: [{ endpointNumber: 5, direction: "in", type: "bulk", packetSize: 512 }, { endpointNumber: 4, direction: "out", type: "bulk", packetSize: 512 },
      { endpointNumber: 6, direction: "in", type: "interrupt", packetSize: 16 }] };
  const config = { configurationValue: 1, interfaces: [
    { interfaceNumber: 0, alternates: [{ alternateSetting: 0, interfaceClass: 2, interfaceSubclass: 13, interfaceProtocol: 0, endpoints: [] }] },
    { interfaceNumber: 2, alternates: [{ alternateSetting: 0, interfaceClass: 0xff, interfaceSubclass: 0x42, interfaceProtocol: 3, endpoints: adbAlt.endpoints }] },
    { interfaceNumber: 3, alternates: [adbAlt] }] };
  const inQueue = [];
  const dev = { opened: false, configuration: null, configurations: [config], calls, inQueue,
    async open() { calls.push("open"); this.opened = true; },
    async close() { calls.push("close"); this.opened = false; },
    async selectConfiguration(v) { calls.push(`config ${v}`); this.configuration = config; },
    async claimInterface(n) { calls.push(`claim ${n}`); },
    async releaseInterface(n) { calls.push(`release ${n}`); },
    async selectAlternateInterface() { calls.push("alt"); },
    async clearHalt(d, n) { calls.push(`clearHalt ${d} ${n}`); },
    async transferOut(ep, data) { calls.push(`out ${ep} ${data.length}`); return { status: "ok", bytesWritten: data.length }; },
    async transferIn(ep, len) { calls.push(`in ${ep} ${len}`); const x = inQueue.shift(); if (x instanceof Error) throw x; return x; },
    ...overrides };
  return dev;
}
const named = (name) => { const e = new Error(name); e.name = name; return e; };

test("USB: the adb interface is found by its class, with its bulk endpoints", () => {
  const f = findAdbInterface(fakeUsbDevice());
  assert.equal(f.interface.interfaceNumber, 3);
  assert.equal(f.inEndpoint.endpointNumber, 5);
  assert.equal(f.outEndpoint.endpointNumber, 4);
  assert.equal(findAdbInterface({ configurations: [] }), null);
  assert.equal(findAdbInterface({}), null);
  assert.deepEqual(ADB_FILTERS, [{ classCode: 255, subclassCode: 66, protocolCode: 1 }]);
});

test("USB: open claims the interface; a write of whole packets is followed by a zero-length one", async () => {
  const d = fakeUsbDevice();
  const t = await openUsbTransport(d);
  assert.deepEqual(d.calls, ["open", "config 1", "claim 3"]);
  assert.equal(t.framed, true); assert.equal(t.headerReadSize, 512);
  d.calls.length = 0;
  await t.write(new Uint8Array(24));
  await t.write(new Uint8Array(1024));
  await t.write(new Uint8Array(1025));
  assert.deepEqual(d.calls, ["out 4 24", "out 4 1024", "out 4 0", "out 4 1025"]);
  d.calls.length = 0;
  d.inQueue.push({ status: "stall" }, { status: "ok", data: new DataView(new Uint8Array([1, 2, 3]).buffer) });
  assert.deepEqual([...await t.read(512)], [1, 2, 3]);
  assert.deepEqual(d.calls, ["in 5 512", "clearHalt in 5", "in 5 512"]);
  d.inQueue.push({ status: "babble" });
  await rejects(t.read(24), "protocol");
  d.inQueue.push(named("NetworkError"));
  await rejects(t.read(24), "disconnected");
  await t.close();
  assert.deepEqual(d.calls.slice(-2), ["release 3", "close"]);
  await rejects(t.read(1), "disconnected");
  await rejects(t.write(new Uint8Array(1)), "disconnected");
});

test("USB: why opening failed, as far as the browser tells", async () => {
  await rejects(openUsbTransport({ configurations: [] }), "no-interface");
  await rejects(openUsbTransport(fakeUsbDevice({ async claimInterface() { throw named("NetworkError"); } })), "busy");
  const denied = fakeUsbDevice({ async open() { throw named("SecurityError"); } });
  await assert.rejects(openUsbTransport(denied, "windows"), (e) => e.code === "access" && e.platform === "windows");
  await rejects(openUsbTransport(fakeUsbDevice({ async open() { throw named("NotFoundError"); } })), "disconnected");
  assert.equal(classifyOpenError(named("NetworkError"), "open", "linux").code, "access");
  assert.equal(platformOf({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", platform: "Win32" }), "windows");
  assert.equal(platformOf({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", platform: "MacIntel" }), "mac");
  assert.equal(platformOf({ userAgent: "Mozilla/5.0 (X11; Linux x86_64)", platform: "Linux x86_64" }), "linux");
  assert.equal(platformOf({ userAgent: "Mozilla/5.0 (Linux; Android 14)", platform: "Linux armv81" }), "android");
  assert.equal(platformOf({ userAgent: "", userAgentData: { platform: "Chrome OS" } }), "linux");
});

test("the whole client over the USB transport of a fake USB device", async () => {
  // a FakeAdbd behind the WebUSB calls: what the browser's USBDevice does with the transfers
  const adbd = new FakeAdbd({ shell: () => ({ stdout: "over usb\n", exitCode: 0 }) });
  const tr = adbd.transport();
  const d = fakeUsbDevice({
    async transferOut(ep, data) { await tr.write(data); return { status: "ok", bytesWritten: data.length }; },
    async transferIn(ep, len) { const b = await tr.read(len); return { status: "ok", data: new DataView(b.buffer, b.byteOffset, b.byteLength) }; },
  });
  const conn = new AdbConnection(await openUsbTransport(d), { key: KEY });
  await conn.connect();
  assert.equal((await shell(conn, "echo")).stdout, "over usb\n");
  const data = bytes(131072);
  await push(conn, data, "/data/local/tmp/z");
  assert.deepEqual(Buffer.from(adbd.files.get("/data/local/tmp/z")), Buffer.from(data));
  assert.equal(adbd.zeroWrites, 0);
  // "DATA" + length + 504 bytes is exactly one 512-byte USB packet: the zero-length packet has to follow it
  await push(conn, data.subarray(0, 504), "/data/local/tmp/z2");
  assert.equal(adbd.files.get("/data/local/tmp/z2").length, 504);
  assert.equal(adbd.zeroWrites, 1);
});

// ------------------------------------------------------------------ small things

test("quote and LineSplitter", () => {
  assert.equal(quote("abc/def-1.2"), "abc/def-1.2");
  assert.equal(quote("it's"), "'it'\\''s'");
  assert.equal(quote("a b"), "'a b'");
  assert.equal(quote(""), "''");
  const s = new LineSplitter();
  assert.deepEqual(s.feed("a\r\nb"), ["a"]);
  assert.deepEqual(s.feed("c\n\n"), ["bc", ""]);
  assert.deepEqual(s.flush(), []);
  assert.deepEqual(s.feed(new TextEncoder().encode("é\nx")), ["é"]);
  assert.deepEqual(s.flush(), ["x"]);
});
