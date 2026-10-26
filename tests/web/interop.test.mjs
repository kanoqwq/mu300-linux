// The adb client against a real adbd over TCP. Skipped unless MU300_ADBD_TCP=host:port names one, for example
// Ubuntu 26.04's adbd package in a container (it needs no key and listens on 5555):
//   docker run --rm -d -p 127.0.0.1:15555:5555 ubuntu:26.04 sh -c 'apt-get update && apt-get install -y adbd && exec adbd'
//   MU300_ADBD_TCP=127.0.0.1:15555 node --test tests/web/interop.test.mjs
// Never point this at a device: the tests write files under /tmp of whatever answers.
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";

import { AdbConnection } from "../../site/assets/web-installer/adb/connection.js";
import { shell } from "../../site/assets/web-installer/adb/shell.js";
import { pull, push, stat } from "../../site/assets/web-installer/adb/sync.js";
import { generateKey } from "../../site/assets/web-installer/adb/key.js";
import { tcpTransport } from "./tcp-transport.mjs";

const target = process.env.MU300_ADBD_TCP;
const opts = { skip: target ? false : "MU300_ADBD_TCP is not set" };

// run(fn): fn(conn) with a connection that is closed whatever happens (an open socket keeps Node alive)
async function run(fn) {
  const [host, port] = target.split(":");
  const conn = new AdbConnection(await tcpTransport(host, Number(port)), { key: await generateKey("interop") });
  try {
    await conn.connect("interop");
    await fn(conn);
  } finally {
    await conn.close();
  }
}

test("real adbd: handshake, features, shell with exit codes and stderr", opts, () => run(async (conn) => {
  assert.equal(conn.banner.state, "device");
  assert.ok(conn.features.has("shell_v2"), [...conn.banner.features].join(","));
  const r = await shell(conn, "echo out; echo err >&2; exit 7");
  assert.deepEqual(r, { exitCode: 7, stdout: "out\n", stderr: "err\n" });
  assert.equal((await shell(conn, "cat", { stdin: "through stdin\n" })).stdout, "through stdin\n");
  const lines = [];
  await shell(conn, "for i in 1 2 3; do echo line $i; sleep 0.1; done", { onLine: (l) => lines.push(l) });
  assert.deepEqual(lines, ["line 1", "line 2", "line 3"]);
  await assert.rejects(shell(conn, "sleep 30", { timeoutMs: 300 }), (e) => e.code === "timeout");
  assert.equal((await shell(conn, "echo still here")).stdout, "still here\n");
}));

test("real adbd: push and pull, 5 MB and sizes around the chunk and USB packet boundaries, checksums equal", opts, () => run(async (conn) => {
  for (const size of [0, 1, 504, 65535, 65536, 65537, 5 * 1024 * 1024 + 3]) {
    const data = new Uint8Array(crypto.randomBytes(size));
    const path = `/tmp/mu300-interop-${size}.bin`;
    let last = -1;
    await push(conn, data, path, { mode: 0o640, onProgress: (s) => { assert.ok(s >= last); last = s; } });
    assert.equal(last, size);
    const st = await stat(conn, path);
    assert.equal(st.size, size);
    // stock adbd copies the owner's bits to group and others (FINDINGS 43): only the owner's are compared
    assert.equal(st.mode & 0o700, 0o600);
    const want = crypto.createHash("sha256").update(data).digest("hex");
    assert.equal((await shell(conn, `sha256sum ${path}`)).stdout.split(" ")[0], want);
    const h = crypto.createHash("sha256");
    const r = await pull(conn, path, (c) => h.update(c), { expectedSize: size });
    assert.equal(r.bytes, size);
    assert.equal(h.digest("hex"), want);
    await shell(conn, `rm -f ${path}`);
  }
  await assert.rejects(push(conn, new Uint8Array(10), "/proc/nope/x"), (e) => e.code === "remote");
  await assert.rejects(pull(conn, "/tmp/does-not-exist", () => {}), (e) => e.code === "remote");
  assert.equal((await stat(conn, "/tmp/does-not-exist")).mode, 0);
  assert.equal((await shell(conn, "echo after-failures")).stdout, "after-failures\n");
}));

test("real adbd: an abort in the middle of a push leaves the connection usable", opts, () => run(async (conn) => {
  const ac = new AbortController();
  const data = new Uint8Array(crypto.randomBytes(8 * 1024 * 1024));
  await assert.rejects(push(conn, data, "/tmp/mu300-interop-abort.bin", { signal: ac.signal, onProgress: (s) => { if (s > 1024 * 1024) ac.abort(); } }),
    (e) => e.code === "aborted");
  assert.equal((await shell(conn, "echo ok; rm -f /tmp/mu300-interop-abort.bin")).stdout, "ok\n");
}));
