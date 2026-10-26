// The web installer's logic that needs neither a screen nor a device: what is masked, checksums, reading a picked
// zip, the release list, and the wizard's rules (site/assets/web-installer/{privacy,sha256,zip,github,flow}.js).
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import zlib from "node:zlib";

import { Masker, maskSerial } from "../../site/assets/web-installer/privacy.js";
import { Sha256, sha256Hex } from "../../site/assets/web-installer/sha256.js";
import { ZipError, inspectMagiskZip, openZip, parseManifest } from "../../site/assets/web-installer/zip.js";
import { GithubError, LAST_RELEASE_WITHOUT_WEB, combos, findZip, loadReleases, loadVpnModule, releaseSupportsWeb, zipName } from "../../site/assets/web-installer/github.js";
import { GIB, MIB, NEED, analyseStorage, answersFor, classifyLine, dataNeeded, deviceBlockers, fmtSize, needFor, parsePlan, stageRisk, validPassword, validateChoices } from "../../site/assets/web-installer/flow.js";

// ------------------------------------------------------------------ privacy

test("serial numbers are masked to their last four characters", () => {
  assert.equal(maskSerial("324950664950"), "••••••••4950");
  assert.equal(maskSerial("MU300LINUX-324950664950-203d8329"), "••••••••8329");
  assert.equal(maskSerial("abc"), "•••");
  assert.equal(maskSerial(""), "");
  assert.equal(maskSerial(undefined), "");
});

test("the log masker: serials, identity numbers, phone numbers and MAC addresses go; offsets and checksums stay", () => {
  const m = new Masker();
  m.add("324950664950");
  m.add("MU300LINUX-324950664950-203d8329");
  m.add("hunter2-secret", "***");
  m.add("ab");                                         // too short to be a secret worth replacing everywhere
  const t = m.mask([
    "serial 324950664950 and MU300LINUX-324950664950-203d8329",
    "IMEI: 867530012345678 IMSI 286011234567890 ICCID 8990011234567890123F? 89900112345678901234",
    "sms from +90 532 123 45 67 and +905321234567",
    "password hunter2-secret",
    "wlan0 a4:5e:60:c1:22:9f",
    "region offset 27762098176 size 34359738368",
    "sha256 602f3e1765c4b05c9b17c1c837b7137065889d75950aad1f1cdec3bbb45ce904",
    "date 20261013051733 misc 5f61000042434142010200009f001e000000000000000000000000000be17146",
    "ab stays",
  ].join("\n")).split("\n");
  assert.equal(t[0], "serial ••••••••4950 and ••••••••8329");
  assert.equal(t[1], "IMEI: [masked] IMSI [15 digits] ICCID 8990011234567890123F? [20 digits]");
  assert.equal(t[2], "sms from [phone number] and [phone number]");
  assert.equal(t[3], "password ***");
  assert.equal(t[4], "wlan0 a4:5e:60:••:••:••");
  assert.equal(t[5], "region offset 27762098176 size 34359738368");
  assert.equal(t[6], "sha256 602f3e1765c4b05c9b17c1c837b7137065889d75950aad1f1cdec3bbb45ce904");
  assert.equal(t[7], "date [14 digits] misc 5f61000042434142010200009f001e000000000000000000000000000be17146");
  assert.equal(t[8], "ab stays");
});

// ------------------------------------------------------------------ sha256

test("SHA-256 in pieces equals Node's, for every split around the block size", async () => {
  for (const n of [0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000, 70000]) {
    const data = new Uint8Array(crypto.randomBytes(n));
    const want = crypto.createHash("sha256").update(data).digest("hex");
    assert.equal(new Sha256().update(data).hex(), want, `whole ${n}`);
    const h = new Sha256();
    for (let o = 0; o < n; ) { const step = 1 + ((o * 7) % 97); h.update(data.subarray(o, o + step)); o += step; }
    assert.equal(h.hex(), want, `pieces ${n}`);
    assert.equal(h.hex(), want, "hex() twice");
    assert.equal(await sha256Hex(data), want);
  }
  assert.equal(new Sha256().update(new TextEncoder().encode("abc")).hex(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.throws(() => { const h = new Sha256(); h.hex(); h.update(new Uint8Array(1)); });
});

// ------------------------------------------------------------------ zip

// a zip as the release builds them: small files deflated, the payload stored
function makeZip(files) {
  const locals = [], central = [];
  let off = 0;
  for (const [name, data, method = 8] of files) {
    const raw = Buffer.from(data);
    const body = method === 8 ? zlib.deflateRawSync(raw) : raw;
    const n = Buffer.from(name);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(method, 8);
    lh.writeUInt32LE(zlib.crc32 ? zlib.crc32(raw) : 0, 14); lh.writeUInt32LE(body.length, 18); lh.writeUInt32LE(raw.length, 22);
    lh.writeUInt16LE(n.length, 26);
    locals.push(lh, n, body);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(method, 10); ch.writeUInt32LE(body.length, 20); ch.writeUInt32LE(raw.length, 24);
    ch.writeUInt16LE(n.length, 28); ch.writeUInt32LE(off, 42);
    central.push(ch, n);
    off += 30 + n.length + body.length;
  }
  const cd = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(files.length, 8); eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(off, 16);
  return new Blob([Buffer.concat([...locals, cd, eocd])]);
}
const MANIFEST = (o = {}) => Object.entries({ TAG: "v2026.11.01", SYSTEM: "ubuntu-24.04", OS: "ubuntu", UBUNTU: "24.04", KERNEL: "6.18",
  KERNEL_ASSET: "mu300-kernel-6.18.tar.gz", ROOTFS_ASSET: "mu300-ubuntu-rootfs.tar.gz", SHA256_KERNEL: "a".repeat(64), SHA256_ROOTFS: "b".repeat(64), ...o })
  .map(([k, v]) => `${k}=${v}\n`).join("");
const magiskZip = (manifest = MANIFEST(), installer = "#!/system/bin/sh\n# MU300_ANSWERS MU300-STEP\n") => makeZip([
  ["mu300/manifest", manifest], ["mu300/install.sh", installer], ["mu300/android-install.sh", "x"], ["switch.sh", "x"], ["module.prop", "id=mu300_linux_switch\n"],
  ["payload/mu300-ubuntu-rootfs.tar.gz", crypto.randomBytes(200000), 0]]);

test("a picked Magisk zip: its manifest and whether its installer knows the web installer", async () => {
  assert.deepEqual(await inspectMagiskZip(magiskZip()), { tag: "v2026.11.01", system: "ubuntu-24.04", os: "ubuntu", ubuntu: "24.04", kernel: "6.18", web: true });
  assert.equal((await inspectMagiskZip(magiskZip(MANIFEST(), "#!/system/bin/sh\necho old\n"))).web, false);
  const z = await openZip(magiskZip());
  assert.equal(z.entries.get("payload/mu300-ubuntu-rootfs.tar.gz").method, 0);
  assert.equal((await z.read("payload/mu300-ubuntu-rootfs.tar.gz")).length, 200000);
  await assert.rejects(z.read("payload/mu300-ubuntu-rootfs.tar.gz", 1000), (e) => e.code === "too-large");
  await assert.rejects(z.read("nope"), (e) => e.code === "no-entry");
});

test("a zip that is also an installer for a computer (computer/, START-HERE.md, the launchers) is read the same", async () => {
  const extra = [["START-HERE.md", "# MU300 Linux"], ["start.sh", "#!/bin/sh\n"], ["start.cmd", "@echo off\r\n"], ["start-gui.sh", "x"],
    ["start-gui.cmd", "x"], ["uninstall-linux.sh", "x"], ["uninstall-linux.cmd", "x"], ["computer/.mu300-package", "x"]];
  for (let i = 0; i < 200; i++) extra.push([`computer/tools/file-${i}.sh`, "#!/bin/sh\n# " + "x".repeat(i)]);
  const zip = makeZip([["mu300/manifest", MANIFEST()], ["mu300/install.sh", "#!/system/bin/sh\n# MU300_ANSWERS MU300-STEP\n"],
    ["mu300/android-install.sh", "x"], ["switch.sh", "x"], ["module.prop", "id=mu300_linux_switch\n"], ...extra,
    ["payload/mu300-ubuntu-rootfs.tar.gz", crypto.randomBytes(100000), 0]]);
  assert.deepEqual(await inspectMagiskZip(zip), { tag: "v2026.11.01", system: "ubuntu-24.04", os: "ubuntu", ubuntu: "24.04", kernel: "6.18", web: true });
});

test("what is not one of our zips is refused with a reason", async () => {
  const code = (c) => (e) => e instanceof ZipError && e.code === c;
  await assert.rejects(inspectMagiskZip(new Blob(["hello"])), code("not-a-zip"));
  await assert.rejects(inspectMagiskZip(new Blob([crypto.randomBytes(5000)])), code("not-a-zip"));
  await assert.rejects(inspectMagiskZip(makeZip([["readme.txt", "hi"]])), code("not-ours"));
  for (const bad of [{ TAG: "2026" }, { TAG: "v1/../x" }, { SYSTEM: "arch" }, { OS: "openwrt" }, { UBUNTU: "26.04" }, { KERNEL: "6.6" }, { SHA256_ROOTFS: "zz" }]) {
    await assert.rejects(inspectMagiskZip(magiskZip(MANIFEST(bad))), code("bad-manifest"), JSON.stringify(bad));
  }
  assert.throws(() => parseManifest(""), code("bad-manifest"));
  // a deflated entry that unpacks to more than is allowed (a zip bomb in the manifest's place)
  const bomb = makeZip([["mu300/manifest", Buffer.alloc(5 * 1024 * 1024)], ["mu300/install.sh", "x"], ["mu300/android-install.sh", "x"], ["switch.sh", "x"], ["module.prop", "x"]]);
  await assert.rejects(inspectMagiskZip(bomb), code("too-large"));
  // a directory that points outside the file
  const buf = Buffer.from(await magiskZip().arrayBuffer());
  buf.writeUInt32LE(buf.length + 10, buf.length - 6);
  await assert.rejects(inspectMagiskZip(new Blob([buf])), code("not-a-zip"));
});

// ------------------------------------------------------------------ GitHub

const asset = (name, size = 1000, digest = "sha256:" + "c".repeat(64)) => ({ name, size, digest, browser_download_url: "https://evil.example/" + name });
const rel = (tag, o = {}) => ({ tag_name: tag, prerelease: false, draft: false, published_at: "2026-11-01T10:00:00Z",
  assets: ["openwrt", "openwrt-luci", "ubuntu-24.04"].flatMap((s) => ["5.4", "6.18", "7.2"].map((k) => asset(zipName(tag, s, k))))
    .concat(["6.18", "7.2"].map((k) => asset(zipName(tag, "ubuntu-26.04", k)))), ...o });
const fetchOf = (body, status = 200, headers = {}) => async (url, init) => {
  fetchOf.calls.push([url, init]);
  return { ok: status >= 200 && status < 300, status, headers: { get: (k) => headers[k] }, json: async () => (typeof body === "function" ? body() : body) };
};
fetchOf.calls = [];

test("releases: the newest release, and a beta only when it is newer than that", async () => {
  const list = [rel("v2026.11.01"), rel("v2026.10.30", { prerelease: true, published_at: "2026-10-30T10:00:00Z" }), rel("v2026.10.23", { published_at: "2026-10-12T10:00:00Z" })];
  let r = await loadReleases(fetchOf(list));
  assert.equal(r.stable.tag, "v2026.11.01");
  assert.equal(r.beta, null);
  list.push(rel("v2026.11.05", { prerelease: true, published_at: "2026-11-05T10:00:00Z" }));
  r = await loadReleases(fetchOf(list));
  assert.equal(r.stable.tag, "v2026.11.01");
  assert.equal(r.beta.tag, "v2026.11.05");
  const [url, init] = fetchOf.calls.at(-1);
  assert.equal(url, "https://api.github.com/repos/dikeckaan/mu300-linux/releases?per_page=30");
  assert.equal(init.credentials, "omit");
  assert.equal(init.referrerPolicy, "no-referrer");
});

test("releases: a zip is found by system and kernel, with the checksum GitHub computed and a link built here", async () => {
  const { stable } = await loadReleases(fetchOf([rel("v2026.11.01")]));
  const z = findZip(stable, "openwrt-luci", "7.2");
  assert.deepEqual(z, { name: "mu300-magisk-v2026.11.01-openwrt-luci-k7.2.zip", size: 1000, sha256: "c".repeat(64),
    url: "https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.01/mu300-magisk-v2026.11.01-openwrt-luci-k7.2.zip" });
  assert.equal(findZip(stable, "ubuntu-26.04", "5.4"), null);
  assert.equal(findZip(stable, "arch", "5.4"), null);
  assert.deepEqual(combos(stable), { "ubuntu-24.04": ["5.4", "6.18", "7.2"], "ubuntu-26.04": ["6.18", "7.2"], openwrt: ["5.4", "6.18", "7.2"], "openwrt-luci": ["5.4", "6.18", "7.2"] });
  // a release with the k7.3 zips (7.3, the fourth kernel) offers 7.3 too; one without them (v2026.10.19.2 and older) does not
  const { stable: with73 } = await loadReleases(fetchOf([rel("v2026.11.01", { assets: rel("v2026.11.01").assets
    .concat(["openwrt", "ubuntu-26.04"].map((s) => asset(zipName("v2026.11.01", s, "7.3")))) })]));
  assert.deepEqual(combos(with73).openwrt, ["5.4", "6.18", "7.2", "7.3"]);
  assert.deepEqual(combos(with73)["ubuntu-26.04"], ["6.18", "7.2", "7.3"]);
  assert.equal(releaseSupportsWeb(stable), true);
  assert.equal(releaseSupportsWeb({ tag: LAST_RELEASE_WITHOUT_WEB }), false);
  assert.equal(releaseSupportsWeb({ tag: "v2026.10.19" }), false);
  assert.equal(releaseSupportsWeb({ tag: "v2026.10.24" }), true);
  assert.equal(releaseSupportsWeb({ tag: "v2026.10.23-fix1" }), false);
});

test("releases: what the API says is checked for its shape before it is used", async () => {
  const hostile = [
    { tag_name: "v1/../../x", assets: [] }, { tag_name: "<script>", assets: [] }, { tag_name: 5 }, null, "text",
    rel("v2026.11.02", { draft: true }),
    rel("v2026.11.01", { assets: [asset("../../etc/passwd"), asset("a b.zip"), asset("ok.zip", -5), asset("ok2.zip", 1.5), asset("nohash.zip", 10, "md5:abc"),
      asset("mu300-magisk-v2026.11.01-openwrt-k5.4.zip", 10, "sha256:" + "Z".repeat(64)), asset("mu300-magisk-v2026.11.01-openwrt-k6.18.zip", 10)] }),
  ];
  const { stable } = await loadReleases(fetchOf(hostile));
  assert.equal(stable.tag, "v2026.11.01");
  assert.deepEqual([...stable.assets.keys()].sort(), ["mu300-magisk-v2026.11.01-openwrt-k5.4.zip", "mu300-magisk-v2026.11.01-openwrt-k6.18.zip", "nohash.zip"]);
  assert.equal(findZip(stable, "openwrt", "5.4"), null, "a zip without a usable checksum is not offered");
  assert.ok(findZip(stable, "openwrt", "6.18"));
  for (const a of stable.assets.values()) assert.ok(a.url.startsWith("https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.01/"));
});

test("releases: the failures have names", async () => {
  const code = (c) => (e) => e instanceof GithubError && e.code === c;
  await assert.rejects(loadReleases(async () => { throw new TypeError("Failed to fetch"); }), code("network"));
  await assert.rejects(loadReleases(fetchOf({}, 403, { "x-ratelimit-reset": "1790000000" })), (e) => e.code === "rate-limit" && e.reset === 1790000000);
  await assert.rejects(loadReleases(fetchOf({}, 500)), code("http"));
  await assert.rejects(loadReleases(fetchOf({ message: "x" })), code("bad-answer"));
  await assert.rejects(loadReleases(fetchOf(() => { throw new SyntaxError("json"); })), code("bad-answer"));
  await assert.rejects(loadReleases(fetchOf([])), code("no-release"));
});

test("the VPN module's release", async () => {
  const m = await loadVpnModule(fetchOf({ tag_name: "v2.0.1", assets: [asset("mu300-linux-vpn.tar.gz", 41946588)] }));
  assert.deepEqual(m, { tag: "v2.0.1", name: "mu300-linux-vpn.tar.gz", size: 41946588, sha256: "c".repeat(64),
    url: "https://github.com/dikeckaan/mu300-linux-vpn/releases/download/v2.0.1/mu300-linux-vpn.tar.gz" });
  await assert.rejects(loadVpnModule(fetchOf({ tag_name: "v2.0.1", assets: [] })), (e) => e.code === "no-release");
});

// ------------------------------------------------------------------ the wizard's rules

// the status of the usual 64 GB F50 (FINDINGS 9): partitions end near 26 GiB, about 32 GiB free behind them
function status(o = {}) {
  const disk = 122142720;                                   // sectors of a 58.2 GiB eMMC
  const lastEnd = 54222848;
  const start = (Math.floor(lastEnd / 4096) + 1) * 4096, end = (Math.floor((disk - 34) / 4096) - 1) * 4096;
  return { v: 1, device: "f50", model: "ZTE F50", android_slot: "a", linux_slot: "b", slots_ok: 1, root_kind: "magisk", magiskboot: 1,
    boot_completed: 1, stalled: 0, misc: { valid: 1, bc: "00" }, linux_boot: "android-copy", disk, last_end: lastEnd,
    region: { off: start * 512, size: (end - start) * 512, existing: "no", dirty: 0 },
    parts: [["boot_a", 2048, 131072], ["super", 200000, 10000000], ["userdata", 20000000, lastEnd - 20000000]],
    sd: { dev: "", bytes: 0, small: 0, existing: "no" }, data_free: 20 * GIB, job: { state: "none" }, ...o };
}
const choices = (o = {}) => ({ target: "internal", systems: ["ubuntu-24.04", "openwrt-luci"], kernel: "5.4", bootOs: "ubuntu", defaultLinux: true,
  attempts: 5, hotspot: true, gpu: true, vpn: false, mode: "update", password: "", eraseConfirmed: false, overwriteConfirmed: false, ...o });
const check = (c, s = status(), zips) => validateChoices(c, { status: s, analysis: analyseStorage(s), zips }).map((e) => e.code);

test("storage: the tested layout, with the bar to scale", () => {
  const a = analyseStorage(status());
  assert.equal(a.internal.verdict, "tested-layout");
  assert.equal(a.target, "internal");
  assert.equal(a.makeRoom, null);
  assert.deepEqual(a.bar.map((b) => b.kind), ["android-system", "userdata", "free"]);
  assert.equal(a.bar.reduce((x, b) => x + b.size, 0), a.disk);
  assert.equal(a.sd.present, false);
});

test("storage: verdicts by size, by what is there, and the card", () => {
  const sized = (mib, o = {}) => analyseStorage(status({ region: { off: 27762098176, size: mib * MIB, existing: "no", dirty: 0, ...o } }));
  assert.equal(sized(3000).internal.verdict, "both");
  assert.equal(sized(2000).internal.verdict, "one");
  assert.equal(sized(900).internal.verdict, "openwrt-only");
  assert.equal(sized(600).internal.verdict, "too-small");
  assert.equal(sized(600).internal.usable, false);
  assert.equal(sized(600).target, null);
  assert.equal(sized(30000, { dirty: 3 }).internal.verdict, "dirty");
  const ex = sized(4000, { existing: "yes" });
  assert.equal(ex.internal.verdict, "existing");
  assert.deepEqual(ex.bar.map((b) => b.kind), ["android-system", "userdata", "linux", "free"]);
  assert.equal(ex.bar.reduce((x, b) => x + b.size, 0), ex.disk);
  // an installation that starts behind an alignment gap: the gap is drawn, the sum is still the disk
  const gap = sized(4000, { existing: "yes", off: 27762098176 + 2 * MIB });
  assert.deepEqual(gap.bar.map((b) => b.kind), ["android-system", "userdata", "gap", "linux", "free"]);
  assert.equal(gap.bar.reduce((x, b) => x + b.size, 0), gap.disk);
  const card = (existing) => analyseStorage(status({ sd: { dev: "/dev/block/mmcblk1p1", bytes: 32 * GIB, small: 0, existing } }));
  assert.equal(card("no").target, "internal");
  assert.equal(card("yes").target, "sd");
  assert.equal(card("foreign").sd.usable, false);
  const small = analyseStorage(status({ region: { off: 0, size: 0, existing: "no", dirty: -1 }, sd: { dev: "/dev/block/mmcblk1p1", bytes: 32 * GIB, small: 0, existing: "no" } }));
  assert.equal(small.target, "sd");
});

test("storage: the 32 GB variant gets the make-room figures, not an offer to repartition", () => {
  const disk = 61071360;                                    // 29.1 GiB
  const s = status({ disk, last_end: disk - 2048, region: { off: 0, size: 0, existing: "no", dirty: -1 },
    parts: [["super", 200000, 10000000], ["userdata", 20000000, disk - 2048 - 20000000]] });
  const a = analyseStorage(s);
  assert.equal(a.internal.usable, false);
  assert.ok(a.makeRoom.possible);
  assert.equal(a.makeRoom.total, (disk - 34 - 20000000) * 512);
  assert.equal(a.makeRoom.maxLinux, a.makeRoom.total - 4 * GIB);
  assert.equal(a.makeRoom.minLinux, NEED.openwrt);
  // a device whose last partition is not userdata gets no such figures
  assert.equal(analyseStorage({ ...s, parts: [["userdata", 2048, 1000], ["blackbox", 20000000, disk - 2048 - 20000000]] }).makeRoom, null);
});

test("storage: another installer's GPT partition named mu300root is Linux's place, not Android's (FINDINGS 179)", () => {
  // F50-C with the layout emulated: userdata, then mu300root (804 MiB) right behind it, nothing free after it
  const disk = 122159104, udEnd = 120500224, ps = 120504320, pz = 1646592;
  const part = (region) => status({ disk, last_end: ps + pz,
    parts: [["super", 200000, 10000000], ["userdata", 20000000, udEnd - 20000000], ["mu300root", ps, pz]],
    region: { off: ps * 512, size: pz * 512, existing: "no", dirty: 0, part: 1, part_fs: "none", part_count: 1, foreign_image: 0, ...region } });
  const ex = analyseStorage(part({ existing: "yes", part_fs: "mu300root" }));
  assert.equal(ex.internal.verdict, "part-existing");
  assert.equal(ex.internal.usable, true);
  assert.equal(ex.target, "internal");
  assert.equal(ex.makeRoom, null);
  assert.deepEqual(ex.bar.map((b) => b.kind), ["android-system", "userdata", "gap", "linux", "free"]);
  assert.equal(ex.bar.reduce((x, b) => x + b.size, 0), ex.disk);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt", kernel: "6.18" }), part({ existing: "yes", part_fs: "mu300root" })), []);
  assert.equal(analyseStorage(part({})).internal.verdict, "part-free");
  assert.deepEqual(analyseStorage(part({})).bar.map((b) => b.kind), ["android-system", "userdata", "gap", "free", "free"]);
  assert.equal(analyseStorage(part({ dirty: 4 })).internal.verdict, "part-dirty");
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt", kernel: "6.18" }), part({ dirty: 4 })), ["confirm-overwrite"]);
  // what cannot be used says why, and is never offered
  const foreign = analyseStorage(part({ part_fs: "ext4" }));
  assert.equal(foreign.internal.verdict, "part-foreign");
  assert.equal(foreign.internal.usable, false);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt" }), part({ part_fs: "ext4" })), ["part-foreign"]);
  const two = status({ disk, last_end: ps + pz, parts: [["userdata", 20000000, udEnd - 20000000], ["mu300root", ps, 2048], ["mu300root", ps + 2048, pz - 2048]],
    region: { off: (ps + pz + 4096) * 512, size: 0, existing: "no", dirty: -1, part: 0, part_fs: "none", part_count: 2, foreign_image: 0 } });
  assert.equal(analyseStorage(two).internal.verdict, "part-ambiguous");
  assert.equal(analyseStorage(two).makeRoom, null);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt" }), two), ["part-ambiguous", "too-small"]);
  assert.equal(analyseStorage(part({ foreign_image: 1 })).internal.foreignImage, true);
  // a status from before these fields: as before
  assert.equal(analyseStorage(status()).internal.part, false);
});

test("choices the device would refuse are refused here first", () => {
  assert.deepEqual(check(choices()), []);
  assert.deepEqual(check(choices({ systems: [] })), ["no-system"]);
  assert.deepEqual(check(choices({ systems: ["openwrt", "openwrt-luci"], bootOs: "openwrt" })), ["one-of-each"]);
  assert.deepEqual(check(choices({ systems: ["ubuntu-26.04"], kernel: "5.4" })), ["ubuntu-2604-needs-mainline"]);
  assert.deepEqual(check(choices({ kernel: "6.6" })), ["no-kernel"]);
  // the ZTE V50 (FINDINGS 95): mainline only; the F50 and the U30 Air keep 5.4
  assert.deepEqual(check(choices(), status({ device: "v50" })), ["v50-needs-mainline"]);
  assert.deepEqual(check(choices({ kernel: "6.18" }), status({ device: "v50" })), []);
  assert.deepEqual(check(choices({ kernel: "7.2" }), status({ device: "v50" })), []);
  assert.deepEqual(check(choices(), status({ device: "u30air" })), []);
  assert.deepEqual(deviceBlockers(status({ device: "v50" })), []);
  assert.deepEqual(check(choices({ bootOs: "openwrt" })), ["boot-os-not-installed"]);
  assert.deepEqual(check(choices({ attempts: 7 })), ["attempts"]);
  assert.deepEqual(check(choices({ password: "short" })), ["password"]);
  assert.deepEqual(check(choices({ target: "sd" })), ["no-card", "confirm-erase", "too-small"]);
  assert.deepEqual(check(choices(), status(), { "ubuntu-24.04": {}, "openwrt-luci": null }), ["no-zip"]);
  const small = status({ region: { off: 27762098176, size: 2000 * MIB, existing: "no", dirty: 0 } });
  assert.deepEqual(check(choices(), small), ["too-small"]);
  assert.deepEqual(check(choices({ systems: ["ubuntu-24.04"] }), small), []);
  const dirty = status({ region: { off: 27762098176, size: 30 * GIB, existing: "no", dirty: 2 } });
  assert.deepEqual(check(choices(), dirty), ["confirm-overwrite"]);
  assert.deepEqual(check(choices({ overwriteConfirmed: true }), dirty), []);
});

test("choices: a card, an existing installation, and what stays beside what is installed", () => {
  const card = (existing, o = {}) => status({ sd: { dev: "/dev/block/mmcblk1p1", bytes: 32 * GIB, small: 0, existing }, ...o });
  assert.deepEqual(check(choices({ target: "sd" }), card("no")), ["confirm-erase"]);
  assert.deepEqual(check(choices({ target: "sd", eraseConfirmed: true }), card("no")), []);
  assert.deepEqual(check(choices({ target: "sd", eraseConfirmed: true }), card("foreign")), ["card-foreign"]);
  assert.deepEqual(check(choices({ target: "internal" }), card("yes")), ["card-boots-first"]);
  // an installation on the card: update needs no confirmation, wipe does
  const onCard = card("yes", { linux: { systems: { openwrt: "v1" }, ubuntu: "" } });
  assert.deepEqual(check(choices({ target: "sd", systems: ["ubuntu-24.04"], bootOs: "openwrt" }), onCard), []);
  assert.deepEqual(check(choices({ target: "sd", mode: "wipe" }), onCard), ["confirm-erase"]);
  assert.deepEqual(check(choices({ target: "sd", mode: "wipe", systems: ["ubuntu-24.04"], bootOs: "openwrt", eraseConfirmed: true }), onCard), ["boot-os-not-installed"]);
  // internal, existing: the kept system counts for the space and may be the one that boots
  const ex = status({ region: { off: 27762098176, size: 2000 * MIB, existing: "yes", dirty: -1 }, linux: { systems: { openwrt: "v1" }, ubuntu: "" } });
  assert.deepEqual(check(choices({ systems: ["ubuntu-24.04"], bootOs: "openwrt" }), ex), ["too-small"]);
  assert.deepEqual(check(choices({ systems: ["ubuntu-24.04"], mode: "wipe" }), ex), []);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt", mode: undefined }), ex), ["no-mode"]);
  const u26 = status({ region: { off: 27762098176, size: 8 * GIB, existing: "yes", dirty: -1 }, linux: { systems: { ubuntu: "v1" }, ubuntu: "26.04" } });
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "ubuntu", kernel: "5.4" }), u26), ["installed-2604-needs-mainline"]);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "ubuntu", kernel: "6.18" }), u26), []);
  assert.deepEqual(check(choices({ systems: ["openwrt"], bootOs: "openwrt", kernel: "5.4", mode: "wipe" }), u26), []);
});

test("what blocks an installation before any choice", () => {
  assert.deepEqual(deviceBlockers(status()), []);
  assert.deepEqual(deviceBlockers(status({ device: "", slots_ok: 0, misc: { valid: 0 }, magiskboot: 0, root_kind: "kernelsu", stalled: 1, boot_completed: 0 })).map((b) => b.code),
    ["unknown-model", "no-slots", "misc-invalid", "not-magisk", "stalled", "still-booting"]);
  assert.deepEqual(deviceBlockers(status({ magiskboot: 0 })).map((b) => b.code), ["no-magiskboot"]);
});

test("passwords: what the settings file would change is not accepted", () => {
  for (const ok of ["", "correct horse", "pä55wörd-ok", "a'b\"c$d\\e"]) assert.ok(validPassword(ok), ok);
  for (const bad of ["12345", " leading", "trailing ", '"quoted"', "'quoted'", "two\nlines", "tab\tinside", "x".repeat(129), 123456, null]) assert.ok(!validPassword(bad), String(bad));
});

test("the answers of each run", () => {
  const s = status();
  const ctx = { lang: "tr", status: s, analysis: analyseStorage(s), adbKeyLine: "QAAAA= web" };
  const c = choices({ bootOs: "openwrt-luci", attempts: 3, hotspot: false, password: "never in this file" });
  assert.equal(answersFor(c, 0, ctx), "MU300_LANG=tr\nMU300_STORAGE=internal\nMU300_MODE=update\nMU300_BOOT=linux\nMU300_BOOT_ATTEMPTS=3\nMU300_HOTSPOT=no\nMU300_GPU=yes\nMU300_ADB_KEY=QAAAA= web\n");
  assert.equal(answersFor(c, 1, ctx), "MU300_LANG=tr\nMU300_STORAGE=internal\nMU300_MODE=update\nMU300_BOOT_OS=openwrt-luci\nMU300_BOOT=linux\nMU300_BOOT_ATTEMPTS=3\nMU300_HOTSPOT=no\nMU300_GPU=yes\nMU300_ADB_KEY=QAAAA= web\n");
  assert.ok(!answersFor(c, 0, ctx).includes("never in this file"));
  // the first system is the one that boots: named in both runs
  assert.ok(answersFor(choices(), 0, { ...ctx, adbKeyLine: "" }).includes("MU300_BOOT_OS=ubuntu\n"));
  assert.ok(!answersFor(choices(), 0, { ...ctx, adbKeyLine: "" }).includes("MU300_ADB_KEY"));
  assert.ok(answersFor(choices(), 0, { ...ctx, lang: "de" }).startsWith("MU300_LANG=en\n"));
});

test("answers: what erases is said once, in the first run, and only when confirmed", () => {
  const card = status({ sd: { dev: "/dev/block/mmcblk1p1", bytes: 32 * GIB, small: 0, existing: "no" } });
  const cctx = { lang: "en", status: card, analysis: analyseStorage(card) };
  const sd = choices({ target: "sd", eraseConfirmed: true });
  assert.ok(answersFor(sd, 0, cctx).includes("MU300_STORAGE=sd\nMU300_MODE=update\nMU300_SD_ERASE=yes\n"));
  assert.ok(!answersFor(sd, 1, cctx).includes("ERASE"));
  assert.ok(!answersFor(choices({ target: "sd" }), 0, cctx).includes("ERASE"));
  const dirty = status({ region: { off: 27762098176, size: 30 * GIB, existing: "no", dirty: 2 } });
  const dctx = { lang: "en", status: dirty, analysis: analyseStorage(dirty) };
  assert.ok(answersFor(choices({ overwriteConfirmed: true }), 0, dctx).includes("MU300_REGION_OVERWRITE=yes\n"));
  assert.ok(!answersFor(choices({ overwriteConfirmed: true }), 1, dctx).includes("OVERWRITE"));
  assert.ok(!answersFor(choices(), 0, dctx).includes("OVERWRITE"));
  const ex = status({ region: { off: 27762098176, size: 8 * GIB, existing: "yes", dirty: -1 }, linux: { systems: { openwrt: "v1" }, ubuntu: "" } });
  const ectx = { lang: "en", status: ex, analysis: analyseStorage(ex) };
  const wipe = choices({ mode: "wipe" });
  assert.ok(answersFor(wipe, 0, ectx).includes("MU300_MODE=wipe\n"));
  assert.ok(answersFor(wipe, 1, ectx).includes("MU300_MODE=update\n"), "the second run never wipes what the first installed");
  // a kept OpenWrt may boot although this run installs Ubuntu only; after a wipe it is gone and cannot be named
  const keepBoot = choices({ systems: ["ubuntu-24.04"], bootOs: "openwrt" });
  assert.ok(answersFor(keepBoot, 0, ectx).includes("MU300_BOOT_OS=openwrt\n"));
  assert.ok(!answersFor({ ...keepBoot, mode: "wipe" }, 0, ectx).includes("MU300_BOOT_OS"));
});

test("the installer's lines", () => {
  assert.deepEqual(classifyLine("MU300-STEP 6/9 systems"), { type: "step", n: 6, of: 9, name: "systems" });
  assert.deepEqual(classifyLine("MU300-PLAN have=ubuntu openwrt"), { type: "plan", key: "have", value: "ubuntu openwrt" });
  assert.deepEqual(classifyLine("MU300-PROGRESS 512 32768"), { type: "progress", done: 512, total: 32768 });
  assert.deepEqual(classifyLine("MU300-WEB-EXIT 0"), { type: "exit", rc: 0 });
  assert.deepEqual(classifyLine("MU300-WEB-EXIT interrupted"), { type: "exit", rc: "interrupted" });
  assert.deepEqual(classifyLine("! boot_b did not verify"), { type: "error", text: "boot_b did not verify" });
  assert.deepEqual(classifyLine("- Installing openwrt"), { type: "text", text: "- Installing openwrt" });
  // a line that only looks like one of them is text
  assert.equal(classifyLine("MU300-STEP 6/9 systems; rm -rf /").type, "text");
  assert.equal(classifyLine(" MU300-STEP 6/9 systems").type, "text");
  assert.deepEqual(parsePlan(["x", "MU300-PLAN tag=v1", "MU300-PLAN extras=", "MU300-PLAN bad key=1"]), { tag: "v1", extras: "" });
  assert.deepEqual(["check", "image", "systems", "boot", "arm", "done", "what"].map(stageRisk), ["nothing", "nothing", "systems", "boot", "arm", "done", "nothing"]);
  assert.equal(needFor(["ubuntu"]), NEED.ubuntu);
  assert.equal(needFor(["openwrt-luci"]), NEED.openwrt);
  assert.equal(needFor(["openwrt", "ubuntu"]), NEED.both);
  assert.equal(dataNeeded(100 * MIB, 40 * MIB), 792 * MIB);
  assert.equal(fmtSize(32 * GIB), "32.0 GiB");
  assert.equal(fmtSize(700 * MIB), "700 MiB");
  assert.equal(fmtSize(2048), "2 KiB");
});
