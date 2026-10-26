// Running a command on the device: adb's shell service.
//
// With the shell_v2 feature (Android 7 and newer, and the adbd of MU300 Linux) the command runs without a pty
// ("shell,v2,raw:"), its stdout and stderr come separately and its exit status is reported. Without it the old
// "shell:" service gives one merged stream and no status, so the status is asked for with an echo at the end.
//
// Binary data never goes through here: on some devices `su` puts the command on a pty of its own, and the tty layer
// rewrites LF as CRLF (issue #2). Files go through sync.js.
import { concat, readU32le, text, u32le, utf8, LineSplitter } from "./bytes.js";
import { AdbError } from "./errors.js";

const ID_STDIN = 0, ID_STDOUT = 1, ID_STDERR = 2, ID_EXIT = 3, ID_CLOSE_STDIN = 4;
const RC_MARK = "MU300-ADB-RC=";
// how much output is kept when the caller does not take it as it comes
const KEEP = 4 * 1024 * 1024;

// The packets of the shell protocol out of the chunks of a stream: id (1 byte), length (4, little endian), data.
export class ShellPacketReader {
  constructor() { this.buf = new Uint8Array(0); }
  feed(chunk) {
    this.buf = this.buf.length ? concat(this.buf, chunk) : chunk;
    const out = [];
    while (this.buf.length >= 5) {
      const len = readU32le(this.buf, 1);
      if (len > 16 * 1024 * 1024) throw new AdbError("protocol", "the device sent a shell packet of an impossible size");
      if (this.buf.length < 5 + len) break;
      out.push({ id: this.buf[0], data: this.buf.subarray(5, 5 + len) });
      this.buf = this.buf.subarray(5 + len);
    }
    return out;
  }
}

// shell(conn, command, options) -> { exitCode, stdout, stderr }
// options:
//   onStdout(text) / onStderr(text)   called with each piece as it arrives (then stdout / stderr are not collected)
//   onLine(line, isStderr)            called for every complete line (CR before LF dropped)
//   stdin                             a string or bytes written to the command, then its stdin is closed
//   signal                            an AbortSignal: aborting closes the stream (the device ends the command: adbd
//                                     sends it SIGHUP) and rejects with code "aborted"
//   timeoutMs                         rejects with "timeout" and closes the stream
// A command that cannot be started rejects with "remote". exitCode is null when the device did not report one.
export async function shell(conn, command, options = {}) {
  const v2 = conn.features.has("shell_v2");
  const stream = await conn.open(v2 ? `shell,v2,raw:${command}` : `shell:${command}; echo ${RC_MARK}$?`);
  const out = [], err = [];
  let outLen = 0, errLen = 0;
  let exitCode = null;
  const lines = { out: new LineSplitter(), err: new LineSplitter() };
  const emit = (data, isErr) => {
    if (!data.length) return;
    const t = text(data);
    if (isErr) {
      if (options.onStderr) options.onStderr(t); else if (errLen < KEEP) { err.push(t); errLen += t.length; }
    } else if (options.onStdout) options.onStdout(t); else if (outLen < KEEP) { out.push(t); outLen += t.length; }
    if (options.onLine) for (const l of (isErr ? lines.err : lines.out).feed(t)) options.onLine(l, isErr);
  };

  let failure = null;
  const stop = (e) => { if (!failure) failure = e; stream.close(); };
  const onAbort = () => stop(new AdbError("aborted", "cancelled"));
  if (options.signal) {
    if (options.signal.aborted) onAbort(); else options.signal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = options.timeoutMs ? setTimeout(() => stop(new AdbError("timeout", `no answer to "${command.slice(0, 60)}" in time`)), options.timeoutMs) : null;

  try {
    if (options.stdin !== undefined && options.stdin !== null) {
      const data = typeof options.stdin === "string" ? utf8(options.stdin) : options.stdin;
      if (v2) {
        for (let o = 0; o < data.length; o += 32768) {
          const part = data.subarray(o, o + 32768);
          await stream.write(concat(new Uint8Array([ID_STDIN]), u32le(part.length), part));
        }
        await stream.write(concat(new Uint8Array([ID_CLOSE_STDIN]), u32le(0)));
      } else if (data.length) {
        await stream.write(data);
      }
    }
    const reader = new ShellPacketReader();
    for (;;) {
      const chunk = await stream.read();
      if (chunk === null) break;
      if (!v2) { emit(chunk, false); continue; }
      for (const p of reader.feed(chunk)) {
        if (p.id === ID_STDOUT) emit(p.data, false);
        else if (p.id === ID_STDERR) emit(p.data, true);
        else if (p.id === ID_EXIT) exitCode = p.data.length ? p.data[0] : null;
      }
    }
  } catch (e) {
    if (!failure) failure = e;
  } finally {
    if (timer) clearTimeout(timer);
    if (options.signal) options.signal.removeEventListener("abort", onAbort);
    await stream.close();
  }
  if (failure) throw failure;
  if (options.onLine) {
    for (const l of lines.out.flush()) options.onLine(l, false);
    for (const l of lines.err.flush()) options.onLine(l, true);
  }
  let stdout = out.join(""), stderr = err.join("");
  if (!v2) {
    // the status line this client asked for; when stdout was handed out piece by piece it has gone out with it
    const m = new RegExp(`${RC_MARK}(\\d+)\\r?\\n?$`).exec(stdout);
    if (m) { exitCode = Number(m[1]); stdout = stdout.slice(0, m.index); }
  }
  return { exitCode, stdout, stderr };
}

// A value as one word of a POSIX shell command line.
export function quote(s) {
  s = String(s);
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(s)) return s;
  return "'" + s.replace(/'/g, "'\\''") + "'";
}
