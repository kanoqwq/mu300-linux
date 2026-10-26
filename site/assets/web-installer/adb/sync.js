// adb's file transfer service ("sync:", AOSP adb/SYNC.TXT): push, pull and stat, with progress.
//
// Every request and answer starts with a 4-letter id and a 32-bit little-endian number. A file goes as DATA chunks
// of at most 64 KiB and ends with DONE; the device answers OKAY, or FAIL with a message. This client checks every
// id and every length the device sends, and counts the bytes: a pull that ends short of what the caller expects is
// an error, never a short file handed on.
import { concat, readU32le, text, u32le, utf8 } from "./bytes.js";
import { AdbError } from "./errors.js";

const DATA_MAX = 64 * 1024;
const id = (b) => String.fromCharCode(b[0], b[1], b[2], b[3]);
const req = (name, n) => concat(utf8(name), u32le(n));

// readExact(n) over the chunks of a stream
class Reader {
  constructor(stream) { this.stream = stream; this.buf = new Uint8Array(0); }
  async exact(n) {
    while (this.buf.length < n) {
      const c = await this.stream.read();
      if (c === null) throw new AdbError("closed", "the device ended the file transfer early");
      this.buf = this.buf.length ? concat(this.buf, c) : c;
    }
    const out = this.buf.subarray(0, n);
    this.buf = this.buf.subarray(n);
    return out;
  }
}

async function failMessage(reader, len) {
  if (len > 4096) throw new AdbError("protocol", "the device sent a failure message of an impossible length");
  return text(await reader.exact(len));
}

function checkPath(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.includes("\0") || utf8(path).length > 1024) {
    throw new AdbError("protocol", `not a device path: ${path}`);
  }
}

// stat(conn, path) -> { mode, size, mtime }; a path that does not exist has mode 0
export async function stat(conn, path) {
  checkPath(path);
  const stream = await conn.open("sync:");
  try {
    const p = utf8(path);
    await stream.write(concat(req("STAT", p.length), p));
    const r = new Reader(stream);
    const h = await r.exact(16);
    if (id(h) !== "STAT") throw new AdbError("protocol", "the device did not answer the stat request");
    return { mode: readU32le(h, 4), size: readU32le(h, 8), mtime: readU32le(h, 12) };
  } finally {
    try { await stream.write(req("QUIT", 0)); } catch (e) { /* closing anyway */ }
    await stream.close();
  }
}

// A source for push: a Blob/File (read slice by slice, never whole into memory) or a Uint8Array.
function sourceOf(data) {
  if (data instanceof Uint8Array) {
    return { size: data.length, async slice(a, b) { return data.subarray(a, b); } };
  }
  if (data && typeof data.slice === "function" && typeof data.size === "number") {
    return { size: data.size, async slice(a, b) { return new Uint8Array(await data.slice(a, b).arrayBuffer()); } };
  }
  throw new TypeError("push needs a Blob, a File or a Uint8Array");
}

// push(conn, data, remotePath, options): writes the file on the device (as the adb user: /data/local/tmp on
// Android). options: mode (default 0644), mtime (seconds), onProgress(sent, total), signal.
// Rejects with "remote" and the device's message when the device refuses the file.
export async function push(conn, data, remotePath, options = {}) {
  checkPath(remotePath);
  const src = sourceOf(data);
  const mode = (options.mode === undefined ? 0o644 : options.mode) & 0o777;
  const stream = await conn.open("sync:");
  let aborted = false;
  const onAbort = () => { aborted = true; stream.close(); };
  if (options.signal) {
    if (options.signal.aborted) onAbort(); else options.signal.addEventListener("abort", onAbort, { once: true });
  }
  try {
    const spec = utf8(`${remotePath},${0o100000 | mode}`);
    await stream.write(concat(req("SEND", spec.length), spec));
    // several DATA chunks per adb packet: fewer round trips, the same bytes
    const perWrite = Math.max(1, Math.floor(conn.maxPayload / (DATA_MAX + 8)));
    let sent = 0;
    if (options.onProgress) options.onProgress(0, src.size);
    while (sent < src.size) {
      if (aborted) throw new AdbError("aborted", "cancelled");
      // the device answered before the end: it refused the file (FAIL) and only discards the rest. Stop sending.
      if (stream.chunks.length) break;
      const parts = [];
      for (let i = 0; i < perWrite && sent < src.size; i++) {
        const chunk = await src.slice(sent, Math.min(src.size, sent + DATA_MAX));
        if (!chunk.length) throw new AdbError("protocol", "the file changed while it was being sent");
        parts.push(req("DATA", chunk.length), chunk);
        sent += chunk.length;
      }
      await stream.write(concat(...parts));
      if (options.onProgress) options.onProgress(sent, src.size);
    }
    await stream.write(req("DONE", options.mtime === undefined ? Math.floor(Date.now() / 1000) : options.mtime));
    const r = new Reader(stream);
    const h = await r.exact(8);
    const what = id(h), len = readU32le(h, 4);
    if (what === "FAIL") throw new AdbError("remote", await failMessage(r, len));
    if (what !== "OKAY") throw new AdbError("protocol", "the device did not confirm the file");
    try { await stream.write(req("QUIT", 0)); } catch (e) { /* closing anyway */ }
    if (sent !== src.size) throw new AdbError("protocol", "the device confirmed a file it did not get all of");
    return { bytes: sent };
  } catch (e) {
    if (aborted) throw new AdbError("aborted", "cancelled");
    // a refusal (no such directory, no permission) comes as FAIL while the data is still going out, and the device
    // then closes the stream: the write fails with "closed" before the FAIL is read. Read it now.
    if (e instanceof AdbError && e.code === "closed") {
      const why = await pendingFail(stream);
      if (why) throw new AdbError("remote", why);
    }
    throw e;
  } finally {
    if (options.signal) options.signal.removeEventListener("abort", onAbort);
    await stream.close();
  }
}

async function pendingFail(stream) {
  try {
    const r = new Reader(stream);
    const h = await r.exact(8);
    if (id(h) !== "FAIL") return null;
    return await failMessage(r, readU32le(h, 4));
  } catch (e) { return null; }
}

// pull(conn, remotePath, sink, options): reads the file; sink(chunk) is awaited for every piece (write it to a
// file, hash it). options: onProgress(received, expected), expectedSize (the pull fails when the file is not
// exactly that long), signal. Returns { bytes }.
export async function pull(conn, remotePath, sink, options = {}) {
  checkPath(remotePath);
  const stream = await conn.open("sync:");
  let aborted = false;
  const onAbort = () => { aborted = true; stream.close(); };
  if (options.signal) {
    if (options.signal.aborted) onAbort(); else options.signal.addEventListener("abort", onAbort, { once: true });
  }
  try {
    const p = utf8(remotePath);
    await stream.write(concat(req("RECV", p.length), p));
    const r = new Reader(stream);
    let got = 0;
    const expected = options.expectedSize;
    for (;;) {
      const h = await r.exact(8);
      const what = id(h), len = readU32le(h, 4);
      if (what === "DONE") break;
      if (what === "FAIL") throw new AdbError("remote", await failMessage(r, len));
      if (what !== "DATA") throw new AdbError("protocol", "the device sent something that is not file data");
      if (len > DATA_MAX) throw new AdbError("protocol", "the device sent a file chunk of an impossible size");
      const chunk = await r.exact(len);
      got += len;
      if (expected !== undefined && got > expected) throw new AdbError("protocol", "the device sent more than the file holds");
      // a copy: the reader's buffer is reused, and the sink may keep what it gets
      await sink(chunk.slice());
      if (options.onProgress) options.onProgress(got, expected);
    }
    if (expected !== undefined && got !== expected) {
      throw new AdbError("protocol", `the file arrived short: ${got} of ${expected} bytes`);
    }
    try { await stream.write(req("QUIT", 0)); } catch (e) { /* closing anyway */ }
    return { bytes: got };
  } catch (e) {
    if (aborted) throw new AdbError("aborted", "cancelled");
    throw e;
  } finally {
    if (options.signal) options.signal.removeEventListener("abort", onAbort);
    await stream.close();
  }
}
