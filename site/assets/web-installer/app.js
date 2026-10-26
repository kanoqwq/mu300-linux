// The web installer's screen. It decides nothing about the device: what fits and what is refused is flow.js, what
// the device is asked is device.js, and the device's own installer has the last word. This file turns their answers
// into the page, in the page's language (strings.js), and keeps a masked log of every line the device printed.
//
// Network: the release list from api.github.com, and this site's own files. A release's files are not read from
// GitHub by the page (GitHub does not let a page read them, FINDINGS 72): the device downloads its zip itself, or
// this browser gets it from the project's mirror when config.js names one (mirror.js), or - last - the user
// downloads the file and picks it here. Whichever way, the device checks it against the release's checksum.
import { AdbConnection } from "./adb/connection.js";
import { AdbError } from "./adb/errors.js";
import { fingerprint, publicKeyLine } from "./adb/key.js";
import { ADB_FILTERS, findAdbInterface, openUsbTransport, platformOf } from "./adb/usb.js";
import { MIRROR_BASE } from "./config.js";
import { AndroidDevice, DeviceError, LinuxDevice, directorySink, identify, memorySink } from "./device.js";
import { INSTALL_STEPS, MIB, NEED, UNINSTALL_STEPS, analyseStorage, answersFor, dataNeeded, deviceBlockers, familyOf, fmtSize, osOf,
  parsePlan, stageRisk, targetExisting, targetSize, validateChoices } from "./flow.js";
import { GithubError, combos, findZip, loadReleases, loadVpnModule, releaseSupportsWeb } from "./github.js";
import { loadKey } from "./keystore.js";
import { Manager, renderManager } from "./manager.js";
import { MirrorError, downloadZip, loadMirror } from "./mirror.js";
import { maskSerial, pageMasker as masker } from "./privacy.js";
import { Sha256 } from "./sha256.js";
import { STR } from "./strings.js";
import { ZipError, inspectMagiskZip } from "./zip.js";

const root = document.getElementById("wi-app");
const LANG = (document.documentElement.lang || "en").slice(0, 2);
const L = STR[LANG] || STR.en;
const PARAMS = new URLSearchParams(location.search);
const DEMO = PARAMS.get("demo");
const DEVMODE = PARAMS.has("dev");
const PLATFORM = platformOf();
const TELEGRAM = "https://t.me/mu300project";
const SITE = new URL("../../", import.meta.url);             // the site's root, from assets/web-installer/
const LANG_PREFIX = LANG === "en" ? "" : LANG + "/";
const page = (p) => new URL(LANG_PREFIX + p, SITE).href;

// t("key", a, b): the page's sentence for key, {1} {2} filled in; a missing key shows itself (tests catch those)
function t(key, ...args) {
  let s = L[key] !== undefined ? L[key] : STR.en[key] !== undefined ? STR.en[key] : key;
  args.forEach((a, i) => { s = s.split(`{${i + 1}}`).join(String(a)); });
  return s;
}

// h("div", { class: "x", onclick: fn }, child, ...): an element; strings become text nodes, never markup
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "hidden" || k === "open" || k === "selected") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
// a sentence with one link in it: "text [link text] more" in the string, the brackets mark the link
function withLink(textValue, href, attrs = {}) {
  const m = /^(.*)\[(.+)\](.*)$/s.exec(textValue);
  if (!m) return [textValue];
  return [m[1], h("a", { href, ...attrs }, m[2]), m[3]];
}
const btn = (label, onclick, cls = "", props = {}) => h("button", { type: "button", class: "wi-btn " + cls, onclick, ...props }, label);
const chip = (label, kind) => h("span", { class: "wi-chip " + (kind || "") }, label);
const callout = (kind, title, ...body) => h("div", { class: "callout " + (kind || "") }, title ? h("strong", {}, title) : null, ...body.map((b) => (typeof b === "string" ? h("p", {}, b) : b)));

// ---------------------------------------------------------------------------------------------------- state

const S = {
  supported: !!(navigator.usb && window.isSecureContext),
  phase: "idle",              // idle | connecting | auth | probing | ready | failed
  failure: null,              // { code, platform, detail }
  usb: null,                  // the USBDevice
  conn: null,
  dev: null,                  // AndroidDevice | LinuxDevice (or the demo's)
  kind: "",                   // android | linux | other
  serial: "",
  showSerial: false,
  key: null, keyInfo: null,
  probe: null, rootState: "", status: null, analysis: null, linux: null,
  releases: null, releaseError: null, vpnAsset: null,
  view: "home",               // home | wizard | uninstall | job
  w: null,                    // the wizard's state
  un: { wipe: "secure", sd: "erase", typed: "" },
  job: null,                  // what the job screen shows
  busy: "",                   // a word for what is going on (disables the buttons)
  action: null,               // the manage view's last action: { title, lines, rc, running }
  wake: null,
  granted: [],                // the USB devices this browser was allowed before: connecting needs no chooser
};
// The one way into the log: whatever comes here is masked (privacy.js) before it is kept, shown or saved. The device
// classes mask their lines already; a second pass changes nothing and covers every other caller.
const LOG = [];

function logLine(line, isErr) {
  const m = masker.mask(String(line));
  LOG.push(m);
  if (LOG.length > 20000) LOG.splice(0, LOG.length - 20000);
  const pre = document.getElementById("wi-log-pre");
  if (pre) {
    pre.append(document.createTextNode(m + "\n"));
    const count = document.getElementById("wi-log-count");
    if (count) count.textContent = String(LOG.length);
    if (pre.dataset.follow !== "0") pre.scrollTop = pre.scrollHeight;
  }
}
const note = (text) => logLine("# " + text);

function newWizard() {
  return { step: 0, choices: null, files: {}, vpnFile: null, backup: { state: "none", files: [], where: "" }, plan: null, planLog: [], planError: null,
    typed: "", eraseTyped: "", overwriteTyped: "", run: null, beta: false, unlisted: {},
    // how the release's zips get to the device: "device" (it downloads them itself), "mirror" (this browser
    // downloads them and sends them over USB), "file" (the user's own download, picked here); "" until asked
    source: "", net: null, mirror: null, probing: false, probedFor: "", abort: null };
}

// ---------------------------------------------------------------------------------------------------- connecting

function explainError(e) {
  // the detail may be a device's words (a refused file, a shell's complaint): masked like any other line
  const d = (x) => masker.mask(String(x || "")).slice(0, 300);
  if (e instanceof DeviceError) return { code: "dev." + e.code, detail: d(e.detail) };
  if (e instanceof AdbError) return { code: "adb." + e.code, detail: d(e.message), platform: e.platform || PLATFORM };
  if (e instanceof GithubError) return { code: "gh." + e.code, detail: d(e.message) };
  if (e instanceof ZipError) return { code: "zip." + e.code, detail: d(e.message) };
  if (e instanceof MirrorError) return { code: "mirror." + e.code, detail: d(e.message) };
  if (e && e.name === "NotFoundError") return { code: "usb.none-chosen" };
  if (e && e.name === "SecurityError") return { code: "adb.access", platform: PLATFORM, detail: d(e.message) };
  return { code: "unexpected", detail: d(e && (e.message || String(e))) };
}
// the sentence for an error: the page's own when it has one, else the code and what the device said
function errorText(x) {
  const key = "err." + x.code;
  const known = L[key] !== undefined || STR.en[key] !== undefined;
  if (x.code === "adb.access") return t("err.adb.access." + (["windows", "linux", "mac", "android"].includes(x.platform) ? x.platform : "other"));
  return known ? t(key, x.detail || "") : t("err.generic", x.code, x.detail || "");
}

async function chooseAndConnect() {
  if (S.busy) return;
  let usb;
  try {
    usb = await navigator.usb.requestDevice({ filters: ADB_FILTERS });
  } catch (e) {
    if (e && e.name === "NotFoundError") return;          // the chooser was closed
    S.phase = "failed"; S.failure = explainError(e); render(); return;
  }
  await connectTo(usb);
}

// The devices this browser was allowed before. The browser remembers the permission per device (and a device that
// runs Linux is another device than the same one in Android), so connecting to one of these needs no chooser.
async function loadGranted() {
  try { S.granted = (await navigator.usb.getDevices()).filter((d) => findAdbInterface(d)); } catch (e) { S.granted = []; }
}
// A device that was allowed before came back while the page waits for one (after a restart the page asked for, or
// after a connection that broke): connect to it without asking. A short wait first: just after a start Android
// sets its USB functions up a second time, and a connection made in that moment is cut again.
let reconnectTimer = null;
function reconnectSoon(usb) {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    if (S.phase !== "idle" || !S.lost || S.busy || !findAdbInterface(usb)) return;
    note("the device is back: connecting");
    await connectTo(usb);
  }, 2500);
}

async function connectTo(usb) {
  await disconnect(true);
  S.usb = usb; S.phase = "connecting"; S.failure = null;
  S.serial = usb.serialNumber || "";
  if (S.serial) masker.add(S.serial);
  render();
  try {
    if (!S.key) {
      S.keyInfo = await loadKey("web-installer");
      S.key = S.keyInfo.key;
      S.keyFp = await fingerprint(S.key);
    }
    if (!findAdbInterface(usb)) throw new AdbError("no-interface");
    // The handshake, up to three times. After a session that ended without a goodbye (a closed tab, a browser that
    // was quit, a crash) or just after the device started, the first try can meet silence or a transfer that ends
    // (measured on an F50 from Chrome on macOS, FINDINGS 72; connection.js deals with the lost header that is
    // behind most of it, and adb's own server simply tries again, too). Silence for six seconds is not a device
    // that waits for someone to allow the key: that one has answered, and the page then waits.
    let conn = null;
    for (let attempt = 1; ; attempt++) {
      const transport = await openUsbTransport(usb, PLATFORM);
      let up = false, silent = false;
      const c = new AdbConnection(transport, { key: S.key,
        onAuth: (state) => { if (state === "public-key") { S.phase = "auth"; render(); } },
        onClose: (err) => { if (up) onConnectionLost(c, err); } });
      const quiet = setTimeout(() => { if (S.usb === usb && S.phase === "connecting") { silent = true; c.close(); } }, 6000);
      try {
        await c.connect("web-installer");
        up = true; conn = c;
        break;
      } catch (e) {
        const err = silent ? new AdbError("silent", "the device did not answer") : e;
        try { await c.close(); } catch (e2) { /* gone */ }
        if (S.usb !== usb) return;
        const again = attempt < 3 && S.phase === "connecting" && err instanceof AdbError && ["silent", "disconnected", "protocol"].includes(err.code);
        if (!again) throw err;
        note(`the device did not take the connection (${err.code}); trying again`);
        await new Promise((r) => setTimeout(r, 1500));
      } finally { clearTimeout(quiet); }
    }
    S.conn = conn;
    note(`connected: ${usb.manufacturerName || ""} ${usb.productName || ""} (${maskSerial(S.serial)})`);
    await afterConnect(conn);
  } catch (e) {
    if (S.usb !== usb) return;                              // another attempt has replaced this one
    S.phase = "failed"; S.failure = explainError(e);
    note("connect failed: " + S.failure.code + " " + (S.failure.detail || ""));
    try { if (S.conn) await S.conn.close(); } catch (e2) { /* gone */ }
    S.conn = null;
    render();
  }
}

async function afterConnect(conn) {
  S.phase = "probing"; render();
  const id = await identify(conn);
  S.kind = id.kind;
  const log = (l, isErr) => logLine(l, isErr);
  if (id.kind === "linux") {
    S.dev = new LinuxDevice(conn, { log });
    S.linux = await S.dev.info();
    await startManager();
    S.phase = "ready"; S.view = "home"; render();
    if (S.mgr) { S.mgr.load("overview").catch(mgrFailed); S.mgr.attach().catch(mgrFailed); }
    return;
  }
  if (id.kind !== "android") { S.phase = "failed"; S.failure = { code: "not-ours" }; render(); return; }
  S.dev = new AndroidDevice(conn, { log });
  S.probe = await S.dev.probe();
  S.rootState = "asking"; S.phase = "ready"; S.view = "home"; render();
  await checkRoot();
}

async function checkRoot() {
  if (!S.dev || S.kind !== "android") return;
  S.rootState = "asking"; render();
  try {
    S.rootState = S.probe.su ? await S.dev.root(90000) : "no-su";
    if (S.rootState === "granted") {
      await S.dev.stage(fetchDeviceFile);
      await refreshStatus(true);
      // a job the page did not start in this visit: one that outlived a closed tab, or still runs
      const job = S.status.job;
      // a download the device was doing for a page that is gone: nothing waits for it (what arrived stays for the next try)
      if (job.kind === "fetch" && job.state === "running") { try { await S.dev.cancel(); } catch (e) { if (isGone(e)) return; } await refreshStatus(true); }
      // an installation or an uninstall that went on, or ended, without the page: shown once, with its log
      else if ((job.kind === "install" || job.kind === "uninstall") && ["running", "interrupted", "done"].includes(job.state)) { attachJob(job.kind, { reattached: true }); return; }
      else if (job.kind === "plan" && job.state === "running") { attachJob(job.kind, { reattached: true }); return; }
    }
  } catch (e) {
    if (isGone(e)) return;
    S.failure = explainError(e); S.rootState = "failed";
  }
  render();
}

async function fetchDeviceFile(name) {
  const r = await fetch(new URL("device/" + name, import.meta.url), { cache: "no-store" });
  if (!r.ok) throw new DeviceError("site-files", name);
  return new Uint8Array(await r.arrayBuffer());
}

async function refreshStatus(deep) {
  S.status = await S.dev.status(deep);
  S.analysis = analyseStorage(S.status);
}

const isGone = (e) => e instanceof AdbError && (e.code === "disconnected" || e.code === "closed");

function onConnectionLost(conn, err) {
  if (S.conn !== conn) return;
  note("connection lost: " + (err && err.code));
  S.conn = null; S.dev = null;
  const hadJob = S.view === "job" && S.job && !S.job.done;
  S.phase = "idle";
  S.lost = { hadJob, expected: !!S.expectDisconnect };
  S.expectDisconnect = false;
  releaseWake();
  loadGranted().then(render);
  render();
}

async function disconnect(silent) {
  const c = S.conn;
  S.conn = null; S.dev = null; S.kind = ""; S.probe = null; S.status = null; S.analysis = null; S.linux = null; S.rootState = "";
  S.action = null; S.mgr = null; S.hello = null;
  if (!silent) { S.phase = "idle"; S.lost = null; S.view = "home"; S.w = null; S.job = null; }
  if (c) { try { await c.close(); } catch (e) { /* gone */ } }
  if (!silent) render();
}

async function loadReleaseList() {
  if (S.releases || S.releaseLoading) return;
  S.releaseLoading = true; S.releaseError = null;
  try {
    S.releases = DEMO ? (await import("./demo.js")).demoReleases() : await loadReleases();
  } catch (e) { S.releaseError = explainError(e); }
  S.releaseLoading = false;
  render();
}

async function keepAwake() {
  try { if (navigator.wakeLock && !S.wake) S.wake = await navigator.wakeLock.request("screen"); } catch (e) { /* not granted: the user was told to keep the computer awake */ }
}
function releaseWake() { try { if (S.wake) S.wake.release(); } catch (e) { /* released already */ } S.wake = null; }
window.addEventListener("beforeunload", (ev) => {
  if (S.view === "job" && S.job && !S.job.done) { ev.preventDefault(); ev.returnValue = ""; }
});

// ---------------------------------------------------------------------------------------------------- rendering

function render() {
  // a field that is being typed in keeps the cursor when the page is drawn again (fields that say who they are
  // with data-focus: the two password fields, where leaving the first one draws the page again)
  const at = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focus : "";
  root.replaceChildren(...view());
  root.hidden = false;
  if (at) { const el = root.querySelector(`[data-focus="${at}"]`); if (el) el.focus(); }
}

function view() {
  if (!S.supported && !DEMO) return [unsupportedView(), logPanel()];
  const out = [];
  if (DEMO) out.push(callout("linux", t("demo.title"), t("demo.text")));
  out.push(devicePanel());
  if (S.phase === "ready" && S.dev) {
    if (S.kind === "linux") out.push(linuxView());
    else if (S.rootState === "granted" && S.status) {
      if (S.view === "wizard") out.push(wizardView());
      else if (S.view === "uninstall") out.push(uninstallView());
      else if (S.view === "job") out.push(jobView());
      else out.push(androidHome());
    }
  } else if (S.view === "job" && S.job) {
    out.push(jobView());                // the connection went while a job was shown: its last state stays
  }
  out.push(helpPanel(), logPanel());
  return out;
}

function unsupportedView() {
  const why = !window.isSecureContext ? "unsupported.insecure" : PLATFORM === "android" ? "unsupported.phone" : "unsupported.browser";
  return h("section", { class: "panel wi-panel" },
    h("h2", {}, t("unsupported.title")),
    h("p", {}, t(why)),
    h("p", {}, ...withLink(t("unsupported.other-ways"), page("install/"))));
}

// ---- the device panel: connecting, who the device is, root and bootloader

function devicePanel() {
  const p = h("section", { class: "panel wi-panel wi-device", "aria-live": "polite" });
  if (S.phase === "idle" || S.phase === "failed") {
    p.append(h("h2", {}, t("connect.title")));
    if (S.lost) {
      p.append(callout(S.lost.hadJob ? "linux" : "", t(S.lost.expected ? "lost.expected.title" : "lost.title"),
        t(S.lost.hadJob ? "lost.job" : S.lost.expected ? "lost.expected" : "lost.text")));
    }
    if (S.phase === "failed" && S.failure) p.append(failureBox(S.failure));
    p.append(h("ol", { class: "wi-check" },
      h("li", {}, t("connect.check.usb")), h("li", {}, t("connect.check.debugging")),
      h("li", {}, t("connect.check.adb"), " ", h("code", {}, "adb kill-server"))));
    const known = DEMO ? [] : S.granted;
    p.append(h("div", { class: "actions" },
      known.map((d) => btn(t("connect.known", d.productName || t("device.unknown"), maskSerial(d.serialNumber || "")), () => connectTo(d), "primary", { class: "wi-btn primary wi-known" })),
      btn(t(known.length ? "connect.other" : "connect.button"), DEMO ? demoConnect : chooseAndConnect, known.length ? "" : "primary", { id: "wi-connect" })));
    p.append(h("p", { class: "small muted" }, t(known.length ? "connect.known.note" : "connect.chooser")));
    return p;
  }
  if (S.phase === "connecting" || S.phase === "probing") {
    p.append(h("h2", {}, t("connect.title")), h("p", { class: "wi-wait" }, t(S.phase === "connecting" ? "connect.connecting" : "connect.probing")));
    return p;
  }
  if (S.phase === "auth") {
    p.append(h("h2", {}, t("auth.title")), h("p", { class: "wi-wait" }, t("auth.android")),
      callout("", t("auth.linux.title"), t("auth.linux"), keyBox()),
      h("div", { class: "actions" }, btn(t("button.cancel"), () => disconnect(false))));
    return p;
  }
  // ready
  const usb = S.usb || {};
  const name = S.kind === "linux" ? t("device.linux", S.linux && ({ u30air: "U30 Air", v50: "V50" })[S.linux.device] || "F50")
    : (S.probe && S.probe.props["ro.product.model"]) || usb.productName || t("device.unknown");
  p.append(h("div", { class: "wi-device-head" },
    h("h2", {}, name), chip(S.kind === "linux" ? "Linux" : "Android", S.kind === "linux" ? "linux" : "android"),
    btn(t("button.disconnect"), () => disconnect(false), "quiet")));
  const facts = h("dl", { class: "wi-facts" });
  const fact = (k, ...v) => facts.append(h("dt", {}, k), h("dd", {}, ...v));
  if (S.serial) {
    fact(t("device.serial"), h("span", { class: "wi-mono" }, S.showSerial ? S.serial : maskSerial(S.serial)), " ",
      btn(t(S.showSerial ? "button.hide" : "button.show"), () => { S.showSerial = !S.showSerial; render(); }, "link"));
  }
  if (S.kind === "android" && S.probe) {
    const pr = S.probe.props;
    fact(t("device.android"), `${pr["ro.build.version.release"] || "?"} (${pr["ro.build.display.id"] || "?"})`);
    if (pr["ro.boot.slot_suffix"]) fact(t("device.slot"), pr["ro.boot.slot_suffix"].replace("_", ""));
    // root and the bootloader: two facts, shown apart; the page changes neither
    const rk = S.status ? S.status.root_kind : "";
    const rootChip = { granted: chip(t("root.granted", rk === "magisk" ? "Magisk" : rk === "kernelsu" ? "KernelSU" : rk === "apatch" ? "APatch" : "su"), "ok"),
      asking: chip(t("root.asking"), "wait"), denied: chip(t("root.denied"), "bad"), "no-su": chip(t("root.none"), "bad"), failed: chip(t("root.failed"), "bad") }[S.rootState] || chip("?", "");
    fact(t("device.root"), rootChip);
    const lk = S.probe.lock;
    // locked is not an error and stops nothing: with a patched TrustOS a locked device starts Linux. It is a
    // question for the user, in the warning colour (below, and again beside the plan).
    fact(t("device.bootloader"), chip(t("lock." + lk), lk === "unlocked" ? "ok" : lk === "locked" ? "warn" : ""));
  }
  if (S.kind === "linux" && S.linux) {
    fact(t("linux.system"), `${S.linux.os || "?"} · ${S.linux.version || "?"}`);
    fact(t("linux.kernel"), S.linux.kernel || "?");
  }
  p.append(facts);
  if (S.kind === "android") {
    if (S.rootState === "asking") p.append(h("p", { class: "wi-wait" }, t("root.asking.text")));
    if (S.rootState === "denied") p.append(callout("warn", t("root.denied.title"), t("root.denied.text")), h("div", { class: "actions" }, btn(t("button.again"), checkRoot)));
    if (S.rootState === "no-su") p.append(callout("warn", t("root.none.title"), t("root.none.text")));
    if (S.rootState === "failed" && S.failure) p.append(failureBox(S.failure), h("div", { class: "actions" }, btn(t("button.again"), checkRoot)));
    if (S.probe && S.probe.lock === "locked") p.append(lockedNote());
    if (S.probe) {
      p.append(h("details", { class: "wi-explain" }, h("summary", {}, t("explain.title")),
        h("p", {}, t("explain.root")), h("p", {}, t("explain.lock")), h("p", {}, t("explain.lock." + S.probe.lock)), h("p", {}, t("explain.neither"))));
    }
  }
  return p;
}

// the bootloader says "locked": said as a question, never as a refusal
function lockedNote() { return callout("warn", t("lock.locked.title"), t("lock.locked.question")); }

function failureBox(f) {
  const box = callout("warn", t("failure.title"), errorText(f));
  if (f.code === "adb.busy" || f.code === "adb.silent") box.append(h("pre", {}, h("code", {}, "adb kill-server")));
  if (f.code === "adb.access" && f.platform === "linux") {
    box.append(h("pre", {}, h("code", {}, 'echo \'SUBSYSTEM=="usb", ATTR{bInterfaceClass}=="ff", ATTR{bInterfaceSubClass}=="42", MODE="0660", TAG+="uaccess"\' | sudo tee /etc/udev/rules.d/51-adb-webusb.rules\nsudo udevadm control --reload && sudo udevadm trigger')));
  }
  return box;
}

function keyBox() {
  if (!S.key) return null;
  const line = publicKeyLine(S.key);
  const cmd = `echo '${line}' | sudo mu300-usb adb key - && sudo mu300-usb adb on`;
  const pre = h("pre", { class: "wi-key" }, h("code", {}, cmd));
  return h("div", {}, h("p", { class: "small" }, t("key.text", S.keyFp || "")), pre,
    btn(t("button.copy"), async (ev) => { try { await navigator.clipboard.writeText(cmd + "\n"); ev.target.textContent = t("button.copied"); } catch (e) { /* select by hand */ } }, "small"),
    S.keyInfo && !S.keyInfo.persistent ? h("p", { class: "small muted" }, t("key.not-kept")) : null);
}

// ---- help, links, the log

function helpPanel() {
  return h("section", { class: "wi-help" },
    h("ul", {},
      h("li", {}, h("a", { href: page("install/") }, t("help.text-installer"))),
      h("li", {}, h("a", { href: "https://github.com/dikeckaan/mu300-linux/issues" }, t("help.issues"))),
      h("li", {}, h("a", { href: TELEGRAM, rel: "noopener" }, t("help.telegram")))),
    S.key ? h("details", { class: "wi-explain" }, h("summary", {}, t("key.title")), h("p", {}, t("key.what")), keyBox()) : null);
}

function logPanel() {
  const pre = h("pre", { id: "wi-log-pre", class: "wi-log", tabindex: "0", "aria-label": t("log.title") }, LOG.join("\n") + (LOG.length ? "\n" : ""));
  pre.addEventListener("scroll", () => { pre.dataset.follow = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30 ? "1" : "0"; });
  const d = h("details", { class: "wi-logbox", id: "wi-logbox", open: S.logOpen },
    h("summary", {}, t("log.title"), " (", h("span", { id: "wi-log-count" }, String(LOG.length)), ")"),
    h("p", { class: "small muted" }, t("log.masked")),
    pre,
    h("div", { class: "actions" }, btn(t("log.save"), saveLog, "small"), btn(t("log.copy"), async () => { try { await navigator.clipboard.writeText(LOG.join("\n") + "\n"); } catch (e) { /* no clipboard */ } }, "small")));
  d.addEventListener("toggle", () => { S.logOpen = d.open; if (d.open) pre.scrollTop = pre.scrollHeight; });
  return d;
}

function saveBlob(blob, name) {
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}
function stamp() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}
function saveLog() {
  const head = `MU300 Linux web installer log, ${new Date().toISOString()}\n${navigator.userAgent}\nserial numbers, identity numbers, phone numbers and MAC addresses are masked\n\n`;
  saveBlob(new Blob([head + LOG.join("\n") + "\n"], { type: "text/plain" }), `mu300-web-installer-${stamp()}.log`);
}

// ---------------------------------------------------------------------------------------------------- Android: home

function storageBar(a) {
  if (!a.bar.length) return null;
  const bar = h("div", { class: "wi-bar", role: "img", "aria-label": t("storage.bar.label") });
  const legend = h("ul", { class: "wi-legend" });
  for (const seg of a.bar) {
    if (seg.kind === "gap") continue;
    const el = h("span", { class: "wi-seg " + seg.kind });
    el.style.setProperty("flex-grow", String(Math.max(seg.size / a.disk, 0.012)));
    bar.append(el);
    legend.append(h("li", { class: seg.kind }, t("storage.seg." + seg.kind), " ", h("span", { class: "muted" }, fmtSize(seg.size))));
  }
  return h("figure", { class: "wi-storage" }, bar, legend);
}

function androidHome() {
  const s = S.status, a = S.analysis;
  const out = h("section", { class: "panel wi-panel" });
  const blockers = deviceBlockers(s);
  const hasLinux = s.linux_boot === "linux";
  out.append(h("h2", {}, t(hasLinux ? "home.installed.title" : "home.title")));
  out.append(storageBar(a));
  const linux = s.linux && s.linux.mounted ? s.linux : null;
  if (linux) {
    const names = Object.entries(linux.systems).map(([n, v]) => `${sysName(n, linux.ubuntu)}${v ? " (" + v + ")" : ""}`);
    out.append(h("dl", { class: "wi-facts" },
      h("dt", {}, t("home.systems")), h("dd", {}, names.join(", ") || "-"),
      h("dt", {}, t("home.where")), h("dd", {}, t(linux.where === "sd" ? "storage.sd" : "storage.internal")),
      linux.boot_os ? [h("dt", {}, t("home.boots")), h("dd", {}, sysName(linux.boot_os, linux.ubuntu))] : null,
      linux.kernel ? [h("dt", {}, t("choice.kernel")), h("dd", {}, linux.kernel)] : null,
      h("dt", {}, t("home.default")), h("dd", {}, t(linux.default_linux ? "home.default.linux" : "home.default.android", linux.attempts))));
  }
  if (s.device === "v50") out.append(callout("warn", t("v50.title"), t("v50.text")));
  for (const b of blockers) out.append(callout("warn", t("blocker.title"), t("blocker." + b.code)));
  if (S.failure && S.busy === "") { out.append(failureBox(S.failure)); }
  const acts = h("div", { class: "actions" });
  const off = !!S.busy;
  if (hasLinux) {
    acts.append(btn(t("home.boot-linux"), bootLinux, "primary", { disabled: off }));
    acts.append(btn(t("home.update"), startWizard, "", { disabled: off || blockers.length > 0 }));
    acts.append(btn(t("home.uninstall"), () => { S.view = "uninstall"; S.failure = null; render(); }, "danger", { disabled: off }));
  } else {
    acts.append(btn(t("home.install"), startWizard, "primary", { disabled: off || blockers.length > 0 }));
    if (a.internal.existing || a.sd.existing === "yes" || s.linux_boot === "other") {
      acts.append(btn(t("home.uninstall"), () => { S.view = "uninstall"; S.failure = null; render(); }, "danger", { disabled: off }));
    }
  }
  acts.append(btn(t("home.reboot"), async () => { S.expectDisconnect = true; await S.dev.reboot(); }, "quiet", { disabled: off }));
  out.append(acts);
  if (hasLinux) out.append(h("p", { class: "small muted" }, t("home.boot-linux.note")));
  if (s.job.state === "done") {
    out.append(h("p", { class: "small muted" }, t("home.lastjob", t("job.kind." + s.job.kind), String(s.job.rc)), " ",
      btn(t("home.lastjob.show"), () => attachJob(s.job.kind, { reattached: true }), "link")));
  }
  return out;
}

const SYS_LABEL = { "ubuntu-24.04": "Ubuntu 24.04", "ubuntu-26.04": "Ubuntu 26.04", openwrt: "OpenWrt", "openwrt-luci": "OpenWrt + MU300 panel" };
function sysName(os, ubuntu) {
  if (os === "ubuntu") return "Ubuntu" + (ubuntu ? " " + ubuntu : "");
  return SYS_LABEL[os] ? (os === "openwrt-luci" ? t("sys.openwrt-luci") : SYS_LABEL[os]) : os;
}
const sysLabel = (system) => (system === "openwrt-luci" ? t("sys.openwrt-luci") : SYS_LABEL[system] || system);

async function bootLinux() {
  S.busy = "boot"; S.failure = null; render();
  try {
    S.expectDisconnect = true;
    await S.dev.bootLinux();
  } catch (e) {
    S.expectDisconnect = false;
    if (!isGone(e)) S.failure = explainError(e);
  }
  S.busy = ""; render();
}

// ---------------------------------------------------------------------------------------------------- the wizard

const WSTEPS = ["storage", "choices", "files", "backup", "review"];

function startWizard() {
  S.w = newWizard(); S.view = "wizard"; S.failure = null;
  const a = S.analysis, st = S.status;
  const have = st.linux && st.linux.systems ? Object.keys(st.linux.systems) : [];
  S.w.choices = { target: a.target, systems: [], kernel: (st.linux && st.linux.kernel) || (st.device === "v50" ? "6.18" : "5.4"), bootOs: (st.linux && st.linux.boot_os) || "",
    // Linux as the default boot is proposed every time, as the installers do: an installation that is there may say
    // "Android" only because the page's own "Restart into Android" made it so on the way here
    defaultLinux: true, attempts: (st.linux && st.linux.attempts) || 5, hotspot: true, gpu: true, vpn: false,
    mode: "update", password: "", password2: "", adb: false, eraseConfirmed: false, overwriteConfirmed: false, have };
  loadReleaseList();
  render();
}

function wizardCtx() {
  const w = S.w, c = w.choices;
  const release = w.beta && S.releases && S.releases.beta ? S.releases.beta : S.releases && S.releases.stable;
  const zips = {};
  if (release) for (const s of c.systems) zips[s] = findZip(release, s, c.kernel);
  return { status: S.status, analysis: S.analysis, release, zips };
}

function stepper(active) {
  return h("ol", { class: "wi-stepper" }, WSTEPS.map((n, i) => h("li", { class: i === active ? "now" : i < active ? "done" : "", "aria-current": i === active ? "step" : null }, t("wizard.step." + n))));
}

function wizardView() {
  const w = S.w;
  const out = h("section", { class: "panel wi-panel wi-wizard" });
  out.append(h("div", { class: "wi-device-head" }, h("h2", {}, t("wizard.title")), btn(t("button.cancel"), () => { S.view = "home"; S.w = null; render(); }, "quiet", { disabled: !!S.busy })));
  out.append(stepper(w.step));
  out.append([stepStorage, stepChoices, stepFiles, stepBackup, stepReview][w.step]());
  return out;
}

function nav(back, next, nextLabel, nextDisabled) {
  return h("div", { class: "actions wi-nav" },
    back ? btn(t("button.back"), () => { S.w.step--; S.failure = null; render(); }, "", { disabled: !!S.busy }) : null,
    next ? btn(nextLabel || t("button.next"), next, "primary", { disabled: !!nextDisabled || !!S.busy }) : null);
}
const radio = (name, value, current, label, onchange, props = {}) => h("label", { class: "wi-opt" + (props.disabled ? " off" : "") },
  h("input", { type: "radio", name, value, checked: current === value, onchange, ...props }), h("span", {}, label));
const check = (checked, label, onchange, props = {}) => h("label", { class: "wi-opt" }, h("input", { type: "checkbox", checked, onchange, ...props }), h("span", {}, label));

// step 1: where Linux goes
function stepStorage() {
  const a = S.analysis, c = S.w.choices, w = S.w;
  const box = h("div", {});
  box.append(h("h3", {}, t("storage.title")), storageBar(a));
  box.append(h("p", {}, t("storage.verdict." + a.internal.verdict, fmtSize(a.internal.size), String(Math.max(a.internal.dirty, 0)))));
  if (a.internal.foreignImage) box.append(callout("warn", "", t("storage.foreign-image")));
  if (a.makeRoom) {
    box.append(callout("warn", t("makeroom.title"),
      h("p", {}, t("makeroom.figures", fmtSize(a.makeRoom.userdata), fmtSize(a.makeRoom.total), fmtSize(Math.max(a.makeRoom.maxLinux, 0)), fmtSize(a.makeRoom.minLinux))),
      h("p", {}, ...withLink(t(a.makeRoom.possible ? "makeroom.text" : "makeroom.impossible"), page("install/") + "#variant-32gb"))));
  }
  const opts = h("div", { class: "wi-opts" });
  const set = (v) => () => { c.target = v; c.eraseConfirmed = false; w.eraseTyped = ""; render(); };
  opts.append(radio("target", "internal", c.target, t("storage.opt.internal", fmtSize(a.internal.size), t(a.internal.existing ? "storage.has-linux" : "storage.free")), set("internal"),
    { disabled: !a.internal.usable }));
  if (a.sd.present) {
    opts.append(radio("target", "sd", c.target, t("storage.opt.sd", fmtSize(a.sd.size), t("storage.sd." + a.sd.existing)), set("sd"), { disabled: !a.sd.usable }));
  } else {
    opts.append(h("p", { class: "small muted" }, t(S.status.sd.small ? "storage.sd.small" : "storage.sd.none")));
  }
  box.append(opts);
  if (c.target === "internal" && a.sd.existing === "yes") box.append(callout("warn", "", t("verr.card-boots-first")));
  if (c.target && targetExisting(a, c.target)) {
    box.append(h("h3", {}, t("mode.title")),
      h("div", { class: "wi-opts" },
        radio("mode", "update", c.mode, t("mode.update"), () => { c.mode = "update"; render(); }),
        radio("mode", "wipe", c.mode, t("mode.wipe"), () => { c.mode = "wipe"; render(); })));
  }
  const existing = c.target ? targetExisting(a, c.target) : false;
  const formats = !(existing && c.mode !== "wipe");
  if (c.target === "sd" && formats) {
    box.append(callout("warn", t("erase.title"), t("erase.sd", fmtSize(a.sd.size)),
      h("label", { class: "wi-field" }, t("erase.type", "ERASE"), h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: w.eraseTyped,
        oninput: (ev) => { w.eraseTyped = ev.target.value; c.eraseConfirmed = w.eraseTyped.trim() === "ERASE"; document.getElementById("wi-next").disabled = !storageOk(); } }))));
  }
  if (c.target === "internal" && existing && c.mode === "wipe") box.append(callout("warn", t("erase.title"), t("erase.internal")));
  if (c.target === "internal" && !existing && a.internal.dirty > 0) {
    box.append(callout("warn", t("dirty.title"), t(a.internal.part ? "dirty.text.part" : "dirty.text", String(a.internal.dirty)),
      h("label", { class: "wi-field" }, t("erase.type", "overwrite"), h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: w.overwriteTyped,
        oninput: (ev) => { w.overwriteTyped = ev.target.value; c.overwriteConfirmed = w.overwriteTyped.trim() === "overwrite"; document.getElementById("wi-next").disabled = !storageOk(); } }))));
  }
  const n = nav(false, () => { S.w.step = 1; render(); }, null, !storageOk());
  n.lastChild.id = "wi-next";
  box.append(n);
  return box;
}
function storageOk() {
  const a = S.analysis, c = S.w.choices;
  if (c.target !== "internal" && c.target !== "sd") return false;
  if (c.target === "internal" && (!a.internal.usable || a.sd.existing === "yes")) return false;
  if (c.target === "sd" && !a.sd.usable) return false;
  const existing = targetExisting(a, c.target);
  const formats = !(existing && c.mode !== "wipe");
  if (c.target === "sd" && formats && !c.eraseConfirmed) return false;
  if (c.target === "internal" && !existing && a.internal.dirty > 0 && !c.overwriteConfirmed) return false;
  return true;
}

// step 2: what is installed
function stepChoices() {
  const w = S.w, c = w.choices, a = S.analysis;
  const box = h("div", {});
  const rel = S.releases;
  if (S.releaseError) {
    box.append(callout("warn", t("release.error.title"), errorText(S.releaseError)), h("div", { class: "actions" }, btn(t("button.again"), () => { S.releaseError = null; loadReleaseList(); render(); })), nav(true));
    return box;
  }
  if (!rel) { box.append(h("p", { class: "wi-wait" }, t("release.loading")), nav(true)); return box; }
  const ctx = wizardCtx(), release = ctx.release;
  box.append(h("h3", {}, t("release.title")));
  box.append(h("p", {}, t("release.is", release.tag), w.beta ? " " + t("release.beta-mark") : ""));
  // asked only when there is a prerelease newer than the release; the answer is no unless the box is ticked
  if (rel.beta && rel.stable) {
    box.append(check(w.beta, t("release.beta.ask", rel.beta.tag), (ev) => { w.beta = ev.target.checked; w.files = {}; render(); }));
  }
  if (!releaseSupportsWeb(release) && !DEVMODE) {
    box.append(callout("warn", t("release.old.title"), h("p", {}, ...withLink(t("release.old", release.tag), page("install/")))), nav(true));
    return box;
  }
  const have = c.mode === "wipe" || !targetExisting(a, c.target) ? [] : c.have;
  const size = targetSize(a, c.target);
  const avail = combos(release);
  const toggle = (system) => (ev) => {
    c.systems = c.systems.filter((s) => familyOf(s) !== familyOf(system));
    if (ev.target.checked) c.systems.push(system);
    c.systems.sort((x, y) => (familyOf(x) === "ubuntu" ? -1 : 1) - (familyOf(y) === "ubuntu" ? -1 : 1));
    const after = [...new Set([...have, ...c.systems.map(osOf)])];
    if (!after.includes(c.bootOs)) c.bootOs = c.systems.length ? osOf(c.systems[0]) : after[0] || "";
    if (c.systems.includes("ubuntu-26.04") && c.kernel === "5.4") c.kernel = "6.18";
    w.files = {}; render();
  };
  box.append(h("h3", {}, t("choice.systems")));
  if (have.length) box.append(h("p", { class: "small" }, t("choice.have", have.map((o) => sysName(o, S.status.linux && S.status.linux.ubuntu)).join(", "))));
  const grid = h("div", { class: "wi-tiles" });
  for (const system of ["ubuntu-24.04", "ubuntu-26.04", "openwrt", "openwrt-luci"]) {
    const on = c.systems.includes(system);
    grid.append(h("label", { class: "wi-tile" + (on ? " on" : "") + (avail[system] ? "" : " off") },
      h("input", { type: "checkbox", checked: on, disabled: !avail[system], onchange: toggle(system) }),
      h("strong", {}, sysLabel(system)), h("span", { class: "small" }, t("sys.about." + system))));
  }
  box.append(grid, h("p", { class: "small muted" }, t("choice.systems.note", fmtSize(size), fmtSize(NEED.openwrt), fmtSize(NEED.ubuntu), fmtSize(NEED.both))));

  box.append(h("h3", {}, t("choice.kernel")));
  const ks = h("div", { class: "wi-opts" });
  for (const k of ["5.4", "6.18", "7.2", "7.3"]) {
    const ok = c.systems.every((s) => (avail[s] || []).includes(k)) && !(S.status.device === "v50" && k === "5.4");   // the V50: mainline only
    ks.append(radio("kernel", k, c.kernel, t("kernel." + k), () => { c.kernel = k; w.files = {}; render(); }, { disabled: !ok }));
  }
  box.append(ks);

  const after = [...new Set([...have, ...c.systems.map(osOf)])];
  if (after.length > 1) {
    box.append(h("h3", {}, t("choice.boot-os")),
      h("div", { class: "wi-opts" }, after.map((o) => radio("bootos", o, c.bootOs, sysName(o, c.systems.includes("ubuntu-26.04") ? "26.04" : c.systems.includes("ubuntu-24.04") ? "24.04" : S.status.linux && S.status.linux.ubuntu), () => { c.bootOs = o; render(); }))));
  }
  box.append(h("h3", {}, t("choice.default")),
    h("div", { class: "wi-opts" },
      radio("defboot", "linux", c.defaultLinux ? "linux" : "android", t("choice.default.linux"), () => { c.defaultLinux = true; render(); }),
      radio("defboot", "android", c.defaultLinux ? "linux" : "android", t("choice.default.android"), () => { c.defaultLinux = false; render(); })));
  if (c.defaultLinux) {
    box.append(h("label", { class: "wi-field" }, t("choice.attempts"),
      h("select", { onchange: (ev) => { c.attempts = Number(ev.target.value); } }, [1, 2, 3, 4, 5, 6].map((n) => h("option", { value: n, selected: c.attempts === n }, String(n)))),
      h("span", { class: "small muted" }, t("choice.attempts.note"))));
  }
  box.append(h("h3", {}, t("choice.more")),
    check(c.hotspot, t("choice.hotspot"), (ev) => { c.hotspot = ev.target.checked; }),
    check(c.gpu, t("choice.gpu"), (ev) => { c.gpu = ev.target.checked; }),
    check(c.vpn, t("choice.vpn"), async (ev) => { c.vpn = ev.target.checked; w.vpnFile = null; if (c.vpn && !S.vpnAsset) { try { S.vpnAsset = DEMO ? (await import("./demo.js")).demoVpn() : await loadVpnModule(); } catch (e) { c.vpn = false; S.failure = explainError(e); } render(); } }),
    check(c.adb, t("choice.adb"), (ev) => { c.adb = ev.target.checked; }),
    h("p", { class: "small muted" }, t("choice.adb.note")));

  box.append(h("h3", {}, t("choice.password")), h("p", { class: "small" }, t("choice.password.note")),
    h("div", { class: "wi-pw" },
      h("label", { class: "wi-field" }, t("choice.password.label"), h("input", { type: "password", autocomplete: "new-password", "data-focus": "pw1", value: c.password, oninput: (ev) => { c.password = ev.target.value; }, onchange: () => setTimeout(render, 0) })),
      h("label", { class: "wi-field" }, t("choice.password.again"), h("input", { type: "password", autocomplete: "new-password", "data-focus": "pw2", value: c.password2, oninput: (ev) => { c.password2 = ev.target.value; }, onchange: () => setTimeout(render, 0) }))));

  // what this choice downloads, from where and how large: said before anything is fetched
  const wanted = c.systems.map((sys) => ctx.zips[sys]).filter(Boolean);
  if (wanted.length) {
    box.append(h("h3", {}, t("choice.files")),
      h("p", { class: "small" }, t("choice.files.what", release.tag, fmtSize(wanted.reduce((x, z) => x + z.size, 0)))),
      h("ul", { class: "wi-filelist small wi-mono" }, wanted.map((z) => h("li", {}, `${z.name} · ${fmtSize(z.size)}`))),
      h("p", { class: "small muted" }, t("choice.files.how")));
  }

  const errs = validateChoices(c, ctx);
  if (c.password !== c.password2) errs.push({ code: "password-differs" });
  const shown = errs.filter((e) => !(e.code === "no-system" && !w.touched));
  w.touched = true;
  for (const e of shown) box.append(callout("warn", "", t("verr." + e.code, e.system ? sysLabel(e.system) : fmtSize(e.need || 0), e.kernel || fmtSize(e.size || 0))));
  if (S.failure) box.append(failureBox(S.failure));
  box.append(nav(true, () => { S.failure = null; S.w.step = 2; render(); probeSources(); }, null, errs.length > 0));
  return box;
}

// step 3: the files of the release
function neededFiles() {
  const w = S.w, c = w.choices, ctx = wizardCtx();
  const list = c.systems.map((s) => ({ id: s, label: sysLabel(s) + " · " + t("choice.kernel") + " " + c.kernel, asset: ctx.zips[s], kind: "zip" }));
  if (c.vpn && S.vpnAsset) list.push({ id: "vpn", label: t("files.vpn", S.vpnAsset.tag), asset: S.vpnAsset, kind: "vpn" });
  return list;
}
async function pickFile(item, file) {
  const w = S.w, c = w.choices, ctx = wizardCtx();
  const slot = { file, error: null, ok: false };
  if (item.kind === "vpn") w.vpnFile = slot; else w.files[item.id] = slot;
  try {
    if (item.kind === "zip") {
      const info = await inspectMagiskZip(file);
      slot.info = info;
      if (info.system !== item.id || info.kernel !== c.kernel) slot.error = t("files.err.other", sysLabel(info.system), info.kernel);
      else if (!info.web) slot.error = t("files.err.old", info.tag);
      else if (info.tag !== ctx.release.tag || file.size !== item.asset.size) {
        if (DEVMODE) { slot.unlisted = true; slot.sha256 = await hashFile(file); } else slot.error = t("files.err.release", ctx.release.tag, info.tag);
      }
    } else if (file.size !== item.asset.size) {
      slot.error = t("files.err.size", fmtSize(item.asset.size));
    }
  } catch (e) {
    slot.error = e instanceof ZipError ? t("files.err.notzip") : (e.message || String(e));
  }
  slot.ok = !slot.error;
  render();
}
async function hashFile(file) {
  const hsh = new Sha256();
  for (let o = 0; o < file.size; o += 4 * MIB) hsh.update(new Uint8Array(await file.slice(o, o + 4 * MIB).arrayBuffer()));
  return hsh.hex();
}
// Which ways there are to get the chosen zips onto the device, asked once per choice: can the device download from
// GitHub itself (android-web.sh net: a request for the first zip's headers), and does the mirror hold this release.
// The first way that works is the one proposed: the device's own download, then this browser's, then a file.
async function probeSources(force) {
  const w = S.w;
  if (!w || w.probing) return;
  const zips = neededFiles().filter((i) => i.kind === "zip");
  if (!zips.length || zips.some((i) => !i.asset)) return;
  const key = zips.map((i) => i.asset.name).join(" ");
  if (!force && w.probedFor === key) return;
  w.probing = true; w.net = null; w.mirror = null; render();
  if (DEVMODE) {
    w.net = { ok: false, why: "dev", uplink: "none", detail: "" };      // zips that are no release's: only a picked file
  } else {
    try { w.net = await S.dev.net(zips[0].asset.name); } catch (e) {
      if (isGone(e)) { w.probing = false; return; }
      w.net = { ok: false, why: "failed", uplink: "none", detail: explainError(e).detail || "" };
    }
    if (MIRROR_BASE && !DEMO) {
      try {
        const m = await loadMirror(MIRROR_BASE, wizardCtx().release.tag);
        if (zips.every((i) => m.files[i.asset.name] && m.files[i.asset.name].sha256 === i.asset.sha256 && m.files[i.asset.name].size === i.asset.size)) w.mirror = m;
      } catch (e) { w.mirror = null; }
    }
  }
  if (S.w !== w) return;
  w.probing = false; w.probedFor = key;
  const ok = { device: w.net.ok, mirror: !!w.mirror, file: true };
  if (!w.source || !ok[w.source]) w.source = w.net.ok ? "device" : w.mirror ? "mirror" : "file";
  render();
}
function stepFiles() {
  const w = S.w;
  const box = h("div", {});
  const list = neededFiles();
  const zips = list.filter((i) => i.kind === "zip");
  const total = zips.reduce((x, i) => x + i.asset.size, 0);
  const tag = wizardCtx().release.tag;
  box.append(h("h3", {}, t("files.title")), h("p", {}, t("files.what", tag, fmtSize(total))),
    h("ul", { class: "wi-filelist small wi-mono" }, zips.map((i) => h("li", {}, `${i.asset.name} · ${fmtSize(i.asset.size)}`))));
  if (w.probing || !w.net) {
    box.append(h("p", { class: "wi-wait" }, t("files.probing")), nav(true));
    return box;
  }
  const uplink = t("files.uplink." + w.net.uplink);
  box.append(h("p", { class: w.net.ok ? "wi-good small" : "small" },
    w.net.ok ? t("files.net.ok", uplink) : t(w.net.why === "dev" ? "files.net.dev" : w.net.why === "host" ? "files.net.host" : "files.net.none"),
    !w.net.ok && w.net.detail ? " (" + w.net.detail + ")" : "", " ",
    btn(t("files.net.again"), () => probeSources(true), "link")));
  const pick = (v) => () => { w.source = v; render(); };
  const opts = h("div", { class: "wi-opts" });
  opts.append(radio("source", "device", w.source, t("files.src.device", fmtSize(total), uplink), pick("device"), { disabled: !w.net.ok }));
  if (MIRROR_BASE) opts.append(radio("source", "mirror", w.source, t("files.src.mirror", fmtSize(total)), pick("mirror"), { disabled: !w.mirror }));
  opts.append(radio("source", "file", w.source, t("files.src.file"), pick("file")));
  box.append(opts);
  if (w.source === "device") box.append(h("p", { class: "small muted" }, t(w.net.uplink === "mobile" || w.net.uplink === "none" || w.net.uplink === "other" ? "files.src.device.metered" : "files.src.device.note", fmtSize(total))));
  if (w.source === "mirror") box.append(h("p", { class: "small muted" }, t("files.src.mirror.note")));
  let all = w.source === "device" ? w.net.ok : w.source === "mirror" ? !!w.mirror : true;
  // the rows with a link and a picker: every zip when the user brings the files, and always the VPN module
  for (const item of list) {
    if (item.kind === "zip" && w.source !== "file") continue;
    const slot = item.kind === "vpn" ? w.vpnFile : w.files[item.id];
    const row = h("div", { class: "wi-file" + (slot && slot.ok ? " ok" : "") },
      h("div", {}, h("strong", {}, item.label), h("div", { class: "small muted wi-mono" }, `${item.asset.name} · ${fmtSize(item.asset.size)}`)),
      h("div", { class: "wi-file-acts" },
        h("a", { class: "wi-btn", href: item.asset.url, rel: "noopener", download: item.asset.name }, t("files.download")),
        h("label", { class: "wi-btn" }, t("files.pick"), h("input", { type: "file", class: "wi-hidden", accept: item.kind === "vpn" ? ".gz,.tgz" : ".zip",
          onchange: (ev) => { if (ev.target.files[0]) pickFile(item, ev.target.files[0]); } }))));
    if (DEMO) row.lastChild.append(btn(t("demo.file"), () => { const made = { file: { name: item.asset.name, size: item.asset.size }, ok: true }; if (item.kind === "vpn") w.vpnFile = made; else w.files[item.id] = made; render(); }, "small"));
    if (slot && slot.error) row.append(h("p", { class: "wi-bad small" }, slot.error));
    else if (slot && slot.ok) row.append(h("p", { class: "wi-good small" }, slot.unlisted ? t("files.unlisted") : t("files.ok", slot.file.name)));
    if (!slot || !slot.ok) all = false;
    box.append(row);
  }
  if (w.source === "file") box.append(h("p", { class: "small muted" }, t("files.why")));
  const need = dataNeeded(Math.max(...zips.map((i) => i.asset.size), 0), list.filter((i) => i.kind === "vpn").reduce((x, i) => x + i.asset.size, 0));
  if (S.status.data_free < need) { all = false; box.append(callout("warn", "", t("files.space", fmtSize(need), fmtSize(S.status.data_free)))); }
  box.append(h("p", { class: "small muted" }, t("files.note")));
  box.append(nav(true, () => { S.w.step = 3; render(); }, null, !all));
  return box;
}

// step 4: a backup of what the installation changes
function stepBackup() {
  const w = S.w, b = w.backup;
  const box = h("div", {});
  box.append(h("h3", {}, t("backup.title")), h("p", {}, t("backup.what", S.status.linux_slot)), h("p", {}, t("backup.not")));
  const canDir = typeof window.showDirectoryPicker === "function";
  if (b.state === "none" || b.state === "failed") {
    if (b.state === "failed" && S.failure) box.append(failureBox(S.failure));
    box.append(h("p", { class: "small" }, t(canDir ? "backup.where.folder" : "backup.where.downloads")));
    box.append(h("div", { class: "actions" },
      btn(t(canDir ? "backup.choose" : "backup.start"), () => runBackup(canDir), "primary", { disabled: !!S.busy }),
      btn(t("backup.skip"), () => { b.state = "skipped"; render(); }, "quiet", { disabled: !!S.busy })));
  } else if (b.state === "running") {
    box.append(progressBar("wi-backup-progress"), h("p", { class: "wi-wait", id: "wi-backup-text" }, t("backup.running")));
  } else if (b.state === "done") {
    box.append(callout("android", t("backup.done.title"), t("backup.done", String(b.files.length), b.where)));
    if (b.store) {
      box.append(h("ul", { class: "wi-downloads" }, [...b.store.keys()].map((n) => h("li", {}, btn(n, () => saveBlob(b.store.get(n), n), "link")))));
    }
  } else if (b.state === "skipped") {
    box.append(callout("", "", t("backup.skipped")), h("div", { class: "actions" }, btn(t("backup.undo-skip"), () => { b.state = "none"; render(); }, "quiet")));
  }
  box.append(nav(true, () => { S.w.step = 4; preparePlan(); }, null, !(b.state === "done" || b.state === "skipped")));
  return box;
}
function progressBar(id) {
  return h("div", { class: "wi-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", id }, h("span", {}));
}
function setProgress(id, done, total, text, textId) {
  const el = document.getElementById(id);
  if (el) {
    const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
    el.firstChild.style.setProperty("width", pct.toFixed(1) + "%");
    el.setAttribute("aria-valuenow", String(Math.round(pct)));
  }
  const tx = textId && document.getElementById(textId);
  if (tx && text) tx.textContent = text;
}
function backupReadme(result) {
  return [`MU300 Linux: backup made by the web installer, ${new Date().toISOString()}`,
    `Android ran from slot ${result.androidSlot}; these are the images of slot ${result.linuxSlot} (the Linux slot), misc and the partition table,`,
    "as they were before the installation. They hold no IMEI and no calibration data: those partitions are never read",
    "by the web installer (tools/backup-device.sh of the project backs them up).", "",
    "To put one image back (device in rooted Android, adb on a computer):",
    "  adb push <name>.img /data/local/tmp/p.img",
    "  adb shell \"su -c 'dd if=/data/local/tmp/p.img of=/dev/block/by-name/<name> && sync'\"",
    "misc-head.bin is the first 4096 bytes of misc; gpt-head.bin and gpt-tail.bin are the two copies of the partition table.",
    "The uninstaller does not need these files: it copies Android's own boot image back.", "",
    ...result.files.map((f) => `${f.sha256}  ${f.name}`), ""].join("\n");
}
async function runBackup(useDir) {
  const b = S.w.backup;
  let open, where, store = null, dir = null;
  try {
    if (useDir) {
      const top = await window.showDirectoryPicker({ mode: "readwrite", id: "mu300-backup" });
      const name = `mu300-backup-${stamp()}`;
      dir = await top.getDirectoryHandle(name, { create: true });
      open = directorySink(dir); where = `${top.name}/${name}`;
    } else {
      store = new Map(); open = memorySink(store); where = t("backup.where.memory");
    }
  } catch (e) { if (e && e.name === "AbortError") return; S.failure = explainError(e); b.state = "failed"; render(); return; }
  b.state = "running"; S.busy = "backup"; S.failure = null; render();
  try {
    const result = await S.dev.backup(open, (done, total, name) => setProgress("wi-backup-progress", done, total, t("backup.progress", name, fmtSize(done), fmtSize(total)), "wi-backup-text"));
    const readme = new TextEncoder().encode(backupReadme(result));
    const sink = await open("README.txt", readme.length);
    await sink.write(readme); await sink.close();
    b.files = result.files; b.where = where; b.store = store; b.state = "done";
    note(`backup: ${result.files.length} files`);
  } catch (e) {
    if (isGone(e)) return;
    S.failure = explainError(e); b.state = "failed";
  }
  S.busy = ""; render();
}

// step 5: the device's own plan, then the word INSTALL
function fileOf(system) { return S.w.files[system]; }
function shaFor(system) { const ctx = wizardCtx(), slot = fileOf(system); return S.w.source === "file" && slot && slot.unlisted ? slot.sha256 : ctx.zips[system].sha256; }
function nameFor(system) { const ctx = wizardCtx(); return ctx.zips[system].name; }

// "12 MiB of 41 MiB · 3.2 MiB/s": the speed over the last seconds, not since the start
function rateText() {
  const seen = [];
  return (done, total) => {
    const now = Date.now();
    seen.push([now, done]);
    while (seen.length > 2 && now - seen[0][0] > 4000) seen.shift();
    const [t0, d0] = seen[0];
    const speed = now - t0 >= 800 && done > d0 ? " · " + t("send.speed", fmtSize((done - d0) / ((now - t0) / 1000))) : "";
    return `${fmtSize(done)} / ${fmtSize(total)}${speed}`;
  };
}
// One zip onto the device, the way the files step settled: onProgress(label, done, total, text)
async function sendRun(i, onProgress) {
  const w = S.w, c = w.choices, system = c.systems[i];
  const asset = wizardCtx().zips[system], signal = w.abort ? w.abort.signal : undefined;
  const say = (label) => { const r = rateText(); return (done, tot) => onProgress(label, done, tot, r(done, tot)); };
  if (w.source === "device") {
    await S.dev.fetchZip(asset.name, asset.size, asset.sha256, say(t("send.device", sysLabel(system))), signal);
  } else if (w.source === "mirror") {
    const blob = await downloadZip(MIRROR_BASE, w.mirror, asset, { onProgress: say(t("send.mirror", sysLabel(system))), signal });
    await S.dev.takeZip(blob, asset.name, asset.sha256, { onProgress: say(t("send.zip", sysLabel(system))), signal });
  } else {
    await S.dev.takeZip(fileOf(system).file, asset.name, shaFor(system), { onProgress: say(t("send.zip", sysLabel(system))), signal });
  }
  if (i === 0 && c.vpn && w.vpnFile) {
    await S.dev.addExtra("vpn", w.vpnFile.file, S.vpnAsset.sha256, { onProgress: say(t("send.vpn")), signal });
  }
}
async function giveAnswers(i) {
  const w = S.w, c = w.choices;
  if (c.password) masker.add(c.password, "***");
  const text = answersFor(c, i, { lang: LANG, status: S.status, analysis: S.analysis, adbKeyLine: c.adb && S.key ? publicKeyLine(S.key) : "" });
  await S.dev.setAnswers(text, c.password);
}
async function preparePlan() {
  const w = S.w;
  w.plan = null; w.planLog = []; w.planError = null; w.typed = "";
  w.abort = new AbortController(); w.sending = true;
  S.busy = "plan"; S.failure = null; render();
  await keepAwake();
  try {
    await sendRun(0, (label, s, tot, text) => setProgress("wi-send-progress", s, tot, `${label}: ${text}`, "wi-send-text"));
    w.sending = false;
    const stop = document.getElementById("wi-send-cancel"); if (stop) stop.hidden = true;
    setProgress("wi-send-progress", 1, 1, t("plan.asking"), "wi-send-text");
    await giveAnswers(0);
    await S.dev.start("plan", nameFor(w.choices.systems[0]));
    const lines = [], errors = [];
    const rc = await S.dev.follow((e) => {
      if (e.type === "plan") lines.push(`MU300-PLAN ${e.key}=${e.value}`);
      else if (e.type === "error") errors.push(e.text);
      else if (e.type === "text" && e.text.trim()) w.planLog.push(e.text);
    });
    if (rc === 3) w.plan = parsePlan(lines);
    else w.planError = errors.length ? errors : [t("plan.failed", String(rc))];
  } catch (e) {
    if (isGone(e)) return;
    S.failure = explainError(e);
  }
  w.abort = null; w.sending = false;
  S.busy = ""; releaseWake(); render();
}
// the ways a file did not arrive that another source can get around
const SOURCE_FAILURES = ["dev.fetch-no-network", "dev.fetch-host", "dev.download-failed", "dev.fetch-size", "dev.sha-mismatch",
  "mirror.none", "mirror.network", "mirror.truncated", "mirror.mismatch", "mirror.bad-manifest"];
function stepReview() {
  const w = S.w, c = w.choices, st = S.status;
  const box = h("div", {});
  box.append(h("h3", {}, t("review.title")));
  if (S.busy === "plan") {
    box.append(progressBar("wi-send-progress"), h("p", { class: "wi-wait", id: "wi-send-text" }, t(w.source === "device" ? "send.starting.device" : w.source === "mirror" ? "send.starting.mirror" : "send.starting")),
      h("p", { class: "small muted" }, t("send.note")),
      h("div", { class: "actions" }, btn(t("send.cancel"), () => { if (w.abort) w.abort.abort(); }, "quiet", { id: "wi-send-cancel", hidden: !w.sending })));
    return box;
  }
  if (S.failure) {
    box.append(failureBox(S.failure));
    // a file that did not arrive: the same way again (a download continues where it broke), or another way
    if (SOURCE_FAILURES.includes(S.failure.code)) {
      box.append(h("div", { class: "actions" }, btn(t("files.other-way"), () => { S.failure = null; w.step = 2; if (w.source !== "file") w.source = ""; render(); probeSources(true); })));
    }
    box.append(nav(true, preparePlan, t("button.again")));
    return box;
  }
  if (w.planError) {
    box.append(callout("warn", t("plan.refused.title"), ...w.planError.map((e) => h("p", {}, e)), h("p", {}, t("plan.refused.note"))), nav(true, preparePlan, t("button.again")));
    return box;
  }
  if (!w.plan) { box.append(nav(true, preparePlan, t("plan.get"))); return box; }
  const p = w.plan;
  const rows = [
    [t("review.systems"), c.systems.map(sysLabel).join(" + ") + (c.systems.length > 1 ? " " + t("review.two-runs") : "")],
    [t("review.release"), `${p.tag} · ${t("choice.kernel")} ${p.kernel}`],
    [t("review.storage"), p.storage === "sd" ? t("review.storage.sd", fmtSize(Number(p.size))) : t("review.storage.internal", fmtSize(Number(p.size)))],
    [t("review.filesystem"), t(p.format === "1" ? "review.fs.create" : "review.fs.keep")],
    [t("review.boots"), sysName(c.bootOs, p.ubuntu)],
    [t("review.default"), t(c.defaultLinux ? "home.default.linux" : "home.default.android", String(c.attempts))],
    [t("review.password"), t("review.pw." + (c.password ? "given" : p.password === "kept" ? "kept" : "image"))],
    [t("review.extras"), [c.vpn ? "VPN" : null, c.adb ? t("review.adb") : null].filter(Boolean).join(", ") || "-"],
    [t("review.bootchain"), p.sync_bootchain === "1" ? (p.bootchain ? t("review.bootchain.sync", p.bootchain.split(" ").join(", "), p.android_slot, p.linux_slot) : t("review.bootchain.same")) : t("review.bootchain.off")],
  ];
  box.append(h("div", { class: "table-scroll" }, h("table", {}, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, v)))))));
  if (S.probe && S.probe.lock === "locked") box.append(lockedNote());
  box.append(callout("linux", t("review.writes.title"),
    t("review.writes", p.storage === "sd" ? t("review.writes.sd") : t("review.writes.region"), p.linux_slot, p.android_slot),
    t("review.fallback")));
  if (w.planLog.length) box.append(h("details", { class: "wi-explain" }, h("summary", {}, t("review.device-plan")), h("pre", { class: "wi-planlog" }, w.planLog.join("\n"))));
  box.append(h("ul", { class: "wi-check" }, h("li", {}, t("review.keep.power", st.device === "u30air" ? t("review.keep.battery") : t(st.device === "v50" ? "review.keep.nobattery.v50" : "review.keep.nobattery"))), h("li", {}, t("review.keep.tab")), h("li", {}, t("review.keep.time"))));
  const go = btn(t("review.install"), startInstall, "primary", { disabled: w.typed !== "INSTALL", id: "wi-go" });
  box.append(h("label", { class: "wi-field" }, t("erase.type", "INSTALL"), h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: w.typed,
    oninput: (ev) => { w.typed = ev.target.value.trim(); go.disabled = w.typed !== "INSTALL"; } })));
  box.append(h("div", { class: "actions wi-nav" }, btn(t("button.back"), () => { S.w.step = 3; render(); }), go));
  return box;
}

// ---------------------------------------------------------------------------------------------------- jobs

// S.job: { kind, steps: [{ name, state }], current, lines, errors, rc, done, runIndex, runs, title, sending, progress }
function newJob(kind, extra = {}) {
  const names = kind === "uninstall" ? UNINSTALL_STEPS : INSTALL_STEPS;
  return { kind, steps: names.map((name) => ({ name, state: "todo" })), current: "", errors: [], rc: null, done: false, runIndex: 0, runs: 1, sending: "", ...extra };
}
function jobEvent(e) {
  const j = S.job;
  if (!j) return;
  if (e.type === "step") {
    for (const s of j.steps) { if (s.state === "now") s.state = "done"; }
    const s = j.steps.find((x) => x.name === e.name);
    if (s) s.state = e.name === "done" ? "done" : "now";
    j.current = e.name; j.progress = null;
    updateJobView();
  } else if (e.type === "error") { j.errors.push(e.text); updateJobView(); }
  else if (e.type === "progress") { j.progress = e; setProgress("wi-job-progress", e.done, e.total); }
}
function updateJobView() {
  const el = document.getElementById("wi-job");
  if (el) el.replaceWith(jobView());
}

async function startInstall() {
  const w = S.w, c = w.choices;
  S.job = newJob("install", { runs: c.systems.length, systems: c.systems.slice(), choices: { ...c, password: "", password2: "" }, device: S.status.device });
  S.view = "job"; S.busy = "job"; S.failure = null; render();
  await keepAwake();
  const j = S.job;
  try {
    for (let i = 0; i < c.systems.length; i++) {
      j.runIndex = i; j.steps = INSTALL_STEPS.map((name) => ({ name, state: "todo" })); j.current = "";
      if (i > 0) {
        // the second system: its zip, and the device's plan for it. It must be an update of what the first run made.
        j.sending = t(w.source === "device" ? "send.device" : w.source === "mirror" ? "send.mirror" : "send.zip", sysLabel(c.systems[i])); updateJobView();
        await S.dev.clean(true);
        await refreshStatus(false);
        await sendRun(i, (label, s, tot, text) => setProgress("wi-job-progress", s, tot, `${label}: ${text}`, "wi-job-sending"));
        j.sending = ""; updateJobView();
        await giveAnswers(i);
        await S.dev.start("plan", nameFor(c.systems[i]));
        const lines = [];
        const prc = await S.dev.follow((e) => { if (e.type === "plan") lines.push(`MU300-PLAN ${e.key}=${e.value}`); else if (e.type === "error") j.errors.push(e.text); });
        const plan = parsePlan(lines);
        if (prc !== 3 || plan.format !== "0" || plan.update !== "1") { j.rc = prc === 3 ? "plan" : prc; j.secondRefused = true; break; }
      }
      await giveAnswers(i);
      // "more": another system follows this one. Only this page can send it; the job's record says so, for a page
      // that finds the job after this one was closed.
      if (i + 1 < c.systems.length) await S.dev.start("install", nameFor(c.systems[i]), "more");
      else await S.dev.start("install", nameFor(c.systems[i]));
      const rc = await S.dev.follow(jobEvent);
      j.rc = rc;
      if (rc !== 0) break;
    }
    await finishJob();
  } catch (e) {
    if (isGone(e)) return;                                // the job goes on; the device panel says how to come back
    j.failure = explainError(e); j.done = true; S.busy = ""; releaseWake(); render();
  }
}
async function finishJob() {
  const j = S.job;
  j.done = true; S.busy = ""; releaseWake();
  try {
    // the zips, the unpacked installer and the job's record go: the next connection finds nothing left to show
    if (!j.plain) await S.dev.clean(true);
    await refreshStatus(true);
  } catch (e) { if (isGone(e)) return; }
  S.w = null;
  render();
}
// a job the device is running or has run, picked up by a page that did not start it (a closed tab, a pulled cable)
async function attachJob(kind, options = {}) {
  S.job = newJob(kind === "uninstall" ? "uninstall" : "install", { reattached: !!options.reattached, plain: kind === "plan",
    // the page that started this run was to install another system after it, and is gone
    unfinished: !!(options.reattached && S.status && S.status.job.more) });
  S.view = "job"; S.busy = "job"; S.failure = null; render();
  const j = S.job;
  try {
    j.rc = await S.dev.follow(jobEvent);
    await finishJob();
  } catch (e) {
    if (isGone(e)) return;
    j.failure = explainError(e); j.done = true; S.busy = ""; render();
  }
}

function jobView() {
  const j = S.job;
  const out = h("section", { class: "panel wi-panel", id: "wi-job" });
  const title = j.kind === "uninstall" ? t("job.uninstall.title") : j.runs > 1 ? t("job.install.title.n", sysLabel(j.systems[j.runIndex]), String(j.runIndex + 1), String(j.runs))
    : t("job.install.title");
  out.append(h("h2", {}, title));
  if (j.reattached && !j.done) out.append(callout("", "", t("job.reattached")));
  if (j.sending) out.append(h("p", { class: "wi-wait", id: "wi-job-sending" }, j.sending), progressBar("wi-job-progress"));
  const list = h("ol", { class: "wi-steps" });
  for (const s of j.steps) {
    const li = h("li", { class: s.state }, h("span", { class: "wi-step-name" }, t(`${j.kind === "uninstall" ? "ustep" : "step"}.${s.name}`)));
    if (s.state === "now") {
      li.append(h("span", { class: "small muted" }, t(j.kind === "uninstall" ? "urisk." + s.name : "risk." + stageRisk(s.name))));
      if (j.progress || s.name === "erase") li.append(progressBar("wi-job-progress"));
    }
    list.append(li);
  }
  out.append(list);
  for (const e of j.errors) out.append(callout("warn", "", e));
  if (j.failure) out.append(failureBox(j.failure));
  if (!j.done) {
    if (!S.conn) out.append(callout("linux", t("lost.title"), t("lost.job")));
    else out.append(h("p", { class: "small muted" }, t("job.keep")));
    return out;
  }
  // done
  if (j.kind === "uninstall") {
    if (j.rc === 0) out.append(callout("android", t("job.uninstall.ok.title"), t("job.uninstall.ok")), h("div", { class: "actions" }, btn(t("home.reboot"), async () => { S.expectDisconnect = true; await S.dev.reboot(); }, "primary"), btn(t("button.done"), () => { S.view = "home"; S.job = null; render(); })));
    else out.append(callout("warn", t("job.failed.title"), t(j.rc === "interrupted" ? "job.interrupted" : "job.uninstall.failed")), h("div", { class: "actions" }, btn(t("button.done"), () => { S.view = "home"; S.job = null; render(); })));
    return out;
  }
  if (j.rc === 0 && !j.plain) out.append(donePanel(j));
  else if (j.plain) out.append(callout("", "", t("job.plan-only")), h("div", { class: "actions" }, btn(t("button.done"), () => { S.view = "home"; S.job = null; render(); })));
  else {
    const first = j.runIndex > 0;
    out.append(callout("warn", t("job.failed.title"),
      t(j.rc === "interrupted" ? "job.interrupted" : first ? "job.second.failed" : "job.install.failed"),
      t(first ? "job.second.state" : "job.install.state")),
      h("div", { class: "actions" }, btn(t("button.done"), () => { S.view = "home"; S.job = null; render(); }), btn(t("log.save"), saveLog)));
  }
  return out;
}

function donePanel(j) {
  const c = j.choices || {}, st = S.status || {};
  const ip = ({ u30air: "192.168.78.1", v50: "192.168.79.1" })[j.device || st.device] || "192.168.77.1";
  const bootOs = c.bootOs || (st.linux && st.linux.boot_os) || "ubuntu";
  const user = bootOs === "ubuntu" ? "ubuntu" : "root";
  const box = h("div", {});
  box.append(callout("android", t("done.title"), t("done.text")));
  if (j.unfinished) box.append(callout("warn", t("done.unfinished.title"), t("done.unfinished")));
  box.append(h("h3", {}, t("done.next")),
    h("ol", {},
      h("li", {}, t("done.reboot")),
      h("li", {}, t("done.wait")),
      h("li", {}, t("done.ssh"), h("pre", {}, h("code", {}, `ssh ${user}@${ip}`)), bootOs !== "ubuntu" ? h("span", {}, t("done.luci", `http://${ip}`)) : null),
      h("li", {}, t(j.reattached ? "done.pw.reattached" : c.password ? "done.pw.given" : c.mode === "update" && (c.have || []).includes(bootOs) && !(j.systems || []).map(osOf).includes(bootOs) ? "done.pw.kept" : bootOs === "ubuntu" ? "done.pw.ubuntu" : "done.pw.openwrt"))));
  box.append(h("h3", {}, t("done.fallback.title")), h("p", {}, t(c.defaultLinux === false ? "done.fallback.android" : "done.fallback", String(c.attempts || 5))), h("p", {}, t("done.back")));
  box.append(h("p", {}, t(c.adb ? "done.web.adb" : "done.web.noadb")));
  box.append(h("div", { class: "actions" },
    // a plain restart: the installer has armed Linux already, nothing more is written
    btn(t("done.reboot.button"), async () => { S.expectDisconnect = true; await S.dev.reboot(); }, "primary"),
    btn(t("button.done"), () => { S.view = "home"; S.job = null; render(); })));
  box.append(h("p", { class: "wi-follow" }, ...withLink(t("done.telegram"), TELEGRAM, { rel: "noopener" })));
  return box;
}

// ---------------------------------------------------------------------------------------------------- uninstall

function uninstallView() {
  const a = S.analysis, s = S.status, u = S.un;
  const out = h("section", { class: "panel wi-panel" });
  out.append(h("div", { class: "wi-device-head" }, h("h2", {}, t("un.title")), btn(t("button.cancel"), () => { S.view = "home"; render(); }, "quiet")));
  out.append(h("p", {}, t("un.what", s.android_slot, s.linux_slot)));
  if (a.internal.existing) {
    out.append(h("h3", {}, t("un.internal", fmtSize(a.internal.size))),
      h("div", { class: "wi-opts" },
        radio("wipe", "secure", u.wipe, t("un.secure"), () => { u.wipe = "secure"; render(); }),
        radio("wipe", "quick", u.wipe, t("un.quick"), () => { u.wipe = "quick"; render(); }),
        radio("wipe", "keep", u.wipe, t("un.keep"), () => { u.wipe = "keep"; render(); })));
  }
  if (a.sd.existing === "yes") {
    out.append(h("h3", {}, t("un.card", fmtSize(a.sd.size))),
      h("div", { class: "wi-opts" },
        radio("sdwipe", "erase", u.sd, t("un.card.erase"), () => { u.sd = "erase"; render(); }),
        radio("sdwipe", "keep", u.sd, t("un.card.keep"), () => { u.sd = "keep"; render(); })));
  }
  out.append(callout("", t("un.untouched.title"), t("un.untouched", s.android_slot), ...[h("p", {}, ...withLink(t("un.grow"), page("updates/") + "#uninstall"))]));
  const go = btn(t("un.go"), startUninstall, "danger", { disabled: u.typed !== "UNINSTALL" });
  out.append(h("label", { class: "wi-field" }, t("erase.type", "UNINSTALL"), h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: u.typed,
    oninput: (ev) => { u.typed = ev.target.value.trim(); go.disabled = u.typed !== "UNINSTALL"; } })), h("div", { class: "actions" }, go));
  return out;
}
async function startUninstall() {
  const u = S.un, a = S.analysis;
  S.job = newJob("uninstall");
  S.view = "job"; S.busy = "job"; S.failure = null; u.typed = ""; render();
  await keepAwake();
  try {
    await S.dev.start("uninstall", a.internal.existing ? u.wipe : "keep", a.sd.existing === "yes" ? u.sd : "keep");
    S.job.rc = await S.dev.follow(jobEvent);
    await finishJob();
  } catch (e) {
    if (isGone(e)) return;
    S.job.failure = explainError(e); S.job.done = true; S.busy = ""; releaseWake(); render();
  }
}

// ---------------------------------------------------------------------------------------------------- MU300 Linux

// ---- the device manager (manager.js) on a release with mu300-api; the small view below on an older one
async function startManager() {
  S.mgr = null; S.hello = null;
  if (typeof S.dev.hello !== "function") return;
  try { S.hello = await S.dev.hello(); } catch (e) { if (isGone(e)) throw e; S.hello = null; }
  if (!S.hello) return;
  S.mgr = new Manager(S.dev, S.hello, { log: (l, e) => logLine(l, e), masker, onChange: mgrChanged });
}
function mgrFailed(e) {
  if (isGone(e) || !S.mgr) return;
  S.mgr.note = { kind: "bad", title: t("mgr.failed"), text: errorText(explainError(e)) };
  render();
}
// a job's new lines go straight into its box (typing in a field is not disturbed); anything else draws the page
function mgrChanged(lines) {
  const pre = document.getElementById("wi-job-pre");
  if (lines && pre && S.mgr && S.mgr.job && S.mgr.job.state === "running" && pre.dataset.state === "running") {
    if (lines.length) { pre.append(document.createTextNode(lines.join("\n") + "\n")); pre.scrollTop = pre.scrollHeight; }
    return;
  }
  render();
}
const MGR_UI = {
  h, t, btn, chip, callout, render, masker, saveBlob, stamp,
  errorText: (e) => errorText(explainError(e)),
  isGone,
  leaving: () => { S.expectDisconnect = true; },
  logText: () => LOG.join("\n"),
  publicKey: () => (S.key ? publicKeyLine(S.key) : ""),
  timeZones: () => { try { return Intl.supportedValuesOf("timeZone").filter((z) => z.includes("/")); } catch (e) { return []; } },
};

function linuxView() {
  if (S.mgr) {
    const v = renderManager(MGR_UI, S.mgr);
    const pre = v.querySelector("#wi-job-pre");
    if (pre && S.mgr.job) pre.dataset.state = S.mgr.job.state;
    return v;
  }
  const i = S.linux, d = S.dev;
  const out = h("section", { class: "panel wi-panel" });
  out.append(h("h2", {}, t("linux.title")));
  out.append(callout("", t("mgr.old.title"), t("mgr.old.text")));
  out.append(h("dl", { class: "wi-facts" },
    h("dt", {}, t("linux.system")), h("dd", {}, `${i.os || "?"} · ${i.version || "?"}`),
    h("dt", {}, t("linux.kernel")), h("dd", {}, `${i.kernel || "?"}${i.kernelChoice ? " (" + i.kernelChoice + ")" : ""}`),
    h("dt", {}, t("home.systems")), h("dd", {}, i.systems.map((o) => sysName(o)).join(", ") || "-"),
    h("dt", {}, t("home.boots")), h("dd", {}, i.bootOs ? sysName(i.bootOs) : "-"),
    h("dt", {}, t("home.default")), h("dd", {}, t(i.defaultBoot === "linux" ? "home.default.linux" : "home.default.android", String(i.attempts))),
    h("dt", {}, t("linux.uptime")), h("dd", {}, t("linux.uptime.value", String(Math.floor(i.uptime / 3600)), String(Math.floor((i.uptime % 3600) / 60)))),
    i.adb ? [h("dt", {}, "adb"), h("dd", {}, t("linux.adb." + i.adb))] : null));
  if (!i.isRoot) out.append(callout("warn", "", t("linux.not-root")));
  const off = !!S.busy;
  const act = (title, fn, opts = {}) => async () => {
    if (opts.confirm && !window.confirm(opts.confirm)) return;
    S.action = { title, lines: [], rc: null, running: true }; S.busy = "action"; render();
    if (opts.long) await keepAwake();
    try {
      if (opts.leaves) S.expectDisconnect = true;
      const r = await fn((line) => { const l = masker.mask(line); S.action.lines.push(l); const pre = document.getElementById("wi-action-pre"); if (pre) { pre.append(document.createTextNode(l + "\n")); pre.scrollTop = pre.scrollHeight; } });
      S.action.rc = r ? r.rc : 0;
      if (opts.refresh) S.linux = await d.info();
    } catch (e) {
      if (isGone(e)) return;
      S.action.rc = -1; S.action.lines.push(errorText(explainError(e)));
    }
    S.action.running = false; S.busy = ""; releaseWake(); render();
  };
  out.append(h("h3", {}, t("linux.updates")),
    h("div", { class: "actions" },
      btn(t("linux.check"), act(t("linux.check"), (on) => d.checkUpdate(on)), "", { disabled: off }),
      btn(t("linux.apply"), act(t("linux.apply"), (on) => d.applyUpdate(on), { confirm: t("linux.apply.confirm"), long: true, refresh: true }), "primary", { disabled: off })),
    h("p", { class: "small muted" }, t("linux.apply.note")));
  out.append(h("h3", {}, t("choice.kernel")),
    h("div", { class: "actions" }, ["5.4", "6.18", "7.2", "7.3"].map((k) => btn(k + (i.kernelChoice === k ? " ✓" : ""), act(t("linux.kernel.set", k), (on) => d.setKernel(k, on), { confirm: t("linux.kernel.confirm", k), long: true, refresh: true }), "", { disabled: off || i.kernelChoice === k }))),
    h("p", { class: "small muted" }, t("linux.kernel.note")));
  if (i.systems.length > 1) {
    out.append(h("h3", {}, t("choice.boot-os")),
      h("div", { class: "actions" }, i.systems.map((o) => btn(sysName(o) + (i.bootOs === o ? " ✓" : ""), act(t("linux.os.set", sysName(o)), (on) => d.setBootOs(o, on), { refresh: true }), "", { disabled: off || i.bootOs === o }))));
  }
  out.append(h("h3", {}, t("linux.power")),
    h("div", { class: "actions" },
      btn(t("linux.reboot"), act(t("linux.reboot"), async () => { await d.reboot(); }, { confirm: t("linux.reboot.confirm"), leaves: true }), "", { disabled: off }),
      btn(t("linux.to-android"), act(t("linux.to-android"), (on) => d.toAndroid(on), { confirm: t("linux.to-android.confirm"), leaves: true }), "", { disabled: off }),
      // "restart into Android" makes Android the default boot (mu300-next-boot android); this is the way back
      i.defaultBoot !== "linux" ? btn(t("linux.default-linux"), act(t("linux.default-linux"), (on) => d.nextBoot("linux", on), { refresh: true }), "", { disabled: off }) : null),
    h("p", { class: "small muted" }, t("linux.to-android.note")));
  if (i.defaultBoot !== "linux") out.append(h("p", { class: "small muted" }, t("linux.default-android.note")));
  out.append(h("h3", {}, t("linux.logs")),
    h("div", { class: "actions" }, btn(t("linux.logs.get"), act(t("linux.logs"), (on) => d.logs(on)), "", { disabled: off })),
    h("p", { class: "small muted" }, t("linux.logs.note")));
  if (S.action) {
    out.append(h("div", { class: "wi-action" },
      h("h3", {}, S.action.title, " ", S.action.running ? chip(t("action.running"), "wait") : chip(S.action.rc === 0 ? t("action.ok") : t("action.failed", String(S.action.rc)), S.action.rc === 0 ? "ok" : "bad")),
      h("pre", { class: "wi-log", id: "wi-action-pre", tabindex: "0" }, S.action.lines.join("\n") + (S.action.lines.length ? "\n" : "")),
      S.action.running ? null : h("div", { class: "actions" }, btn(t("log.save"), () => saveBlob(new Blob([S.action.lines.join("\n") + "\n"], { type: "text/plain" }), `mu300-${stamp()}.log`), "small"))));
  }
  if (i.source === "commands") out.append(h("p", { class: "small muted" }, t("linux.source.commands")));
  return out;
}

// ---------------------------------------------------------------------------------------------------- the demo

async function demoConnect() {
  const demo = await import("./demo.js");
  const d = demo.makeDemo(DEMO, (l) => logLine(l));
  S.usb = { productName: d.kind === "linux" ? "MU300 Linux" : "F50", manufacturerName: "ZTE" };
  S.serial = d.serial; masker.add(S.serial);
  S.keyInfo = await loadKey("web-installer"); S.key = S.keyInfo.key; S.keyFp = await fingerprint(S.key);
  S.conn = { close: async () => {} };
  S.dev = d; S.kind = d.kind; S.phase = "ready"; S.view = "home"; S.lost = null;
  if (d.kind === "linux") {
    S.linux = await d.info();
    await startManager();
    render();
    if (S.mgr) { S.mgr.load("overview").catch(mgrFailed); S.mgr.attach().catch(mgrFailed); }
    return;
  }
  S.probe = await d.probe();
  render();
  await checkRoot();
}

// ---------------------------------------------------------------------------------------------------- start

if (navigator.usb) {
  navigator.usb.addEventListener("disconnect", async (ev) => { if (S.usb === ev.device && S.conn) S.conn.close(); await loadGranted(); if (S.phase === "idle") render(); });
  navigator.usb.addEventListener("connect", async (ev) => {
    await loadGranted();
    if (S.phase !== "idle") return;
    render();
    if (S.lost && !DEMO) reconnectSoon(ev.device);
  });
  loadGranted().then(() => { if (S.phase === "idle") render(); });
}
// a tab that is closed or reloaded says goodbye to the device when it can, so that the next connection is taken at once
window.addEventListener("pagehide", () => { if (S.conn) S.conn.close(); });
window.__mu300 = { state: S, strings: STR, render };       // for a look from the browser's console; nothing reads it
render();
