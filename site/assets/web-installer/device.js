// A device behind an adb connection, as the page uses it. Two kinds, behind one small set of calls each:
//
//   AndroidDevice  rooted Android: everything goes through tools/android-web.sh (pushed to the device first), run
//                  under su, which answers in JSON and marked lines
//   LinuxDevice    MU300 Linux with adb on: one adapter around "run a command over adb shell, get JSON"
//                  (mu300-device --json, docs/ADB-INTERFACE.md, where the image has it; the facts it does not
//                  carry, and all of them on an older image, from the commands every image has)
//
// identify(conn) tells which one a connection is. Nothing here touches the DOM; tests/web drives both kinds against
// a scripted device.
import { AdbError } from "./adb/errors.js";
import { shell } from "./adb/shell.js";
import { push } from "./adb/sync.js";
import { classifyLine } from "./flow.js";
import { mask as pageMask } from "./privacy.js";
import { Sha256, sha256Hex } from "./sha256.js";

export const STAGE = "/data/local/tmp/mu300-web";
export const DEVICE_FILES = ["android-web.sh", "storage.sh", "android-boot-image.sh", "android-mount-mu300root.sh", "switch.sh"];

export class DeviceError extends Error {
  // code: a word the page has a sentence for (the script's MU300-WEB-ERROR codes among them)
  constructor(code, detail) { super(detail ? `${code}: ${detail}` : code); this.name = "DeviceError"; this.code = code; this.detail = detail || ""; }
}

// What may be an argument of the device script: a plain word (a command, a checksum, a release file's name). No
// blank, quote, slash, $ or backslash can be in it, and no "..": nothing the device's shell would read as anything
// but one word, and nothing that names a place outside the directory the script puts it in.
const ARG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
export const safeArg = (a) => typeof a === "string" && ARG.test(a) && !a.includes("..");
// The files the page ever writes on the device, all in its own directory under /data/local/tmp: the device scripts
// (DEVICE_FILES), the answers, a Magisk zip of a release, an extra under a fixed name. Remote paths are built from
// these and from nothing a device or a listing said.
const ZIP_NAME = /^mu300-magisk-v[0-9][A-Za-z0-9._-]{0,80}\.zip$/;
const EXTRA_FILE = { vpn: "extra-vpn.tar.gz", panel: "extra-panel.tar.gz" };
// ... and the files it ever reads back: the copies android-web.sh makes for a backup, by their exact names
const BACKUP_FILE = /^(?:(?:boot|uboot|sml|teecfg|trustos|vbmeta)_[ab]\.img|misc-head\.bin|gpt-head\.bin|gpt-tail\.bin|partitions\.txt)$/;
const lines = (s) => s.split("\n").map((l) => l.replace(/\r+$/, ""));
// why the device's own download of a release file failed: the exit status of android-web.sh's fetch job
export const FETCH_FAILURES = { 10: "fetch-no-network", 11: "fetch-host", 12: "download-failed", 13: "fetch-size", 14: "fetch-space" };

// ---- which kind of device is this?

// identify(conn) -> { kind: "android" | "linux" | "other", props }
// One unprivileged command. Android: getprop exists. Our Linux: /etc/mu300 does.
export async function identify(conn) {
  const r = await shell(conn, "if [ -d /etc/mu300 ] || [ -x /opt/mu300/bin/mu300-device ]; then echo KIND=linux; " +
    "elif command -v getprop >/dev/null 2>&1; then echo KIND=android; else echo KIND=other; fi", { timeoutMs: 15000 });
  const m = /KIND=(linux|android|other)/.exec(r.stdout);
  return { kind: m ? m[1] : "other" };
}

// ---- Android

// What Android says without root: who the device is, whether its bootloader says it is unlocked, whether there is a
// su at all. These are properties any adb user may read; none of them is a serial number or an identity of the SIM.
const PROPS = ["ro.product.model", "ro.product.device", "ro.build.version.release", "ro.build.display.id", "ro.boot.slot_suffix",
  "ro.boot.verifiedbootstate", "ro.boot.flash.locked", "ro.boot.vbmeta.device_state", "ro.boot.veritymode", "sys.boot_completed"];

export function lockState(props) {
  // "unlocked" | "locked" | "unknown", from what the bootloader told Android. Unisoc bootloaders do not all set
  // these (FINDINGS 72): unknown is an answer.
  const vb = props["ro.boot.verifiedbootstate"], fl = props["ro.boot.flash.locked"], ds = props["ro.boot.vbmeta.device_state"];
  if (ds === "unlocked" || fl === "0" || vb === "orange") return "unlocked";
  if (ds === "locked" || fl === "1" || vb === "green" || vb === "yellow") return "locked";
  return "unknown";
}

export class AndroidDevice {
  constructor(conn, options = {}) {
    this.conn = conn;
    this.kind = "android";
    // every line a device command prints, for the log - masked here, before anyone gets it (privacy.js)
    this.mask = options.mask || pageMask;
    const log = options.log || (() => {});
    this.log = (l, isErr) => log(this.mask(l), isErr);
    this.staged = false;
  }

  // { props, lock, su: boolean }
  async probe() {
    const cmd = PROPS.map((p) => `echo "${p}=$(getprop ${p})"`).join("; ") + "; command -v su >/dev/null 2>&1 && echo SU=1 || echo SU=0";
    const r = await shell(this.conn, cmd, { timeoutMs: 20000 });
    const props = {};
    let su = false;
    for (const l of lines(r.stdout)) {
      const eq = l.indexOf("=");
      if (eq <= 0) continue;
      if (l.startsWith("SU=")) su = l === "SU=1"; else props[l.slice(0, eq)] = l.slice(eq + 1);
    }
    return { props, lock: lockState(props), su };
  }

  // Is root granted to adb? The first call may wait for the user to answer the root manager's prompt on the device.
  // -> "granted" | "denied" | "no-su"
  async root(timeoutMs = 60000) {
    let r;
    try {
      r = await shell(this.conn, "su -c 'id -u' 2>&1", { timeoutMs });
    } catch (e) {
      if (e instanceof AdbError && e.code === "timeout") return "denied";
      throw e;
    }
    const out = r.stdout.trim().split(/\s+/).pop() || "";
    if (out === "0") return "granted";
    if (/not found|inaccessible|No such file/i.test(r.stdout + r.stderr)) return "no-su";
    return "denied";
  }

  // Pushes the device scripts. `fetchFile(name)` returns the bytes of device/NAME as the site serves them; they are
  // checked against the site's SHA256SUMS before they go to the device.
  async stage(fetchFile) {
    const sums = new Map();
    for (const l of new TextDecoder().decode(await fetchFile("SHA256SUMS")).split("\n")) {
      const m = /^([0-9a-f]{64}) [ *](\S+)$/.exec(l.trim());
      if (m) sums.set(m[2], m[1]);
    }
    const files = [];
    for (const name of DEVICE_FILES) {
      const data = await fetchFile(name);
      if (!sums.has(name) || (await sha256Hex(data)) !== sums.get(name)) throw new DeviceError("site-files", name);
      files.push([name, data]);
    }
    const r = await shell(this.conn, `mkdir -p ${STAGE}/in && chmod 755 ${STAGE} && echo STAGE-OK`, { timeoutMs: 20000 });
    if (!r.stdout.includes("STAGE-OK")) throw new DeviceError("no-stage", this.mask((r.stdout + r.stderr).trim()).slice(0, 200));
    for (const [name, data] of files) await push(this.conn, data, `${STAGE}/${name}`, { mode: 0o644 });
    this.staged = true;
  }

  // Runs one command of android-web.sh under su. Returns { rc, out: [lines], marks: { NAME: [rest...] } }; a
  // MU300-WEB-ERROR line becomes a DeviceError. onLine gets every line as it comes.
  async web(args, options = {}) {
    for (const a of args) if (!safeArg(a)) throw new DeviceError("bad-argument", this.mask(String(a)).slice(0, 40));
    const cmd = `su -c 'sh ${STAGE}/android-web.sh ${args.join(" ")}'`;
    const out = [], marks = {};
    // marks and events are read from the line as it came (a checksum or a size must not be changed by the masker);
    // what is kept for showing (out, the log) is masked. options.onEvent gets flow.js's classification with its
    // text masked; nobody outside this function sees a raw line.
    const take = (l, isErr) => {
      out.push(this.mask(l));
      this.log(l, isErr);
      const m = /^MU300-WEB-([A-Z]+) ?(.*)$/.exec(l);
      if (m) (marks[m[1]] = marks[m[1]] || []).push(m[2]);
      if (options.onEvent) {
        const ev = classifyLine(l);
        if (ev.text !== undefined) ev.text = this.mask(ev.text);
        if (ev.type === "plan") ev.value = this.mask(ev.value);
        options.onEvent(ev, l.startsWith("MU300-WEB-"));
      }
    };
    const r = await shell(this.conn, cmd, { onLine: take, stdin: options.stdin, signal: options.signal, timeoutMs: options.timeoutMs,
      onStdout: () => {}, onStderr: () => {} });
    if (marks.ERROR) {
      const [code, ...rest] = marks.ERROR[0].split(" ");
      throw new DeviceError(/^[a-z-]{1,40}$/.test(code) ? code : "device", this.mask(rest.join(" ")).slice(0, 200));
    }
    return { rc: r.exitCode, out, marks };
  }

  async status(deep = false) {
    const r = await this.web(deep ? ["status", "deep"] : ["status"], { timeoutMs: 120000 });
    if (!r.marks.STATUS) throw new DeviceError("no-answer", r.out.slice(-3).join(" | "));
    let s;
    try { s = JSON.parse(r.marks.STATUS[0]); } catch (e) { throw new DeviceError("bad-answer", "status"); }
    if (!s || s.v !== 1 || typeof s.region !== "object" || !Array.isArray(s.parts) || typeof s.sd !== "object" || typeof s.job !== "object") {
      throw new DeviceError("bad-answer", "status");
    }
    return s;
  }

  // Pushes a Magisk zip and has the device check it against the release's checksum. -> the zip's manifest as the
  // device read it: { name, web, tag, system, os, ubuntu, kernel }
  async takeZip(file, name, sha256, options = {}) {
    if (typeof name !== "string" || !ZIP_NAME.test(name) || name.includes("..")) throw new DeviceError("bad-argument", "zip name");
    if (!/^[0-9a-f]{64}$/.test(sha256 || "")) throw new DeviceError("bad-argument", "checksum");
    await push(this.conn, file, `${STAGE}/in/${name}`, { mode: 0o644, onProgress: options.onProgress, signal: options.signal });
    const r = await this.web(["take", name, sha256], { timeoutMs: 600000 });
    if (!r.marks.ZIP) throw new DeviceError("no-answer", "take");
    try { return JSON.parse(r.marks.ZIP[0]); } catch (e) { throw new DeviceError("bad-answer", "take"); }
  }

  // Can the device download from GitHub right now, and over what? `name`: the release zip that would be asked for
  // (its headers are requested), or nothing for github.com itself.
  // -> { ok, why: "" | "no-network" | "host" | "tool" | "failed", uplink: "mobile" | "wifi" | "ethernet" | "other" | "none", detail }
  async net(name) {
    if (name !== undefined && (typeof name !== "string" || !ZIP_NAME.test(name) || name.includes(".."))) throw new DeviceError("bad-argument", "zip name");
    const r = await this.web(name ? ["net", name] : ["net"], { timeoutMs: 60000 });
    if (!r.marks.NET) throw new DeviceError("no-answer", "net");
    let n;
    try { n = JSON.parse(r.marks.NET[0]); } catch (e) { throw new DeviceError("bad-answer", "net"); }
    if (!n || typeof n !== "object") throw new DeviceError("bad-answer", "net");
    return { ok: n.ok === 1, why: ["no-network", "host", "tool", "failed"].includes(n.why) ? n.why : "",
      uplink: ["mobile", "wifi", "ethernet", "other"].includes(n.uplink) ? n.uplink : "none", detail: this.mask(String(n.detail || "")).slice(0, 160) };
  }

  // The device downloads a release zip itself (android-web.sh makes the address from the name; it uses the device's
  // own internet connection), then the same check as for a pushed one: the release's SHA-256, on the device, of the
  // file only root can change. onProgress(done, total). An aborted signal cancels the download on the device; what
  // has arrived stays there and the next call continues it.
  async fetchZip(name, size, sha256, onProgress = () => {}, signal) {
    if (typeof name !== "string" || !ZIP_NAME.test(name) || name.includes("..")) throw new DeviceError("bad-argument", "zip name");
    if (!/^[0-9a-f]{64}$/.test(sha256 || "") || !Number.isSafeInteger(size) || size <= 0) throw new DeviceError("bad-argument", "checksum");
    await this.web(["run", "fetch", name, String(size)], { timeoutMs: 60000 });
    const errors = [];
    let rc;
    try {
      rc = await this.follow((e) => { if (e.type === "progress") onProgress(Math.min(e.done, size), size); else if (e.type === "error") errors.push(e.text); }, signal);
    } catch (e) {
      if (e instanceof AdbError && e.code === "aborted") { try { await this.cancel(); } catch (e2) { /* it ended by itself */ } }
      throw e;
    }
    if (rc !== 0) throw new DeviceError(FETCH_FAILURES[rc] || "download-failed", errors.join(" "));
    const r = await this.web(["take", name, sha256], { timeoutMs: 600000 });
    if (!r.marks.ZIP) throw new DeviceError("no-answer", "take");
    try { return JSON.parse(r.marks.ZIP[0]); } catch (e) { throw new DeviceError("bad-answer", "take"); }
  }

  // an extra goes to the device under a name of the page's own, whatever the picked file is called
  async addExtra(kind, file, sha256, options = {}) {
    const name = Object.prototype.hasOwnProperty.call(EXTRA_FILE, kind) ? EXTRA_FILE[kind] : null;
    if (!name) throw new DeviceError("bad-argument", "extra");
    if (!/^[0-9a-f]{64}$/.test(sha256 || "")) throw new DeviceError("bad-argument", "checksum");
    await push(this.conn, file, `${STAGE}/in/${name}`, { mode: 0o644, onProgress: options.onProgress, signal: options.signal });
    await this.web(["extra", kind, name, sha256], { timeoutMs: 600000 });
  }

  // The answers of the next run. The password, when there is one, travels on the stdin of a root command.
  async setAnswers(textValue, password) {
    await push(this.conn, new TextEncoder().encode(textValue), `${STAGE}/in/answers.conf`, { mode: 0o600 });
    await this.web(["answers"], { timeoutMs: 30000 });
    if (password) await this.web(["password"], { stdin: password + "\n", timeoutMs: 30000 });
  }

  async start(kind, ...args) { await this.web(["run", kind, ...args], { timeoutMs: 60000 }); }

  // Follows the running (or finished) job from its first line. onEvent gets classifyLine()'s objects. Resolves with
  // the job's exit status (a number, or "interrupted"); rejects when the connection goes - the job itself goes on.
  async follow(onEvent, signal) {
    let rc = null;
    await this.web(["follow"], { signal, onEvent: (ev, own) => {
      if (ev.type === "exit") rc = ev.rc;
      else if (!own) onEvent(ev);
    } });
    if (rc === null) throw new DeviceError("no-answer", "follow");
    return rc;
  }

  async cancel() { await this.web(["cancel"], { timeoutMs: 30000 }); }
  // Root removes what is in root's directory. What the page pushed into its own directory (the adb user's) the
  // page removes itself, without root: root never deletes anything where someone else can put a name.
  async clean(all = false) {
    await this.web(all ? ["clean", "all"] : ["clean"], { timeoutMs: 120000 });
    try { await shell(this.conn, `rm -rf ${STAGE}/in ${STAGE}/out`, { timeoutMs: 30000 }); } catch (e) { if (!(e instanceof AdbError) || e.code !== "timeout") throw e; }
  }
  // One copy of the backup out of root's directory: "cat NAME" prints it as base64 (the adb user cannot read that
  // directory, so adb's file service cannot fetch it; and root puts nothing where the adb user can write). The
  // text is decoded as it comes; `sink(bytes)` is awaited for every piece. -> the number of bytes
  async _cat(name, sink, options = {}) {
    if (!BACKUP_FILE.test(name)) throw new DeviceError("bad-argument", "backup file");
    let text = "", got = 0, head = "", odd = false;
    let chain = Promise.resolve(), failed = null;
    const feed = (t) => {
      if (head.length < 200) head += t.slice(0, 200 - head.length);
      const clean = t.replace(/[\r\n]/g, "");
      // anything that is not base64 is not the file: the script's refusal (a marked line), a shell's complaint
      if (/[^A-Za-z0-9+/=]/.test(clean)) odd = true;
      if (odd || failed) return;
      text += clean;
      const n = text.length - (text.length % 4);
      if (!n) return;
      const part = text.slice(0, n); text = text.slice(n);
      let bytes;
      try { bytes = Uint8Array.from(atob(part), (c) => c.charCodeAt(0)); } catch (e) { failed = new DeviceError("bad-answer", "backup"); return; }
      got += bytes.length;
      if (options.expectedSize !== undefined && got > options.expectedSize) { failed = new DeviceError("bad-answer", "backup"); return; }
      const at = got;
      chain = chain.then(() => sink(bytes)).then(() => { if (options.onProgress) options.onProgress(at); }, (e) => { failed = failed || e; });
    };
    const r = await shell(this.conn, `su -c 'sh ${STAGE}/android-web.sh cat ${name}'`, { onStdout: feed, onStderr: () => {}, signal: options.signal, timeoutMs: 900000 });
    await chain;
    const refused = /MU300-WEB-ERROR ([a-z-]{1,40})/.exec(head);
    if (refused) throw new DeviceError(refused[1]);
    if (failed) throw failed;
    if (odd || text || r.exitCode !== 0) throw new DeviceError("bad-answer", "backup");
    if (options.expectedSize !== undefined && got !== options.expectedSize) throw new DeviceError("bad-answer", "backup");
    return got;
  }

  // Arms Linux for one boot and restarts. The connection ends with the device: that is the success.
  async bootLinux() {
    try {
      const r = await this.web(["boot-linux"], { timeoutMs: 60000 });
      return r.out;                       // masked lines
    } catch (e) {
      if (e instanceof AdbError && (e.code === "disconnected" || e.code === "closed")) return [];
      throw e;
    }
  }

  // Copies of what an installation writes, read out of root's directory and checked. `open(name, size)` returns a sink
  // { write(chunk), close(), abort() } for one file (a file in a folder the user chose, or memory).
  // -> { files: [{ name, size, sha256 }], androidSlot, linuxSlot }
  async backup(open, onProgress = () => {}, signal) {
    const r = await this.web(["backup"], { timeoutMs: 900000, signal });
    const files = (r.marks.FILE || []).map((l) => {
      const [name, size, sha256] = l.split(" ");
      // the name becomes part of a path on the device and a file name on this computer: only the names the device
      // script gives its copies, a size in digits (at most 1 GiB: no partition it copies is larger), a checksum
      if (!BACKUP_FILE.test(name || "") || !/^\d{1,10}$/.test(size || "") || Number(size) > 1073741824 || !/^[0-9a-f]{64}$/.test(sha256 || "")) {
        throw new DeviceError("bad-answer", "backup");
      }
      return { name, size: Number(size), sha256 };
    });
    if (new Set(files.map((f) => f.name)).size !== files.length) throw new DeviceError("bad-answer", "backup");
    if (!files.length || !r.marks.BACKUP) throw new DeviceError("no-answer", "backup");
    const total = files.reduce((a, f) => a + f.size, 0);
    let before = 0;
    try {
      for (const f of files) {
        const sink = await open(f.name, f.size);
        const h = new Sha256();
        try {
          await this._cat(f.name, async (c) => { h.update(c); await sink.write(c); },
            { expectedSize: f.size, signal, onProgress: (got) => onProgress(before + got, total, f.name) });
          if (h.hex() !== f.sha256) throw new DeviceError("backup-mismatch", f.name);
          await sink.close();
        } catch (e) {
          try { await sink.abort(); } catch (e2) { /* nothing to undo */ }
          throw e;
        }
        before += f.size;
      }
    } finally {
      // the copies on the device go, whether the pull worked or not (not when the device is gone)
      try { await this.clean(); } catch (e) { /* the next clean takes them */ }
    }
    const m = /android_slot=([ab]) linux_slot=([ab])/.exec(r.marks.BACKUP[0]) || [];
    return { files, androidSlot: m[1] || "", linuxSlot: m[2] || "" };
  }

  async reboot() {
    try {
      const s = await this.conn.open("reboot:");
      await s.close();
    } catch (e) {
      if (!(e instanceof AdbError)) throw e;
    }
  }
}

// ---- MU300 Linux

// The facts the manage view shows, when the image has no "mu300-device --json" yet: one line each, KEY=VALUE, from
// commands and files every image has. Never anything of the modem or the SIM.
const LINUX_FACTS = [
  'echo "device=$(/opt/mu300/bin/mu300-device 2>/dev/null)"',
  'echo "version=$(head -n1 /etc/mu300/image-version 2>/dev/null)"',
  'echo "os=$(. /etc/os-release 2>/dev/null; echo "$ID $VERSION_ID")"',
  'echo "kernel=$(uname -r)"',
  'echo "kernel_choice=$(head -n1 /mnt/mu300-disk/boot/kernel 2>/dev/null)"',
  'echo "boot_os=$(head -n1 /mnt/mu300-disk/.mu300/boot-os 2>/dev/null)"',
  'echo "default_boot=$(head -n1 /etc/mu300/default-boot 2>/dev/null)"',
  'echo "attempts=$(head -n1 /mnt/mu300-disk/.mu300/boot-attempts 2>/dev/null)"',
  'echo "systems=$(for d in ubuntu openwrt openwrt-luci; do [ -d /mnt/mu300-disk/$d/etc ] && printf "%s " $d; done)"',
  'echo "uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)"',
  'echo "root=$(id -u)"',
].join("; ");

const WORD = /^[A-Za-z0-9._ -]{0,80}$/;

export function normalizeLinuxInfo(o, source) {
  const s = (v) => (typeof v === "string" && WORD.test(v) ? v.trim() : typeof v === "number" ? String(v) : "");
  const systems = Array.isArray(o.systems) ? o.systems.map(s) : s(o.systems).split(/\s+/);
  return {
    source,                                  // "json" (mu300-device --json) or "commands"
    device: ["f50", "u30air", "v50"].includes(s(o.device)) ? s(o.device) : "",
    version: s(o.version), os: s(o.os), kernel: s(o.kernel), kernelChoice: s(o.kernel_choice),
    bootOs: s(o.boot_os), defaultBoot: s(o.default_boot) === "linux" ? "linux" : "android",
    attempts: /^[1-6]$/.test(s(o.attempts)) ? Number(s(o.attempts)) : 5,
    systems: systems.filter((x) => ["ubuntu", "openwrt", "openwrt-luci"].includes(x)),
    uptime: /^\d+$/.test(s(o.uptime)) ? Number(s(o.uptime)) : 0,
    isRoot: o.root === true || s(o.root) === "0" || o.root === 0,
    // what adb does there: off, open (every computer gets in), keys (authorised keys only), unavailable; "" unknown
    adb: ["off", "open", "keys", "unavailable"].includes(s(o.adb)) ? s(o.adb) : "",
  };
}

export class LinuxDevice {
  constructor(conn, options = {}) {
    this.conn = conn;
    this.kind = "linux";
    this.mask = options.mask || pageMask;
    const log = options.log || (() => {});
    this.log = (l, isErr) => log(this.mask(l), isErr);
  }

  // _run(command): one command over adb shell, with /opt/mu300/bin on the PATH. The commands are this file's own
  // (fixed text, arguments from short lists); nothing a device or a listing said is ever part of one. Every line
  // goes to the log and to onLine masked. -> { rc, stdout }: stdout is what the command printed, for info() to
  // read facts from, and is not for showing.
  async _run(command, options = {}) {
    const take = (l, isErr) => { this.log(l, isErr); if (options.onLine) options.onLine(this.mask(l), isErr); };
    const r = await shell(this.conn, `export PATH="$PATH:/opt/mu300/bin:/usr/sbin:/sbin"; ${command}`,
      { onLine: take, timeoutMs: options.timeoutMs, signal: options.signal });
    return { rc: r.exitCode, stdout: r.stdout };
  }
  // the same for the actions: only the exit status comes back, the text went out masked
  async run(command, options = {}) { return { rc: (await this._run(command, options)).rc }; }

  // The device's own description. An image with "mu300-device --json" (format 1, docs/ADB-INTERFACE.md) says who
  // it is, what runs, and what adb does; an older one prints the device's name for any argument. What that JSON
  // does not carry (the kernel chosen, the system that boots, the systems installed) is read one by one, on both.
  async info() {
    const j = await this._run("mu300-device --json 2>/dev/null", { timeoutMs: 20000 });
    let own = null;
    const t = j.stdout.trim();
    if (t.startsWith("{")) {
      try {
        const o = JSON.parse(t);
        if (o && typeof o === "object" && o.mu300 === 1) {
          own = { device: o.device, version: o.release, kernel: o.kernel, root: o.root === true ? 0 : 1, adb: o.adb,
            os: [o.system, o.version_id].filter((x) => typeof x === "string" && x).join(" ") };
        }
      } catch (e) { /* not JSON after all: the commands */ }
    }
    const r = await this._run(LINUX_FACTS, { timeoutMs: 20000 });
    const o = {};
    for (const l of lines(r.stdout)) {
      const eq = l.indexOf("=");
      if (eq > 0) o[l.slice(0, eq)] = l.slice(eq + 1);
    }
    return normalizeLinuxInfo({ ...o, ...(own || {}) }, own ? "json" : "commands");
  }

  // The actions of the manage view. Each is one command of the device's own tools; its output goes to onLine and
  // to the log, its exit status is the answer.
  checkUpdate(onLine) { return this.run("mu300-update check", { onLine, timeoutMs: 180000 }); }
  applyUpdate(onLine, signal) { return this.run("mu300-update apply", { onLine, signal }); }
  setKernel(k, onLine, signal) {
    if (!["5.4", "6.18", "7.2", "7.3"].includes(k)) throw new DeviceError("bad-argument", k);
    return this.run(`mu300-update kernel ${k}`, { onLine, signal });
  }
  setBootOs(name, onLine) {
    if (!["ubuntu", "openwrt", "openwrt-luci"].includes(name)) throw new DeviceError("bad-argument", name);
    return this.run(`mu300-os ${name}`, { onLine, timeoutMs: 60000 });
  }
  nextBoot(target, onLine) {
    if (!["android", "linux"].includes(target)) throw new DeviceError("bad-argument", target);
    return this.run(`mu300-next-boot ${target}`, { onLine, timeoutMs: 60000 });
  }
  // the kernel log and the log of this boot: what a bug report needs. The page masks it before it shows or saves it.
  logs(onLine) {
    return this.run("echo '--- mu300-update version'; mu300-update version 2>&1; echo '--- dmesg (last 300 lines)'; dmesg 2>/dev/null | tail -n 300; " +
      "echo '--- system log (last 200 lines)'; if command -v journalctl >/dev/null 2>&1; then journalctl -b -n 200 --no-pager 2>/dev/null; " +
      "else logread 2>/dev/null | tail -n 200; fi", { onLine, timeoutMs: 120000 });
  }
  async reboot() {
    try { await this.run("sync; reboot", { timeoutMs: 20000 }); } catch (e) { if (!(e instanceof AdbError)) throw e; }
  }
  // Android next, then a restart: the way back to Android without a terminal
  async toAndroid(onLine) {
    const r = await this.nextBoot("android", onLine);
    if (r.rc !== 0) return r;
    await this.reboot();
    return r;
  }

  // ---- mu300-api (FINDINGS 110, docs/ADB-INTERFACE.md): the device manager's one way in.
  // hello() -> its answer ({ api, system, device, methods, features, job }), or null when this release has no
  // mu300-api (the page then offers what the small manage view above does, and says to update).
  async hello() {
    const r = await shell(this.conn, `[ -x ${API_PATH} ] && ${API_PATH} hello || echo MU300-API-NONE`, { stdin: "{}", timeoutMs: 30000 });
    if (/MU300-API-NONE/.test(r.stdout)) return null;
    const o = parseApiAnswer(r.stdout);
    if (!o || o.ok !== 1 || typeof o.api !== "number" || !Array.isArray(o.methods)) return null;
    this.apiMethods = new Set(o.methods.filter((m) => API_METHOD.test(m)));
    return o;
  }

  // api(method, request) -> the answer's object ({ ok: 1, ... }); a refusal is a DeviceError "api-CODE" whose detail is
  // the device's (masked). The method is one of the page's fixed list and one the device said it has; the request
  // goes on the command's stdin as JSON, never into the command line, and is never logged: it may hold a password.
  // The log gets the method and how it ended, nothing else.
  async api(method, request = {}, options = {}) {
    if (!API_METHODS.has(method) || !API_METHOD.test(method)) throw new DeviceError("bad-argument", method);
    if (this.apiMethods && !this.apiMethods.has(method)) throw new DeviceError("api-unknown-method", method);
    if (!request || typeof request !== "object" || Array.isArray(request)) throw new DeviceError("bad-argument", method);
    const r = await shell(this.conn, `${API_PATH} ${method}`, { stdin: JSON.stringify(request), timeoutMs: options.timeoutMs || 90000, signal: options.signal });
    const o = parseApiAnswer(r.stdout);
    if (!o) { this.log(`# mu300-api ${method}: no answer`, true); throw new DeviceError("bad-answer", method); }
    if (o.ok !== 1) {
      const code = typeof o.error === "string" && /^[a-z-]{1,30}$/.test(o.error) ? o.error : "failed";
      const detail = this.mask(typeof o.detail === "string" ? o.detail : "").slice(0, 200);
      this.log(`# mu300-api ${method}: ${code}${detail ? " (" + detail + ")" : ""}`, true);
      throw new DeviceError("api-" + code, detail);
    }
    if (!options.quiet) this.log(`# mu300-api ${method}: ok`);
    return o;
  }
}

export const API_PATH = "/opt/mu300/bin/mu300-api";
const API_METHOD = /^[a-z][a-z0-9_.]{0,39}$/;
// the methods this page ever asks for (mu300-api --methods; tests/web/manager.test.mjs holds the two to the same list)
export const API_METHODS = new Set(("hello overview wifi.get wifi.set wifi.power wificlient.status wificlient.scan wificlient.join " +
  "wificlient.leave data.get data.set data.power sim.get sim.select sim.card sim.state voice.get voice.set radio.get radio.set lock.get lock.set vpn.status vpn.profiles vpn.subs " +
  "vpn.settings vpn.service vpn.killswitch vpn.use vpn.add vpn.add_url vpn.rename vpn.remove vpn.setting vpn.sub_add " +
  "vpn.sub_refresh vpn.sub_remove vpn.logs vpn.install vpn.engine_install vpn.engines sms.list sms.read sms.send sms.job sms.delete " +
  "sms.forward sms.forward_set sms.forward_test sms.forward_log_clear lan.get lan.set usb.role usb.net adb.auth system.get system.hostname system.timezone system.password system.ssh " +
  "system.reboot system.boot system.bootos update.check update.apply kernel.set extra.list extra.install extra.remove logs " +
  "backup.export backup.import at.send at.history ussd.send ussd.cancel modem.restart ttl.get ttl.set traffic.get traffic.set power.get " +
  "power.set led.get led.set zram.get zram.set cpu.get cpu.profile cpu.core cpu.lowlatency cpu.voltage update.status update.source packages.get " +
  "packages.act docker.status job.status job.cancel job.clear").split(" "));
// the last line of what mu300-api printed, as an object (it prints exactly one)
export function parseApiAnswer(stdout) {
  const last = String(stdout || "").trim().split("\n").pop() || "";
  if (!last.startsWith("{")) return null;
  try {
    const o = JSON.parse(last);
    return o && typeof o === "object" && !Array.isArray(o) ? o : null;
  } catch (e) { return null; }
}


// A sink for backup() that keeps a file in memory (for the "save" link of browsers without a folder picker)
export function memorySink(store) {
  return (name) => {
    const parts = [];
    return { async write(c) { parts.push(c); }, async close() { store.set(name, new Blob(parts)); }, async abort() {} };
  };
}

// A sink for backup() that writes into a directory the user picked (File System Access API)
export function directorySink(dirHandle) {
  return async (name) => {
    const fh = await dirHandle.getFileHandle(name, { create: true });
    const w = await fh.createWritable();
    return { write: (c) => w.write(c), close: () => w.close(), abort: () => w.abort() };
  };
}
