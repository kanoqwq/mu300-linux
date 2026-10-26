// The page without a device: ?demo=android | installed | small | linux | linux-old (a release without mu300-api,
// FINDINGS 110). Made-up answers behind the same calls as
// device.js, so that the whole wizard can be looked at (and its three languages read) before a device is plugged in.
// Nothing here opens a USB device, and nothing is written anywhere. Loaded only when the address asks for it.
import { GIB } from "./flow.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const TAG = "v2026.11.01";

function asset(repo, tag, name, size) {
  return { name, size, sha256: "0".repeat(64), url: `https://github.com/${repo}/releases/download/${tag}/${name}` };
}
export function demoReleases() {
  const mk = (tag, prerelease) => {
    const assets = new Map();
    const sizes = { openwrt: 45e6, "openwrt-luci": 47e6, "ubuntu-24.04": 120e6, "ubuntu-26.04": 130e6 };
    for (const [s, size] of Object.entries(sizes)) {
      for (const k of ["5.4", "6.18", "7.2", "7.3"]) {
        if (s === "ubuntu-26.04" && k === "5.4") continue;
        const name = `mu300-magisk-${tag}-${s}-k${k}.zip`;
        assets.set(name, asset("dikeckaan/mu300-linux", tag, name, size));
      }
    }
    return { tag, prerelease, published: Date.now(), assets };
  };
  return { stable: mk(TAG, false), beta: mk("v2026.11.04", true) };
}
export function demoVpn() { return { tag: "v2.0.1", ...asset("dikeckaan/mu300-linux-vpn", "v2.0.1", "mu300-linux-vpn.tar.gz", 41946588) }; }

function baseStatus(kind) {
  const small = kind === "small";
  const disk = small ? 61071360 : 122142720;
  const lastEnd = small ? disk - 2048 : 54222848;
  const start = (Math.floor(lastEnd / 4096) + 1) * 4096, end = (Math.floor((disk - 34) / 4096) - 1) * 4096;
  const installed = kind === "installed";
  return {
    v: 1, model: "ZTE F50", product: "mu300", device: "f50", android: "13", build: "MU300_ZYV1.0.0B09", boot_completed: 1, android_slot: "a", linux_slot: "b",
    slots_ok: 1, root_kind: "magisk", magisk: "27.0:MAGISK:R", magiskboot: 1, busybox: "/data/adb/magisk/busybox", misc: { valid: 1, bc: "00" },
    linux_boot: installed ? "linux" : "android-copy", disk, last_end: lastEnd,
    region: { off: start * 512, size: Math.max(0, (end - start) * 512), existing: installed ? "yes" : "no", dirty: installed || small ? -1 : 0 },
    parts: [["boot_a", 2048, 131072], ["boot_b", 133120, 131072], ["super", 400000, 12000000], ["userdata", 14000000, lastEnd - 14000000]],
    sd: small ? { dev: "/dev/block/mmcblk1p1", bytes: 59 * GIB, small: 0, existing: "no" } : { dev: "", bytes: 0, small: 0, existing: "no" },
    data_free: 21 * GIB, stalled: 0, module: installed ? 1 : 0, job: { state: "none", kind: "", arg: "", rc: -1, step: 0 },
    ...(installed ? { linux: { where: "internal", mounted: 1, systems: { ubuntu: "v2026.10.23", "openwrt-luci": "v2026.10.23" }, boot_os: "ubuntu", kernel: "6.18", ubuntu: "24.04", default_linux: 1, attempts: 5 } } : {}),
  };
}

class DemoAndroid {
  constructor(kind, log) { this.kind = "android"; this.variant = kind; this.log = log; this.serial = "324950664950"; this.st = baseStatus(kind); this.job = null; }
  async probe() {
    return { props: { "ro.product.model": "ZTE F50", "ro.product.device": "mu300", "ro.build.version.release": "13", "ro.build.display.id": "MU300_ZYV1.0.0B09", "ro.boot.slot_suffix": "_a" }, lock: this.variant === "installed" ? "locked" : "unknown", su: true };
  }
  async root() { await wait(700); return "granted"; }
  async stage() { await wait(300); }
  async status() { await wait(250); return JSON.parse(JSON.stringify(this.st)); }
  async takeZip(file, name, sha, o = {}) {
    for (let i = 0; i <= 20; i++) { if (o.onProgress) o.onProgress(file.size * i / 20, file.size); await wait(80); }
    this.log(`MU300-WEB-ZIP {"name":"${name}","web":1}`);
    return { name, web: 1 };
  }
  async net() { await wait(600); return this.variant === "small" ? { ok: false, why: "no-network", uplink: "none", detail: "wget: bad address 'github.com'" } : { ok: true, why: "", uplink: "mobile", detail: "" }; }
  async fetchZip(name, size, sha, onProgress = () => {}) {
    for (let i = 0; i <= 30; i++) { onProgress(size * i / 30, size); await wait(120); }
    this.log(`MU300-WEB-ZIP {"name":"${name}","web":1}`);
    return { name, web: 1 };
  }
  async addExtra(kind, file, sha, o = {}) { for (let i = 0; i <= 10; i++) { if (o.onProgress) o.onProgress(file.size * i / 10, file.size); await wait(60); } }
  async setAnswers(text) { this.answers = text; }
  async start(kind, ...args) { this.job = { kind, args }; }
  async follow(onEvent) {
    const say = async (line, ms = 350) => {
      this.log(line);
      const m = /^MU300-STEP (\d+)\/(\d+) (\S+)$/.exec(line);
      const p = /^MU300-PLAN ([a-z_]+)=(.*)$/.exec(line);
      onEvent(m ? { type: "step", n: +m[1], of: +m[2], name: m[3] } : p ? { type: "plan", key: p[1], value: p[2] } : line.startsWith("! ") ? { type: "error", text: line.slice(2) } : { type: "text", text: line });
      await wait(ms);
    };
    const j = this.job, a = this.answers || "";
    const sd = /MU300_STORAGE=sd/.test(a);
    if (j.kind === "uninstall") {
      const names = ["check", "misc", "boot", "erase", "card", "cleanup", "done"];
      for (let i = 0; i < names.length; i++) {
        await say(`MU300-STEP ${i + 1}/7 ${names[i]}`, 500);
        if (names[i] === "erase" && j.args[0] === "secure") for (let d = 1; d <= 8; d++) { onEvent({ type: "progress", done: d * 4096, total: 32768 }); await wait(250); }
      }
      await say("- Done. The device boots Android; restart it once to check.");
      Object.assign(this.st, { linux_boot: "android-copy", module: 0 }); this.st.region.existing = "no"; this.st.region.dirty = 0; delete this.st.linux;
      return 0;
    }
    const system = /-(ubuntu-2[46]\.04|openwrt-luci|openwrt)-k([0-9.]+)\.zip$/.exec(j.args[0]) || [0, "openwrt", "5.4"];
    const os = system[1].startsWith("ubuntu") ? "ubuntu" : system[1];
    await say(`MU300 Linux ${TAG}`);
    await say("MU300-STEP 1/9 check"); await say("- device: ZTE F50 / mu300"); await say("- Android runs from slot a; Linux goes to slot b (boot_b)");
    await say("MU300-STEP 2/9 unpack", 700); await say("MU300-STEP 3/9 plan");
    const have = this.st.linux && !/MU300_MODE=wipe/.test(a) ? Object.keys(this.st.linux.systems).join(" ") : "";
    const format = this.st.region.existing === "yes" && !/MU300_MODE=wipe/.test(a) && !sd ? "0" : this.st.linux && sd ? "0" : "1";
    for (const l of [`tag=${TAG}`, `system=${system[1]}`, `os=${os}`, `ubuntu=${system[1].startsWith("ubuntu") ? system[1].slice(7) : ""}`, `kernel=${system[2]}`, "device=f50",
      `storage=${sd ? "sd" : "internal"}`, `sd_dev=${sd ? "/dev/block/mmcblk1p1" : ""}`, `offset=${this.st.region.off}`, `size=${sd ? this.st.sd.bytes : this.st.region.size}`,
      `format=${this.installedOnce ? "0" : format}`, `update=${this.installedOnce || format === "0" ? "1" : "0"}`, `have=${have}`, `boot_os=${os}`, "default_linux=1", "boot_attempts=5", "hotspot=1", "gpu=yes",
      `password=${this.installedOnce || format === "0" ? "kept" : "image"}`, "extras=", "adb=1", "linux_slot=b", "android_slot=a", "sync_bootchain=1", "bootchain=", `dry_run=${j.kind === "plan" ? 1 : 0}`]) await say("MU300-PLAN " + l, 15);
    if (j.kind === "plan") { await say("- Dry run: nothing was written."); return 3; }
    await say("MU300-STEP 4/9 vendor", 900); await say("MU300-STEP 5/9 image", 900);
    await say("MU300-STEP 6/9 systems"); await say(`[device] installing ${os}`, 2500); await say("MU300-INSTALL-OK");
    await say("MU300-STEP 7/9 boot"); await say("- Writing and verifying boot_b", 1200);
    await say("MU300-STEP 8/9 arm", 600); await say("MU300-STEP 9/9 done"); await say(`- Done. Reboot to start ${os}.`);
    this.installedOnce = true;
    this.st.linux_boot = "linux"; this.st.module = 1;
    if (!sd) this.st.region.existing = "yes";
    const systems = { ...(have ? this.st.linux.systems : {}), [os]: TAG };
    this.st.linux = { where: sd ? "sd" : "internal", mounted: 1, systems, boot_os: os, kernel: system[2], ubuntu: system[1].startsWith("ubuntu") ? system[1].slice(7) : (this.st.linux || {}).ubuntu || "", default_linux: 1, attempts: 5 };
    if (sd) this.st.sd.existing = "yes";
    return 0;
  }
  async cancel() {}
  async clean() {}
  async bootLinux() { await wait(500); return []; }
  async backup(open, onProgress) {
    const files = [["boot_b.img", 4 << 20], ["uboot_b.img", 1 << 20], ["sml_b.img", 1 << 20], ["teecfg_b.img", 1 << 18], ["trustos_b.img", 2 << 20], ["vbmeta_b.img", 1 << 16], ["misc-head.bin", 4096], ["gpt-head.bin", 17408], ["gpt-tail.bin", 16896], ["partitions.txt", 200]];
    const total = files.reduce((x, f) => x + f[1], 0);
    let done = 0;
    const out = [];
    for (const [name, size] of files) {
      const sink = await open(name, size);
      await sink.write(new Uint8Array(size)); await sink.close();
      done += size; onProgress(done, total, name); await wait(150);
      out.push({ name, size, sha256: "0".repeat(64) });
    }
    return { files: out, androidSlot: "a", linuxSlot: "b" };
  }
  async reboot() { await wait(300); }
}

class DemoLinux {
  constructor(log) { this.kind = "linux"; this.log = log; this.serial = "MU300LINUX-324950664950-203d8329"; this.kernel = "6.18"; this.bootOs = "ubuntu"; }
  async info() {
    return { source: "commands", device: "f50", version: "v2026.10.23", os: "ubuntu 24.04", kernel: this.kernel === "6.18" ? "6.18.56-mu300" : this.kernel === "7.2" ? "7.2.10-mu300" : "5.4.254", kernelChoice: this.kernel,
      bootOs: this.bootOs, defaultBoot: "linux", attempts: 5, systems: ["ubuntu", "openwrt-luci"], uptime: 5025, isRoot: true };
  }
  async _say(onLine, lines, rc = 0) { for (const l of lines) { this.log(l); if (onLine) onLine(l); await wait(250); } return { rc }; }
  checkUpdate(onLine) { return this._say(onLine, ["==> installed: v2026.10.23 (ubuntu, openwrt-luci), kernel 6.18", `==> newest release: ${TAG}`, "an update is available: sudo mu300-update apply"]); }
  applyUpdate(onLine) { return this._say(onLine, [`==> downloading ${TAG}`, "==> unpacking ubuntu", "==> kept: etc/mu300 etc/ssh home root", "==> boot image updated; reboot to start it"]); }
  async setKernel(k, onLine) { const r = await this._say(onLine, [`==> kernel ${k}: downloading`, "==> boot image written and verified", "the next boot is a trial"]); this.kernel = k; return r; }
  async setBootOs(n, onLine) { const r = await this._say(onLine, [`boot system: ${n} (reboot to apply)`]); this.bootOs = n; return r; }
  nextBoot(t, onLine) { return this._say(onLine, [`next boot: ${t}`]); }
  async toAndroid(onLine) { return this._say(onLine, ["next boot: android"]); }
  logs(onLine) { return this._say(onLine, ["--- mu300-update version", "v2026.10.23", "--- dmesg (last 300 lines)", "[    3.201] sprd-sdhci 20300000.sdio: card claimed", "[   12.911] wlan0: aa:bb:cc:dd:ee:ff associated", "[   14.002] modem: IMEI 867530012345678 registered"]); }
  async reboot() { await wait(300); }

  // the device manager's mu300-api, made up (?demo=linux; ?demo=linux-old is a release without it)
  async hello() {
    if (this.old) return null;
    const { API_METHODS } = await import("./device.js");
    return { ok: 1, api: 1, system: "ubuntu", device: "f50", release: "v2026.10.23", methods: [...API_METHODS],
      features: { vpn: true, vpn_extra: true, wifi_client: true, sms: "cli", locks: false, forward: false, dual_sim: true, battery: false, usb_role: false, wpa3: false, luci: false },
      job: { kind: "", state: "none" } };
  }
  async api(method, req = {}) {
    await wait(200);
    this.log(`# mu300-api ${method}: ok`);
    const j = this.job;
    const A = {
      overview: { os: "ubuntu 24.04", device: "f50", release: "v2026.10.23", kernel: "6.18.56-mu300", kernel_choice: this.kernel, hostname: "mu300", uptime: 5025,
        memory: { total_kb: 3900000, avail_kb: 2700000 }, storage: { root: null, disk: { size: 20000000, used: 6000000, avail: 14000000 } },
        signal: { operator: "Demo Telekom", rat: "LTE", registration: "home", rsrp: -92, rsrq: -11.5 }, data: { state: "up", iface: "sipa_eth0", address: "10.64.12.7/30" },
        traffic: { cycle: { used: 3221225472 } }, battery: null, temperatures: [{ name: "soc-thmzone", millicelsius: 47250 }],
        hotspot: { ssid: "MU300-DEMO", enabled: true }, vpn: { installed: true }, lan: "192.168.77.1", boot: { default: "linux", os: this.bootOs, systems: ["ubuntu", "openwrt-luci"] } },
      "wifi.get": { enabled: true, client_mode: false, ssid: "MU300-DEMO", band: "5", channel: "auto", channel_live: 36, country: "TR", security: "wpa2", hidden: false, password_set: true,
        channels: { "2.4": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 5: [36, 40, 44, 48] }, clients: [{ mac: "aa:bb:cc:dd:ee:01", ip: "192.168.77.23", name: "laptop" }] },
      "wificlient.status": { active: false, text: "not connected (the hotspot has the radio)" },
      "wificlient.scan": { text: "SIGNAL  SECURITY  NAME\n-48     WPA2      Home\n-71     WPA2/3    Office" },
      "data.get": { state: "up", iface: "sipa_eth0", address: "10.64.12.7/30", apn: "", pdp: "IP", auth: "", user: "", password_set: false,
        signal: { operator: "Demo Telekom", rat: "LTE", registration: "home" }, sim: { active: 1, default: 1, slot1_supported: 1 }, roaming_switch: false },
      "vpn.status": { installed: true, status: { enabled: false, profile: "p-1", profile_name: "Home server", ...(req.exit_ip ? { exit_ip: "203.0.113.7" } : {}) } },
      "vpn.profiles": { profiles: [{ id: "p-1", name: "Home server", type: "vless", active: true }, { id: "p-2", name: "Backup", type: "wireguard" }] },
      "vpn.subs": { subscriptions: [] }, "vpn.settings": { settings: { KILL_SWITCH: "off", IPV6: "auto" } }, "vpn.logs": { log: "xray: started\nxray: 203.0.113.7:443 connected" },
      "sms.list": { data: { store: "SM", messages: [{ index: [1], sender: "+905551112233", time: "26/10/14 09:12", parts: 1, have: 1, text: "Your code is 123456" }] } },
      "lan.get": { ip: "192.168.77.1", default_ip: "192.168.77.1", usb_role: "", usb_role_switch: false, adb: { state: "on", auth: "open", keys: 0 } },
      "system.get": { hostname: "mu300", timezone: "Europe/Istanbul", user: "ubuntu", ssh: true, release: "v2026.10.23", kernel: "6.18.56-mu300", kernel_choice: this.kernel,
        boot: { default: "linux", attempts: 5, locked: false, os: this.bootOs, systems: ["ubuntu", "openwrt-luci"] },
        extras: [{ name: "vpn", installed: true, release: "v2.0.1" }, { name: "panel", installed: false, release: "" }, { name: "lang", installed: false, release: "" }] },
      logs: { text: "--- mu300-update version\nv2026.10.23\n--- dmesg\n[    3.201] sprd-sdhci 20300000.sdio: card claimed" },
      "job.status": { job: j ? { ...j, text: j.lines.slice(req.from || 0, j.at).join("\n"), next: j.at } : { kind: "", state: "none", rc: null, text: "", next: 0, cancel: false } },
    };
    if (j && method === "job.status") {
      j.at = Math.min(j.lines.length, j.at + 1);
      if (j.at === j.lines.length) Object.assign(j, { state: "done", rc: 0 });
    }
    const jobs = { "update.check": ["==> installed: v2026.10.23", `==> newest release: ${TAG}`, "an update is available"], "update.apply": [`==> downloading ${TAG}`, "==> unpacking ubuntu", "==> boot image updated; reboot to start it"],
      "kernel.set": ["==> kernel: downloading", "==> boot image written and verified"], "extra.install": ["==> downloading the extra", "==> installed"], "extra.remove": ["removed"],
      "vpn.install": ["==> downloading the VPN module", "==> installed"], "vpn.engine_install": ["==> engine installed"], "vpn.add_url": ["profile p-3 added"], "vpn.sub_add": ["subscription s-1 added"],
      "vpn.sub_refresh": ["refreshed"], "wificlient.join": ["joining", "connected"], "wificlient.leave": ["the hotspot is back"] };
    if (jobs[method]) {
      this.job = { kind: method.replace(".", "-"), state: "running", rc: null, lines: jobs[method], at: 0, cancel: true, started: Date.now() };
      return { ok: 1, job: this.job.kind };
    }
    if (method === "job.clear") this.job = null;
    if (method === "kernel.set") this.kernel = req.version;
    return { ok: 1, ...(A[method] || {}) };
  }
}

export function makeDemo(kind, log) {
  if (kind === "linux-old") { const d = new DemoLinux(log); d.old = true; return d; }
  return kind === "linux" ? new DemoLinux(log) : new DemoAndroid(kind, log);
}
