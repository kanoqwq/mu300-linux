// The device manager (site/assets/web-installer/manager.js and device.js's mu300-api adapter) against a scripted
// device whose shell runs the real mu300-api (rootfs/overlay/opt/mu300/bin) on a fake root with stub tools, as
// tests/test_api.py does: the page's method list is the device's, a request travels on stdin only, a secret is
// never in a command line or the log, a refusal is a DeviceError with the device's code, a job is followed to its
// end, and a release without mu300-api is told apart.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { AdbConnection } from "../../site/assets/web-installer/adb/connection.js";
import { API_METHODS, DeviceError, LinuxDevice } from "../../site/assets/web-installer/device.js";
import { Manager, consequence, diagnostics, isJobMethod, normalizeSms } from "../../site/assets/web-installer/manager.js";
import { Masker } from "../../site/assets/web-installer/privacy.js";
import { FakeAdbd } from "./fake-adbd.mjs";

const TOP = path.resolve(new URL("../..", import.meta.url).pathname);
const API = path.join(TOP, "rootfs/overlay/opt/mu300/bin/mu300-api");
const FIX = JSON.parse(fs.readFileSync(new URL("./fixtures/adbkey.json", import.meta.url)));
const KEY = { n: FIX.n, d: FIX.d, e: FIX.e, name: "test" };
const SH = fs.existsSync("/bin/dash") ? "/bin/dash" : "/bin/sh";

function fakeRoot() {
  const t = fs.mkdtempSync(path.join(os.tmpdir(), "mu300-mgr-"));
  const mk = (p, text, mode = 0o644) => { fs.mkdirSync(path.dirname(path.join(t, p)), { recursive: true }); fs.writeFileSync(path.join(t, p), text, { mode }); };
  mk("root/etc/os-release", 'ID=ubuntu\nVERSION_ID="24.04"\n');
  mk("root/etc/mu300/hotspot.conf", "SSID=MU300-1\nPSK=oldsecret99\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n", 0o600);
  mk("root/etc/mu300/image-version", "v2026.10.15\n");
  mk("root/proc/uptime", "100.0 1.0\n");
  mk("disk/boot/kernel", "6.18\n");
  const rec = '#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$STUBLOG/calls"\n';
  mk("bin/mu300-device", "#!/bin/sh\necho f50\n", 0o755);
  mk("bin/mu300-lan-ip", "#!/bin/sh\necho 192.168.77.1\n", 0o755);
  mk("bin/mu300-update", rec + 'echo "==> installed: v2026.10.15"\necho "==> newest release: v2026.11.01"\nexit 0\n', 0o755);
  for (const s of ["systemctl", "iw", "ip", "hostnamectl"]) mk("stubs/" + s, rec, 0o755);
  // the job runs (in the background, as on the device); what is started for later is only recorded
  mk("stubs/setsid", '#!/bin/sh\nif [ "${4:-}" = mu300-api-job ]; then "$@" & exit 0; fi\nprintf "later %s\\n" "$*" >> "$STUBLOG/calls"\n', 0o755);
  const env = { ...process.env, PATH: `${t}/stubs:${process.env.PATH}`, STUBLOG: t, MU300_SYSROOT: `${t}/root`, MU300_BIN: `${t}/bin`,
    MU300_LIBDIR: path.join(TOP, "rootfs/overlay/opt/mu300/lib"), MU300_DISK: `${t}/disk`, MU300_API_RUN: `${t}/run`, MU300_API_NOROOT: "1",
    MU300_DASH_RPC: `${t}/none`, LC_ALL: "C.UTF-8" };
  return { t, env, read: (p) => fs.readFileSync(path.join(t, p), "utf8") };
}

// a device whose shell runs mu300-api for its two command shapes, and nothing else
async function device(fake, options = {}) {
  const dev = new FakeAdbd({
    shell: async (cmd, ctx) => {
      await ctx.waitStdin();
      if (options.old) return { stdout: "MU300-API-NONE\n", exitCode: 0 };
      let m = /^\[ -x \/opt\/mu300\/bin\/mu300-api \] && \/opt\/mu300\/bin\/mu300-api hello \|\| echo MU300-API-NONE$/.exec(cmd);
      const method = m ? "hello" : (/^\/opt\/mu300\/bin\/mu300-api ([a-z0-9._]+)$/.exec(cmd) || [])[1];
      if (!method) return { stdout: "", stderr: "unexpected command\n", exitCode: 127 };
      const r = spawnSync(SH, [API, method], { input: ctx.stdin, env: fake.env, encoding: "utf8", timeout: 30000 });
      return { stdout: r.stdout, stderr: r.stderr, exitCode: r.status ?? 1 };
    },
  });
  const conn = new AdbConnection(dev.transport(), { key: KEY });
  await conn.connect();
  const log = [];
  const masker = new Masker();
  const linux = new LinuxDevice(conn, { log: (l) => log.push(l), mask: (x) => masker.mask(x) });
  return { dev, conn, linux, log, masker };
}

test("the page's methods are mu300-api's, and a release without it is told apart", async () => {
  const fake = fakeRoot();
  const listed = spawnSync(SH, [API, "--methods"], { env: fake.env, encoding: "utf8" }).stdout.split("\n").filter(Boolean);
  assert.deepEqual([...API_METHODS].sort(), listed.sort());
  const { linux } = await device(fake);
  const h = await linux.hello();
  assert.equal(h.api, 1);
  assert.equal(h.system, "ubuntu");
  const old = await device(fake, { old: true });
  assert.equal(await old.linux.hello(), null);
});

test("a request goes on stdin only; a secret is in no command line and no log line", async () => {
  const fake = fakeRoot();
  const { dev, linux, log, masker } = await device(fake);
  const h = await linux.hello();
  const changes = [];
  const M = new Manager(linux, h, { log: (l) => log.push(l), masker, onChange: () => changes.push(1) });
  const secret = "n3w $(reboot) 'pass\"word";
  M.secret(secret);
  const o = await M.act("wifi.set", { ssid: "Café; rm -rf /", password: secret });
  assert.equal(o.ok, 1);
  assert.match(fake.read("root/etc/mu300/hotspot.conf"), /^PSK=n3w \$\(reboot\) 'pass"word$/m);
  assert.match(fake.read("root/etc/mu300/hotspot.conf"), /^SSID=Café; rm -rf \/$/m);
  for (const c of dev.shellCalls) assert.ok(!c.cmd.includes("n3w") && !c.cmd.includes("Café"), c.cmd);
  assert.ok(!log.join("\n").includes("n3w"));
  await M.load("wifi");
  assert.equal(M.data.wifi.password_set, true);
  assert.ok(!JSON.stringify(M.data).includes("n3w") && !JSON.stringify(M.data).includes("oldsecret99"));
});

test("a refusal is the device's code; a method off the page's list is never sent", async () => {
  const fake = fakeRoot();
  const { dev, linux, log } = await device(fake);
  await linux.hello();
  await assert.rejects(linux.api("wifi.set", { password: "short" }), (e) => e instanceof DeviceError && e.code === "api-bad-value" && e.detail === "password");
  await assert.rejects(linux.api("wifi.set", { ssid: "x", extra: 1 }), (e) => e.code === "api-unknown-field");
  assert.ok(log.some((l) => /mu300-api wifi\.set: bad-value \(password\)/.test(l)));
  const before = dev.shellCalls.length;
  await assert.rejects(linux.api("wifi.get;reboot", {}), (e) => e.code === "bad-argument");
  await assert.rejects(linux.api("nope", {}), (e) => e.code === "bad-argument");
  await assert.rejects(linux.api("wifi.get", ["x"]), (e) => e.code === "bad-argument");
  assert.equal(dev.shellCalls.length, before);
});

test("a job is followed to its end from the device's own record, and outlives the call that started it", async () => {
  const fake = fakeRoot();
  const { linux, log, masker } = await device(fake);
  const h = await linux.hello();
  const M = new Manager(linux, h, { log: (l) => log.push(l), masker, pollMs: 100 });
  assert.ok(isJobMethod("update.check") && !isJobMethod("wifi.set"));
  const o = await M.act("update.check", {});
  assert.equal(o.job, "update-check");
  const j = await M.follow();
  assert.equal(j.state, "done");
  assert.equal(j.rc, 0);
  assert.ok(j.lines.includes("==> newest release: v2026.11.01"), j.lines.join("|"));
  assert.ok(log.includes("==> installed: v2026.10.15"));
  // a page that comes later finds it in hello and shows it once
  const again = await device(fake);
  const h2 = await again.linux.hello();
  assert.deepEqual(h2.job, { kind: "update-check", state: "done" });
  const M2 = new Manager(again.linux, h2, { pollMs: 100 });
  assert.equal((await M2.attach()).lines.length, 2);
  await M2.clearJob();
  assert.equal((await again.linux.hello()).job.state, "none");
});

test("what an action does to the page's own connection", () => {
  assert.equal(consequence("usb.role", { role: "host" }).leaves, true);
  assert.equal(consequence("usb.role", { role: "device" }), null);
  assert.equal(consequence("adb.auth", { mode: "keys" }).key, "mgr.confirm.adb-keys");
  assert.equal(consequence("system.reboot", {}).leaves, true);
  assert.equal(consequence("lan.set", { ip: "10.0.0.1" }).args[0], "10.0.0.1");
  assert.ok(!consequence("lan.set", { ip: "10.0.0.1" }).leaves);
  assert.equal(consequence("lan.set", { ip: "10.0.0.1" }, { system: "openwrt-luci" }).leaves, true);
  assert.equal(consequence("lan.set", { ip: "10.0.0.1" }, { system: "openwrt" }).key, "mgr.confirm.lan-usb");
  assert.ok(!consequence("lan.set", { ip: "10.0.0.1" }, { system: "ubuntu" }).leaves);
  assert.equal(consequence("data.power", { state: "off" }).key, "mgr.confirm.data-off-only");
  // data roaming alone restarts nothing: its own sentence; with an APN change it is the restart's
  assert.equal(consequence("data.set", { roaming: false }).key, "mgr.confirm.roaming-off");
  assert.equal(consequence("data.set", { roaming: true }).key, "mgr.confirm.roaming-on");
  assert.equal(consequence("data.set", { roaming: true, apn: "x" }).key, "mgr.confirm.data-set");
  assert.equal(consequence("data.power", { state: "off" }, { wifiUplink: true }).key, "mgr.confirm.data-off");
  assert.equal(consequence("vpn.killswitch", { value: "on" }).key, "mgr.confirm.killswitch");
  assert.equal(consequence("wifi.get", {}), null);
});

test("SMS of both kinds, and a diagnostics file without names, addresses or messages", () => {
  const a = normalizeSms({ store: "SM", messages: [{ index: [3, 4], sender: "+90555", time: "t", parts: 2, have: 2, text: "hi" }] });
  assert.deepEqual(a[0], { id: "3,4", from: "+90555", time: "t", text: "hi", complete: true, pool: false });
  const b = normalizeSms({ msgs: [{ id: "7", peer: "x", status: "unread", dir: "in", time: "t", preview: "p" }, { id: "../x" }] });
  assert.equal(b.length, 1);
  assert.equal(b[0].unread, true);
  assert.deepEqual(normalizeSms("garbage"), []);
  const m = new Masker();
  const d = diagnostics({ hello: { device: "f50" }, wifi: { ssid: "HomeNet", clients: [{ mac: "aa:bb:cc:dd:ee:ff" }] }, data: { user: "me", address: "10.1.2.3" } },
    "wlan0: aa:bb:cc:dd:ee:ff +905551112233", m);
  assert.ok(!d.includes("HomeNet") && !d.includes("10.1.2.3") && !d.includes('"me"'));
  assert.ok(!d.includes("dd:ee:ff") && !d.includes("5551112233"));
  assert.match(d, /\[1 clients\]/);
});
