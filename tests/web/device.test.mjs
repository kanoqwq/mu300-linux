// site/assets/web-installer/device.js against a scripted device: what kind of device a connection is, Android
// through android-web.sh's marked lines (answers that are not what was asked for are errors, never guesses), a
// backup that is checked while it is pulled, and the adapter around MU300 Linux.
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";

import { AdbConnection } from "../../site/assets/web-installer/adb/connection.js";
import { AndroidDevice, DEVICE_FILES, DeviceError, LinuxDevice, STAGE, identify, lockState, memorySink, normalizeLinuxInfo } from "../../site/assets/web-installer/device.js";
import { Masker, mask, pageMasker } from "../../site/assets/web-installer/privacy.js";
import { FakeAdbd } from "./fake-adbd.mjs";

const FIX = JSON.parse(fs.readFileSync(new URL("./fixtures/adbkey.json", import.meta.url)));
const KEY = { n: FIX.n, d: FIX.d, e: FIX.e, name: "test" };
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const code = (c) => (e) => { assert.ok(e instanceof DeviceError, String(e && e.stack)); assert.equal(e.code, c); return true; };

async function device(shellFn, options = {}) {
  const dev = new FakeAdbd({ shell: shellFn, ...options });
  const conn = new AdbConnection(dev.transport(), { key: KEY });
  await conn.connect();
  return { dev, conn };
}
// a device whose android-web.sh answers are scripted: answers[command] = text | (args, ctx) => text
function android(answers, options = {}) {
  const calls = [];
  return device(async (cmd, ctx) => {
    const m = /^su -c 'sh \/data\/local\/tmp\/mu300-web\/android-web\.sh (.*)'$/.exec(cmd);
    if (!m) return answers.raw ? answers.raw(cmd, ctx) : { stdout: "", exitCode: 0 };
    const args = m[1].split(" ");
    calls.push(args);
    if (args[0] === "password") await ctx.waitStdin();
    const a = answers[args[0]];
    const out = typeof a === "function" ? await a(args.slice(1), ctx) : a;
    if (out && typeof out === "object") return out;
    return { stdout: out ?? "MU300-WEB-ERROR bad-command\n", exitCode: /MU300-WEB-ERROR/.test(out ?? "x") ? 1 : 0 };
  }, options).then((x) => ({ ...x, calls, a: new AndroidDevice(x.conn) }));
}
const STATUS = { v: 1, device: "f50", region: { off: 1, size: 2, existing: "no", dirty: 0 }, parts: [], sd: { dev: "" }, job: { state: "none" } };

test("identify: Android, MU300 Linux, something else", async () => {
  for (const kind of ["android", "linux", "other"]) {
    const { conn } = await device((cmd) => ({ stdout: `KIND=${kind}\n`, exitCode: 0 }));
    assert.deepEqual(await identify(conn), { kind });
  }
  const { conn } = await device(() => ({ stdout: "garbage", exitCode: 0 }));
  assert.deepEqual(await identify(conn), { kind: "other" });
});

test("probe: the properties, the bootloader's word and whether there is a su, without root", async () => {
  const { conn, dev } = await device(() => ({ stdout: "ro.product.model=ZTE F50\r\nro.product.device=mu300\r\nro.boot.slot_suffix=_a\r\nro.boot.verifiedbootstate=orange\r\n" +
    "ro.boot.flash.locked=\r\nsys.boot_completed=1\r\nSU=1\r\n", exitCode: 0 }));
  const p = await new AndroidDevice(conn).probe();
  assert.equal(p.props["ro.product.model"], "ZTE F50");
  assert.equal(p.lock, "unlocked");
  assert.equal(p.su, true);
  assert.ok(!dev.shellCalls[0].cmd.includes("su -c"), "nothing in the probe runs as root");
  for (const never of ["serialno", "imei", "iccid", "imsi", "gsm.", "persist.radio", "ril."]) assert.ok(!dev.shellCalls[0].cmd.toLowerCase().includes(never), never);
});

test("the bootloader's lock state from what it told Android; nothing told is unknown", () => {
  assert.equal(lockState({ "ro.boot.vbmeta.device_state": "unlocked" }), "unlocked");
  assert.equal(lockState({ "ro.boot.flash.locked": "0" }), "unlocked");
  assert.equal(lockState({ "ro.boot.verifiedbootstate": "orange" }), "unlocked");
  assert.equal(lockState({ "ro.boot.vbmeta.device_state": "locked" }), "locked");
  assert.equal(lockState({ "ro.boot.flash.locked": "1", "ro.boot.verifiedbootstate": "green" }), "locked");
  assert.equal(lockState({ "ro.boot.verifiedbootstate": "yellow" }), "locked");
  assert.equal(lockState({}), "unknown");
  assert.equal(lockState({ "ro.boot.verifiedbootstate": "", "ro.boot.flash.locked": "" }), "unknown");
});

test("root: granted, denied, no su, and a prompt nobody answers", async () => {
  const ask = async (stdout, exitCode) => new AndroidDevice((await device(() => ({ stdout, exitCode }))).conn).root();
  assert.equal(await ask("0\n", 0), "granted");
  assert.equal(await ask("Permission denied\n", 13), "denied");
  assert.equal(await ask("/system/bin/sh: su: inaccessible or not found\n", 127), "no-su");
  assert.equal(await ask("2000\n", 0), "denied");
  const { conn } = await device(async function* () { yield { wait: 500 }; return 0; });
  assert.equal(await new AndroidDevice(conn).root(50), "denied");
});

test("stage: the device scripts are checked against the site's checksums, then pushed", async () => {
  const files = Object.fromEntries(DEVICE_FILES.map((n) => [n, Buffer.from(`#!/bin/sh\n# ${n}\n`)]));
  const sums = Object.entries(files).map(([n, d]) => `${sha(d)}  ${n}\n`).join("");
  const fetchFile = (bad) => async (name) => (name === "SHA256SUMS" ? Buffer.from(sums) : name === bad ? Buffer.from("tampered") : files[name]);
  const { a, dev } = await android({ raw: () => ({ stdout: "STAGE-OK\n", exitCode: 0 }) });
  await a.stage(fetchFile());
  for (const n of DEVICE_FILES) assert.deepEqual(Buffer.from(dev.files.get(`${STAGE}/${n}`)), files[n]);
  assert.equal(a.staged, true);
  const b = await android({ raw: () => ({ stdout: "STAGE-OK\n", exitCode: 0 }) });
  await assert.rejects(b.a.stage(fetchFile("storage.sh")), code("site-files"));
  assert.equal(b.dev.files.size, 0, "nothing is pushed when one file does not match");
  const c = await android({ raw: () => ({ stdout: "mkdir: Permission denied\n", exitCode: 1 }) });
  await assert.rejects(c.a.stage(fetchFile()), code("no-stage"));
});

test("status: JSON on a marked line, with CRLF from a pty; anything else is an error", async () => {
  const ok = await android({ status: `some text su printed\r\nMU300-WEB-STATUS ${JSON.stringify(STATUS)}\r\n` });
  assert.deepEqual(await ok.a.status(), STATUS);
  assert.deepEqual(ok.calls, [["status"]]);
  await ok.a.status(true);
  assert.deepEqual(ok.calls[1], ["status", "deep"]);
  for (const [answer, want] of [["", "no-answer"], ["MU300-WEB-STATUS {not json\n", "bad-answer"], ["MU300-WEB-STATUS []\n", "bad-answer"],
    [`MU300-WEB-STATUS ${JSON.stringify({ ...STATUS, v: 2 })}\n`, "bad-answer"], [`MU300-WEB-STATUS ${JSON.stringify({ ...STATUS, parts: "x" })}\n`, "bad-answer"],
    ["MU300-WEB-ERROR no-busybox\n", "no-busybox"], ["MU300-WEB-ERROR not-root\n", "not-root"]]) {
    await assert.rejects((await android({ status: answer })).a.status(), code(want), answer);
  }
});

test("arguments that are not plain words never reach the device's shell", async () => {
  const { a, calls } = await android({ status: "x" });
  for (const bad of ["a b", "x;reboot", "$(id)", "it's", "", "a\nb", "`id`"]) await assert.rejects(a.web(["take", bad]), code("bad-argument"));
  assert.deepEqual(calls, []);
});

test("a zip: pushed, then checked by the device; the device's refusal is the answer", async () => {
  const data = new Uint8Array(crypto.randomBytes(300000));
  const digest = sha(data);
  const name = "mu300-magisk-v2026.11.01-openwrt-k6.18.zip";
  const good = await android({ take: ([n, h]) => `MU300-WEB-ZIP ${JSON.stringify({ name: n, web: 1, tag: "v2026.11.01", system: "openwrt", os: "openwrt", ubuntu: "", kernel: "6.18", sha: h })}\n` });
  const prog = [];
  const z = await good.a.takeZip(new Blob([data]), name, digest, { onProgress: (s, t) => prog.push([s, t]) });
  assert.equal(z.tag, "v2026.11.01");
  assert.equal(z.sha, digest);
  assert.deepEqual(Buffer.from(good.dev.files.get(`${STAGE}/in/${name}`)), Buffer.from(data));
  assert.deepEqual(prog.at(-1), [data.length, data.length]);
  const bad = await android({ take: () => "MU300-WEB-ERROR sha-mismatch " + "0".repeat(64) + "\n" });
  await assert.rejects(bad.a.takeZip(data, name, digest), (e) => e.code === "sha-mismatch" && e.detail === "0".repeat(64));
  await assert.rejects((await android({ take: () => "done\n" })).a.takeZip(data, name, digest), code("no-answer"));
});

test("answers go as a file, the password on stdin of a root command and nowhere else", async () => {
  let stdinSeen = null;
  const { a, dev, calls } = await android({ answers: "MU300-WEB-ANSWERS ok\n", password: (args, ctx) => { stdinSeen = ctx.stdin; return "MU300-WEB-PASSWORD ok\n"; } });
  const logged = [];
  a.log = (l) => logged.push(l);
  await a.setAnswers("MU300_BOOT=linux\n", "correct horse");
  assert.equal(Buffer.from(dev.files.get(`${STAGE}/in/answers.conf`)).toString(), "MU300_BOOT=linux\n");
  assert.equal(stdinSeen, "correct horse\n");
  assert.deepEqual(calls, [["answers"], ["password"]]);
  assert.ok(!dev.shellCalls.some((c) => c.cmd.includes("correct horse")), "the password is in no command line");
  assert.ok(!logged.join("\n").includes("correct horse"));
  const none = await android({ answers: "MU300-WEB-ANSWERS ok\n" });
  await none.a.setAnswers("x=1\n", "");
  assert.deepEqual(none.calls, [["answers"]]);
});

test("follow: the job's lines as events, its exit status as the result; a lost connection rejects", async () => {
  const log = "MU300 Linux v2026.11.01\nMU300-STEP 1/9 check\n- device: ZTE F50\nMU300-PLAN kernel=6.18\n! something refused\n\nMU300-WEB-EXIT 1\n";
  const { a } = await android({ follow: log });
  const events = [];
  assert.equal(await a.follow((e) => events.push(e)), 1);
  assert.deepEqual(events.map((e) => e.type), ["text", "step", "text", "plan", "error", "text"]);
  assert.equal(await (await android({ follow: "MU300-WEB-EXIT interrupted\n" })).a.follow(() => {}), "interrupted");
  await assert.rejects((await android({ follow: "no exit line\n" })).a.follow(() => {}), code("no-answer"));
  const cut = await android({ follow: async function* () { yield { out: "MU300-STEP 6/9 systems\n" }; yield { wait: 2000 }; return 0; } });
  const seen = [];
  const p = cut.a.follow((e) => seen.push(e));
  setTimeout(() => cut.dev.unplug(), 30);
  await assert.rejects(p, (e) => e.code === "disconnected");
  assert.deepEqual(seen, [{ type: "step", n: 6, of: 9, name: "systems" }]);
});

// what "android-web.sh cat NAME" prints for the copies of a backup: base64, in lines, as busybox does
const b64 = (buf) => Buffer.from(buf).toString("base64").replace(/(.{76})/g, "$1\n") + "\n";
const cats = (files) => ([name]) => (files[name] ? b64(files[name]) : "MU300-WEB-ERROR no-file\n");

test("backup: every file read out of root's directory, hashed while it comes, compared with what the device said", async () => {
  const boot = new Uint8Array(crypto.randomBytes(200000)), misc = new Uint8Array(crypto.randomBytes(4096));
  const files = { "boot_b.img": boot, "misc-head.bin": misc };
  const list = `MU300-WEB-FILE boot_b.img ${boot.length} ${sha(boot)}\nMU300-WEB-FILE misc-head.bin ${misc.length} ${sha(misc)}\nMU300-WEB-BACKUP android_slot=a linux_slot=b\n`;
  const { a, calls, dev } = await android({ backup: list, cat: cats(files), clean: "MU300-WEB-CLEAN ok\n" });
  const store = new Map();
  const prog = [];
  const r = await a.backup(memorySink(store), (done, total, name) => prog.push([done, total, name]));
  assert.deepEqual(r, { files: [{ name: "boot_b.img", size: boot.length, sha256: sha(boot) }, { name: "misc-head.bin", size: 4096, sha256: sha(misc) }], androidSlot: "a", linuxSlot: "b" });
  assert.deepEqual(Buffer.from(await store.get("boot_b.img").arrayBuffer()), Buffer.from(boot));
  assert.deepEqual(prog.at(-1), [boot.length + 4096, boot.length + 4096, "misc-head.bin"]);
  assert.deepEqual(calls, [["backup"], ["cat", "boot_b.img"], ["cat", "misc-head.bin"], ["clean"]]);
  // root cleans root's directory; the page's own directory is cleaned without root
  const last = dev.shellCalls.at(-1).cmd;
  assert.ok(last.startsWith("rm -rf /data/local/tmp/mu300-web/in") && !last.includes("su "), last);
  // a terminal in the way (CR before LF) changes nothing
  const cr = await android({ backup: list, cat: ([n]) => b64(files[n]).replace(/\n/g, "\r\n"), clean: "MU300-WEB-CLEAN ok\n" });
  const store1 = new Map();
  await cr.a.backup(memorySink(store1));
  assert.equal(sha(Buffer.from(await store1.get("boot_b.img").arrayBuffer())), sha(boot));

  // the device says one checksum and sends other bytes: no file is kept, and the copies on the device still go
  const lie = await android({ backup: list.replace(sha(boot), "f".repeat(64)), cat: cats(files), clean: "MU300-WEB-CLEAN ok\n" });
  const store2 = new Map();
  await assert.rejects(lie.a.backup(memorySink(store2)), code("backup-mismatch"));
  assert.equal(store2.size, 0);
  assert.deepEqual(lie.calls.map((c) => c[0]), ["backup", "cat", "clean"]);
  // a size that is not the file's: more than was said, less than was said
  for (const d of [1, -1]) {
    const off = await android({ backup: list.replace(` ${boot.length} `, ` ${boot.length + d} `), cat: cats(files), clean: "MU300-WEB-CLEAN ok\n" });
    const st = new Map();
    await assert.rejects(off.a.backup(memorySink(st)), code("bad-answer"));
    assert.equal(st.size, 0);
  }
  // what is not base64 is not a file: the script's refusal, a shell's complaint, a text cut in the middle
  for (const answer of ["MU300-WEB-ERROR no-file\n", "sh: base64: not found\n", b64(boot).slice(0, 1001), b64(boot) + "!!\n"]) {
    const x = await android({ backup: list, cat: () => answer, clean: "MU300-WEB-CLEAN ok\n" });
    const st = new Map();
    await assert.rejects(x.a.backup(memorySink(st)), (e) => e instanceof DeviceError && ["no-file", "bad-answer"].includes(e.code), answer.slice(0, 30));
    assert.equal(st.size, 0);
  }
  // names that are not file names, and an empty list
  for (const bad of ["MU300-WEB-FILE ../x 1 " + "a".repeat(64) + "\nMU300-WEB-BACKUP android_slot=a linux_slot=b\n", "MU300-WEB-FILE a b c\nMU300-WEB-BACKUP x\n"]) {
    await assert.rejects((await android({ backup: bad, clean: "" })).a.backup(memorySink(new Map())), code("bad-answer"));
  }
  await assert.rejects((await android({ backup: "MU300-WEB-BACKUP android_slot=a linux_slot=b\n", clean: "" })).a.backup(memorySink(new Map())), code("no-answer"));
});

test("boot-linux ends with the device going away, and that is not an error", async () => {
  const { a, dev } = await android({ "boot-linux": async function* () { yield { out: "slot b armed for one boot. Rebooting into Linux.\n" }; setTimeout(() => dev.unplug(), 10); yield { wait: 500 }; return 0; } });
  assert.deepEqual(await a.bootLinux(), []);
  const no = await android({ "boot-linux": "MU300-WEB-ERROR no-linux\n" });
  await assert.rejects(no.a.bootLinux(), code("no-linux"));
});

test("reboot uses adb's own service", async () => {
  const { a, dev } = await android({});
  await a.reboot();
  assert.equal(dev.rebooted, "");
});

// ------------------------------------------------------------------ MU300 Linux

const FACTS = "device=u30air\nversion=v2026.10.23\nos=ubuntu 24.04\nkernel=7.2.10-mu300\nkernel_choice=7.2\nboot_os=ubuntu\ndefault_boot=linux\nattempts=3\nsystems=ubuntu openwrt-luci \nuptime=512\nroot=0\n";

test("Linux: an image without mu300-device --json is read through the commands every image has", async () => {
  const { conn, dev } = await device((cmd) => (cmd.includes("mu300-device --json") ? { stdout: "f50\n", exitCode: 0 } : { stdout: FACTS, exitCode: 0 }));
  const info = await new LinuxDevice(conn).info();
  assert.deepEqual(info, { source: "commands", device: "u30air", version: "v2026.10.23", os: "ubuntu 24.04", kernel: "7.2.10-mu300", kernelChoice: "7.2",
    bootOs: "ubuntu", defaultBoot: "linux", attempts: 3, systems: ["ubuntu", "openwrt-luci"], uptime: 512, isRoot: true, adb: "" });
  assert.equal(dev.shellCalls.length, 2);
  for (const c of dev.shellCalls) for (const never of ["imei", "iccid", "imsi", "mu300-sim", "mu300-at", "sms", "serial"]) assert.ok(!c.cmd.toLowerCase().includes(never), never);
});

test("Linux: mu300-device --json (docs/ADB-INTERFACE.md, format 1) says who it is; the rest is still read, and every value is checked", async () => {
  const json = { mu300: 1, device: "f50", system: "openwrt-luci", version_id: "25.12.5", release: "v2026.11.01", kernel: "7.2.10", root: true, adb: "open", imei: "867530012345678" };
  const { conn } = await device((cmd) => (cmd.includes("mu300-device --json") ? { stdout: JSON.stringify(json) + "\n", exitCode: 0 } : { stdout: FACTS, exitCode: 0 }));
  const info = await new LinuxDevice(conn).info();
  assert.deepEqual(info, { source: "json", device: "f50", version: "v2026.11.01", os: "openwrt-luci 25.12.5", kernel: "7.2.10", kernelChoice: "7.2", bootOs: "ubuntu",
    defaultBoot: "linux", attempts: 3, systems: ["ubuntu", "openwrt-luci"], uptime: 512, isRoot: true, adb: "open" });
  assert.ok(!("imei" in info));
  // another format number, or nonsense, is not taken for format 1
  for (const other of [{ ...json, mu300: 2 }, { device: "f50" }, [1], "x"]) {
    const x = await device((cmd) => (cmd.includes("mu300-device --json") ? { stdout: JSON.stringify(other) + "\n", exitCode: 0 } : { stdout: FACTS, exitCode: 0 }));
    assert.equal((await new LinuxDevice(x.conn).info()).source, "commands");
  }
  const hostile = await device((cmd) => (cmd.includes("mu300-device --json")
    ? { stdout: JSON.stringify({ mu300: 1, device: "x; rm -rf /", system: "<script>", release: "a\u0000b", kernel: "$(reboot)", root: "yes", adb: "wide-open" }), exitCode: 0 } : { stdout: "", exitCode: 0 }));
  const h = await new LinuxDevice(hostile.conn).info();
  assert.deepEqual([h.device, h.os, h.version, h.kernel, h.isRoot, h.adb], ["", "", "", "", false, ""]);
  assert.equal(normalizeLinuxInfo({ device: "x; rm -rf /", version: "a\u0000b", systems: "ubuntu;reboot" }, "json").device, "");
  // the three devices (the ZTE V50: FINDINGS 95), and nothing else
  for (const d of ["f50", "u30air", "v50"]) assert.equal(normalizeLinuxInfo({ device: d }, "json").device, d);
  assert.equal(normalizeLinuxInfo({ device: "v500" }, "json").device, "");
  assert.deepEqual(normalizeLinuxInfo({ version: "v1 <b>" }, "json").version, "");
});

test("Linux: the actions are the device's own commands, with nothing but known words as arguments", async () => {
  const { conn, dev } = await device((cmd) => ({ stdout: "ok\n", exitCode: cmd.includes("next-boot") ? 0 : 4 }));
  const l = new LinuxDevice(conn);
  const lines = [];
  assert.equal((await l.checkUpdate((x) => lines.push(x))).rc, 4);
  assert.deepEqual(lines, ["ok"]);
  await l.setKernel("6.18");
  await l.setBootOs("openwrt-luci");
  await l.nextBoot("android");
  const cmds = dev.shellCalls.map((c) => c.cmd.split("; ").pop());
  assert.deepEqual(cmds, ["mu300-update check", "mu300-update kernel 6.18", "mu300-os openwrt-luci", "mu300-next-boot android"]);
  assert.throws(() => l.setKernel("6.18; reboot"), code("bad-argument"));
  assert.throws(() => l.setBootOs("arch"), code("bad-argument"));
  assert.throws(() => l.nextBoot("recovery"), code("bad-argument"));
});

test("Linux: back to Android is next-boot android and then a restart, and only when the first worked", async () => {
  const ok = await device((cmd) => { if (cmd.includes("reboot")) setTimeout(() => ok.dev.unplug(), 5); return { stdout: "", exitCode: 0 }; });
  await new LinuxDevice(ok.conn).toAndroid();
  assert.deepEqual(ok.dev.shellCalls.map((c) => c.cmd.split("; ").slice(1).join("; ")), ["mu300-next-boot android", "sync; reboot"]);
  const bad = await device(() => ({ stdout: "cannot write misc\n", exitCode: 1 }));
  const r = await new LinuxDevice(bad.conn).toAndroid();
  assert.equal(r.rc, 1);
  assert.equal(bad.dev.shellCalls.length, 1, "no restart when Android was not made the next boot");
});

// ------------------------------------------------------------------ nothing private in what is shown or kept

const SERIAL = "324950664950", IMEI = "867530012345678", MACADDR = "a4:5e:60:c1:22:9f", PASSWORD = "hunter2-Secret!", SSID = "Kaan Home 5G";
const ADBKEY = "QAAAA" + "Zm9v".repeat(170) + "AAAAAAAAAAAAAAA=";
const DIRTY = [
  `[ro.serialno]: [${SERIAL}]`, `ro.boot.serialno=${SERIAL}`, `adb serial ${SERIAL}`, `IMEI: ${IMEI}`, `+CGSN: ${IMEI}`, `imsi=286011234567890`,
  `ICCID 89900112345678901234`, `wlan0 link/ether ${MACADDR}`, `MU300_PASSWORD=${PASSWORD}`, `password: ${PASSWORD}`, `PSK=wifi-pass-1234`,
  `wpa_passphrase=wifi-pass-1234`, `[device] hotspot: SSID ${SSID} imported (passphrase 14 chars)`, `ADB_KEY='${ADBKEY} web'`,
  "PWHASH='$6$abcdefgh$Q9s8d7f6g5h4j3k2l1qwertyuiopasdfghjklzxcvbnm'", `ro.build.fingerprint=ZTE/MU300/mu300:13/${SERIAL}/user`, "sms from +905321234567",
];
const SECRETS = [SERIAL, IMEI, "286011234567890", "89900112345678901234", MACADDR, PASSWORD, "wifi-pass-1234", SSID, ADBKEY.slice(0, 60), "Q9s8d7f6g5h4", "+905321234567"];
const clean = (text, what) => { for (const x of SECRETS) assert.ok(!String(text).includes(x), `${what} holds ${x}: ${String(text).slice(0, 300)}`); };

test("the masker alone: a serial, an IMEI, a MAC, a password, a hotspot name and a key do not survive", () => {
  const m = new Masker();
  m.add(SERIAL); m.add(PASSWORD, "***");
  clean(m.mask(DIRTY.join("\n")), "the masked text");
  // what a log needs stays readable
  const kept = "MU300-STEP 6/9 systems\n- Installing openwrt\nMU300-PLAN password=given\nregion offset 27762098176\nsha256 " + "ab12".repeat(16) + "\npassword for ubuntu: unchanged";
  assert.equal(m.mask(kept), kept);
});

test("Android: every line the device prints is masked before it reaches the log, an event, a result or an error", async () => {
  pageMasker.add(SERIAL); pageMasker.add(PASSWORD, "***");
  const noisy = DIRTY.join("\r\n") + "\r\n";
  const logged = [];
  const { conn } = await device(async (cmd) => {
    if (cmd.includes(" status")) return { stdout: noisy + `MU300-WEB-STATUS ${JSON.stringify(STATUS)}\n`, exitCode: 0 };
    if (cmd.includes(" follow")) return { stdout: noisy + DIRTY.map((l) => "! " + l).join("\n") + `\nMU300-PLAN have=${IMEI}\nMU300-WEB-EXIT 1\n`, exitCode: 0 };
    if (cmd.includes(" boot-linux")) return { stdout: noisy, exitCode: 0 };
    if (cmd.includes(" clean")) return { stdout: `MU300-WEB-ERROR no-root-dir ${DIRTY.join(" ")}\n`, exitCode: 1 };
    if (cmd.includes("mkdir -p")) return { stdout: DIRTY.join(" "), stderr: DIRTY.join(" "), exitCode: 1 };
    return { stdout: noisy, exitCode: 0 };
  });
  const a = new AndroidDevice(conn, { log: (l) => logged.push(l) });
  assert.deepEqual(await a.status(), STATUS);                 // the answer itself is read from the line as it came
  const events = [];
  await a.follow((e) => events.push(e));
  clean(JSON.stringify(events), "the events");
  assert.ok(events.some((e) => e.type === "error"));
  clean((await a.bootLinux()).join("\n"), "the lines a call returns");
  await assert.rejects(a.clean(), (e) => { clean(e.message + e.detail, "the error"); return e.code === "no-root-dir"; });
  await assert.rejects(a.stage(async () => Buffer.from("")), (e) => e.code === "site-files");
  const sums = DEVICE_FILES.map((n) => `${sha(Buffer.from("x"))}  ${n}\n`).join("");
  await assert.rejects(a.stage(async (n) => Buffer.from(n === "SHA256SUMS" ? sums : "x")), (e) => { clean(e.message, "the stage error"); return e.code === "no-stage"; });
  await assert.rejects(a.web(["take", `x ${PASSWORD}`]), (e) => { clean(e.message, "a refused argument"); return e.code === "bad-argument"; });
  assert.ok(logged.length > 40);
  clean(logged.join("\n"), "the log");
  assert.ok(logged.some((l) => l.includes("••••••••4950")));
});

test("Linux: logs and action output are masked at the source; the facts are read, not shown", async () => {
  pageMasker.add(SERIAL);
  const logged = [], seen = [];
  const { conn } = await device((cmd) => (cmd.includes("mu300-device --json") ? { stdout: "f50\n", exitCode: 0 }
    : cmd.includes("version=") ? { stdout: FACTS + DIRTY.join("\n") + "\n", exitCode: 0 } : { stdout: DIRTY.join("\n") + "\n", stderr: DIRTY.join("\n") + "\n", exitCode: 0 }));
  const l = new LinuxDevice(conn, { log: (x) => logged.push(x) });
  const info = await l.info();
  clean(JSON.stringify(info), "the device's description");
  const r = await l.logs((x) => seen.push(x));
  assert.deepEqual(Object.keys(r), ["rc"], "an action hands back its exit status and no text");
  await l.checkUpdate((x) => seen.push(x));
  clean(seen.join("\n"), "what the manage view shows");
  clean(logged.join("\n"), "the log");
  assert.ok(seen.length >= DIRTY.length * 2);
  assert.equal(mask(`x ${SERIAL} y`), "x ••••••••4950 y");
});

// ------------------------------------------------------------------ nothing a device or a listing says names a path or a command

const HOSTILE = ["a b.zip", "x'; reboot; '.zip", "$(reboot).zip", "`id`.zip", "../../data/adb/x.zip", "mu300-magisk-v1/../x.zip", "mu300-magisk-v1..zip/../../x",
  "mu300-magisk-v1\n.zip", "/system/bin/sh", "mu300-magisk-v1.zip\0", "mu300-magisk-.zip", "-rf", "", "mu300-magisk-v1.zip extra", "mu300-magisk-v1\\.zip", "mu300-magisk-v1;.zip"];

test("a zip or an extra never goes to a path built from a hostile name, and no command is made of one", async () => {
  const { a, dev, calls } = await android({ take: "MU300-WEB-ZIP {}\n", extra: "MU300-WEB-EXTRA vpn\n" });
  const digest = "a".repeat(64);
  for (const name of HOSTILE) await assert.rejects(a.takeZip(new Uint8Array(10), name, digest), code("bad-argument"), JSON.stringify(name));
  for (const bad of ["", "abc", "A".repeat(64), "a".repeat(63) + "'", `${"a".repeat(64)};id`]) {
    await assert.rejects(a.takeZip(new Uint8Array(10), "mu300-magisk-v2026.11.01-openwrt-k5.4.zip", bad), code("bad-argument"), bad);
    await assert.rejects(a.addExtra("vpn", new Uint8Array(10), bad), code("bad-argument"), bad);
  }
  for (const kind of ["lang", "../vpn", "vpn; reboot", "constructor", "__proto__", ""]) await assert.rejects(a.addExtra(kind, new Uint8Array(10), digest), code("bad-argument"), kind);
  assert.equal(dev.files.size, 0, "nothing was pushed");
  assert.deepEqual(calls, [], "nothing was run");
  assert.equal(dev.shellCalls.length, 0);
  // the good ones go to fixed places
  await a.takeZip(new Uint8Array(10), "mu300-magisk-v2026.11.01-openwrt-k5.4.zip", digest);
  await a.addExtra("vpn", new Uint8Array(10), digest);
  assert.deepEqual([...dev.files.keys()], [`${STAGE}/in/mu300-magisk-v2026.11.01-openwrt-k5.4.zip`, `${STAGE}/in/extra-vpn.tar.gz`]);
  assert.deepEqual(calls, [["take", "mu300-magisk-v2026.11.01-openwrt-k5.4.zip", digest], ["extra", "vpn", "extra-vpn.tar.gz", digest]]);
  for (const c of dev.shellCalls) assert.match(c.cmd, /^su -c 'sh \/data\/local\/tmp\/mu300-web\/android-web\.sh [A-Za-z0-9._ -]+'$/);
});

test("a backup pulls only the copies the device script makes, by their exact names", async () => {
  const good = `MU300-WEB-FILE boot_b.img 4 ${sha(Buffer.from("boot"))}\n`;
  const end = "MU300-WEB-BACKUP android_slot=a linux_slot=b\n";
  const names = ["..", ".", "../../../data/adb/magisk.db", "/data/system/users/0/accounts.db", "boot_b.img/../x", "prodnv.img", "nr_fixnv1_a.img", "persist.img",
    "userdata.img", "boot_c.img", "boot_b.img;reboot", "$(id)", "a b", "misc.bin", "boot_b.IMG", "x'y", "boot_b.img\\"];
  for (const name of names) {
    const x = await android({ backup: `${good}MU300-WEB-FILE ${name} 4 ${sha(Buffer.from("evil"))}\n${end}`, cat: () => b64(Buffer.from("evil")), clean: "MU300-WEB-CLEAN ok\n" });
    const store = new Map();
    await assert.rejects(x.a.backup(memorySink(store)), code("bad-answer"), name);
    assert.equal(store.size, 0, `${name}: nothing is written on this computer`);
    assert.ok(!x.calls.some((c) => c[0] === "cat"), `${name}: the device is not asked for it`);
  }
  // sizes and checksums that are not what they must be, and a name given twice
  for (const line of [`MU300-WEB-FILE boot_b.img -4 ${"a".repeat(64)}`, `MU300-WEB-FILE boot_b.img 4e3 ${"a".repeat(64)}`, `MU300-WEB-FILE boot_b.img 99999999999 ${"a".repeat(64)}`,
    "MU300-WEB-FILE boot_b.img 4 zz", `MU300-WEB-FILE boot_b.img 4 ${"a".repeat(64)} extra`.replace(" extra", "\nMU300-WEB-FILE boot_b.img 4 " + "a".repeat(64))]) {
    const x = await android({ backup: `${line}\n${end}`, clean: "MU300-WEB-CLEAN ok\n" });
    await assert.rejects(x.a.backup(memorySink(new Map())), code("bad-answer"), line);
  }
});

test("the Linux adapter sends only its own commands: what the device says is never part of one", async () => {
  const evil = "ubuntu; rm -rf / #";
  const { conn, dev } = await device((cmd) => (cmd.includes("mu300-device --json")
    ? { stdout: JSON.stringify({ mu300: 1, device: "f50", release: "$(reboot)" }), exitCode: 0 }
    : cmd.includes("version=") ? { stdout: `kernel_choice=6.18'; reboot; '\nboot_os=${evil}\nsystems=${evil} openwrt\n`, exitCode: 0 } : { stdout: "", exitCode: 0 }));
  const l = new LinuxDevice(conn);
  const info = await l.info();
  assert.deepEqual([info.kernelChoice, info.bootOs, info.systems, info.version], ["", "", [], ""]);
  assert.throws(() => l.setBootOs(evil), code("bad-argument"));
  assert.throws(() => l.setKernel("6.18'; reboot; '"), code("bad-argument"));
  for (const c of dev.shellCalls) assert.ok(!c.cmd.includes("rm -rf") && !c.cmd.includes("reboot;"), c.cmd);
});

test("the device downloads a zip itself: a job, then the same check; nothing but a release zip's name is sent", async () => {
  const name = "mu300-magisk-v2026.11.01-openwrt-k6.18.zip", digest = "b".repeat(64);
  const ok = await android({ run: "MU300-WEB-JOB started fetch\n", follow: "MU300-PROGRESS 500 1000\nMU300-PROGRESS 1000 1000\n\nMU300-WEB-EXIT 0\n", take: `MU300-WEB-ZIP {"web":1}\n` });
  const prog = [];
  assert.deepEqual(await ok.a.fetchZip(name, 1000, digest, (d, t) => prog.push([d, t])), { web: 1 });
  assert.deepEqual(prog, [[500, 1000], [1000, 1000]]);
  assert.deepEqual(ok.calls, [["run", "fetch", name, "1000"], ["follow"], ["take", name, digest]]);
  // the job's exit status says why: no connection, sent on to another host, failed, not the release's size
  for (const [rc, why] of [[10, "fetch-no-network"], [11, "fetch-host"], [12, "download-failed"], [13, "fetch-size"], [1, "download-failed"], ["interrupted", "download-failed"]]) {
    const bad = await android({ run: "MU300-WEB-JOB started fetch\n", follow: `! the download failed: wget: 324950664950111 said no\n\nMU300-WEB-EXIT ${rc}\n` });
    await assert.rejects(bad.a.fetchZip(name, 1000, digest), (e) => e.code === why && /the download failed/.test(e.detail) && !/324950664950111/.test(e.detail), String(rc));
    assert.ok(!bad.calls.some((c) => c[0] === "take"), "a download that failed is never taken");
  }
  // a download that is larger than asked for is not shown as more than the whole
  const big = await android({ run: "MU300-WEB-JOB started fetch\n", follow: "MU300-PROGRESS 5000 1000\n\nMU300-WEB-EXIT 13\n" });
  const seen = [];
  await assert.rejects(big.a.fetchZip(name, 1000, digest, (d, t) => seen.push([d, t])), code("fetch-size"));
  assert.deepEqual(seen, [[1000, 1000]]);
  // the digest the release names is what the device is asked to check, and a mismatch is the device's word
  const wrong = await android({ run: "MU300-WEB-JOB started fetch\n", follow: "MU300-PROGRESS 1000 1000\n\nMU300-WEB-EXIT 0\n", take: "MU300-WEB-ERROR sha-mismatch " + "c".repeat(64) + "\n" });
  await assert.rejects(wrong.a.fetchZip(name, 1000, digest), code("sha-mismatch"));
  assert.deepEqual(wrong.calls[wrong.calls.length - 1], ["take", name, digest]);
  for (const n of HOSTILE) await assert.rejects(ok.a.fetchZip(n, 1000, digest), code("bad-argument"), JSON.stringify(n));
  for (const size of [0, -1, 1.5, "10", NaN]) await assert.rejects(ok.a.fetchZip(name, size, digest), code("bad-argument"));
});

test("cancelling a download the device is doing ends the job there", async () => {
  const name = "mu300-magisk-v2026.11.01-openwrt-k6.18.zip";
  const ac = new AbortController();
  const d = await android({ run: "MU300-WEB-JOB started fetch\n", cancel: "MU300-WEB-JOB cancelled\n",
    follow: async function* () { yield { out: "MU300-PROGRESS 100 1000\n" }; await new Promise((r) => setTimeout(r, 5000)); return 0; } });
  const p = d.a.fetchZip(name, 1000, "b".repeat(64), () => ac.abort(), ac.signal);
  await assert.rejects(p, (e) => e.code === "aborted");
  assert.deepEqual(d.calls.map((c) => c[0]), ["run", "follow", "cancel"]);
});

test("net: whether the device can reach GitHub, and over what; only shapes the page knows come through", async () => {
  const name = "mu300-magisk-v2026.11.01-openwrt-k6.18.zip";
  const d = await android({ net: 'MU300-WEB-NET {"ok":1,"why":"","uplink":"mobile","detail":""}\n' });
  assert.deepEqual(await d.a.net(name), { ok: true, why: "", uplink: "mobile", detail: "" });
  assert.deepEqual(d.calls, [["net", name]]);
  await d.a.net();
  assert.deepEqual(d.calls[1], ["net"]);
  const none = await android({ net: `MU300-WEB-NET {"ok":0,"why":"no-network","uplink":"none","detail":"wget: bad address 'github.com'"}\n` });
  assert.deepEqual(await none.a.net(name), { ok: false, why: "no-network", uplink: "none", detail: "wget: bad address 'github.com'" });
  // a device's words are masked and cut, and a kind of connection the page has no word for is "none"
  const odd = await android({ net: `MU300-WEB-NET {"ok":"yes","why":"<script>","uplink":"satellite","detail":"IMEI 867530012345678 ${"x".repeat(400)}"}\n` });
  const o = await odd.a.net(name);
  assert.deepEqual([o.ok, o.why, o.uplink], [false, "", "none"]);
  assert.ok(!o.detail.includes("867530012345678") && o.detail.length <= 160);
  await assert.rejects((await android({ net: "MU300-WEB-NET not json\n" })).a.net(name), code("bad-answer"));
  await assert.rejects((await android({ net: "nothing\n" })).a.net(name), code("no-answer"));
  for (const n of HOSTILE) await assert.rejects(d.a.net(n), code("bad-argument"), JSON.stringify(n));
});
