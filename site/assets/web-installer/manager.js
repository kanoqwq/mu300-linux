// The device manager: a device that runs MU300 Linux, managed through its own mu300-api (FINDINGS 110,
// docs/ADB-INTERFACE.md). Two halves:
//
//   Manager          what is read, what is asked, the background job and how it is followed - no DOM, so that
//                    tests/web/manager.test.mjs runs it against the real mu300-api
//   renderManager()  the page: tabs, facts, forms, the job; given the page's helpers (h, t, btn, ...) by app.js
//
// Nothing here composes a command: every request is a method of device.js's fixed list and a JSON object that
// travels on the command's stdin. The device checks every field again; the page checks the obvious ones first only
// to say what is wrong before the cable is involved. Secrets (the hotspot's and the APN's passwords, VPN links and
// URLs, the login password) are only ever sent: the device never returns them, the log never gets them (they are
// added to the page's masker as well), and a field that held one is emptied once it was saved.
import { DeviceError } from "./device.js";

export const TABS = ["overview", "wifi", "mobile", "vpn", "sms", "lan", "system"];

// ---- the device's rules, as the page checks them first (mu300-api checks them again, and it decides)
const bytes = (s) => new TextEncoder().encode(String(s)).length;
const CONTROL = /[\u0000-\u001f\u007f]/;
export const check = {
  ssid: (s) => typeof s === "string" && s.length > 0 && bytes(s) <= 32 && !CONTROL.test(s) && !/^ | $/.test(s),
  // WPA2's passphrase: 8 to 63 printable ASCII characters
  psk: (s) => typeof s === "string" && /^[\x20-\x7e]{8,63}$/.test(s),
  apn: (s) => s === "" || /^[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?$/.test(s),
  cred: (s) => /^[\x20-\x7e]{0,64}$/.test(s) && !/["'\\]/.test(s) && !/^ | $/.test(s),
  lanIp: (s) => /^(10\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}|192\.168\.\d{1,3})\.1$/.test(s) &&
    s.split(".").every((o) => Number(o) <= 255 && String(Number(o)) === o),
  hostname: (s) => /^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(s),
  password: (s) => typeof s === "string" && s.length > 0 && bytes(s) <= 128 && !CONTROL.test(s),
  number: (s) => /^\+?[0-9]{1,20}$/.test(s),
  smsText: (s) => s.length > 0 && [...s].length <= 480 && !/[\u0000-\u0008\u000b-\u001f\u007f]/.test(s),
  vpnName: (s) => s.length > 0 && [...s].length <= 64 && bytes(s) <= 128 && !CONTROL.test(s) && !/^[- ]| $/.test(s),
  vpnLink: (s) => /^(vless|vmess|trojan|ss|hysteria2|hy2|tuic|wireguard|wg):\/\/[!-~]+$/.test(s.trim()) && s.trim().length <= 8000,
  https: (s) => /^https:\/\/[A-Za-z0-9.-]{1,253}(:\d{1,5})?(\/[!-~]*)?$/.test(s) && s.length <= 2048,
  bands: (s) => /^([0-9]{1,3}(,[0-9]{1,3}){0,31})?$/.test(s),
};

// What an action does to this page's own connection, and to the device's others: the sentence the page asks the
// user to confirm with (a key of strings.js, its arguments), and whether the USB connection itself ends (then the
// page expects to lose the device and says so instead of reporting an error). null: nothing to confirm.
export function consequence(method, req = {}, ctx = {}) {
  switch (method) {
    // OpenWrt re-enumerates the USB gadget whenever the LAN comes up (hotplug.d/iface/10-mu300-usb, for macOS's DHCP):
    // the page's own connection goes and comes back (seen on a U30 Air)
    case "lan.set": return String(ctx.system || "").startsWith("openwrt") ? { key: "mgr.confirm.lan-usb", args: [req.ip], leaves: true } : { key: "mgr.confirm.lan", args: [req.ip] };
    case "usb.role": return req.role === "host" ? { key: "mgr.confirm.usb-host", leaves: true } : null;
    case "adb.auth": return { key: req.mode === "keys" ? "mgr.confirm.adb-keys" : "mgr.confirm.adb-open", leaves: true };
    case "data.power": return req.state === "off" ? { key: ctx.wifiUplink ? "mgr.confirm.data-off" : "mgr.confirm.data-off-only" } : null;
    case "data.set":
      // data roaming alone restarts nothing (mobile-data takes it up within half a minute)
      if (Object.keys(req).length === 1 && typeof req.roaming === "boolean")
        return { key: req.roaming ? "mgr.confirm.roaming-on" : "mgr.confirm.roaming-off" };
      return { key: "mgr.confirm.data-set" };
    case "wifi.set": return { key: "mgr.confirm.wifi-set" };
    case "wifi.power": return req.state === "off" ? { key: "mgr.confirm.wifi-off" } : null;
    case "wificlient.join": return { key: "mgr.confirm.wifi-join" };
    case "wificlient.leave": return { key: "mgr.confirm.wifi-leave" };
    case "vpn.killswitch": return req.value === "on" ? { key: "mgr.confirm.killswitch" } : null;
    case "vpn.service": return req.op === "off" && ctx.killswitch ? { key: "mgr.confirm.vpn-off-killswitch" } : null;
    case "system.reboot": return { key: "mgr.confirm.reboot", leaves: true };
    case "system.boot": return req.target === "android" ? { key: "mgr.confirm.boot-android" } : null;
    case "system.bootos": return { key: "mgr.confirm.bootos" };
    case "system.ssh": return req.state === "off" ? { key: "mgr.confirm.ssh-off" } : null;
    case "system.password": return { key: "mgr.confirm.password", args: [ctx.user || "ubuntu"] };
    case "update.apply": return { key: "mgr.confirm.update" };
    case "kernel.set": return { key: "mgr.confirm.kernel", args: [req.version] };
    case "extra.remove": return { key: "mgr.confirm.extra-remove", args: [req.name] };
    case "sim.select": return { key: "mgr.confirm.sim" };
    case "lock.set": return { key: "mgr.confirm.lock" };
    case "vpn.remove": case "vpn.sub_remove": case "sms.delete": return { key: "mgr.confirm.remove" };
    default: return null;
  }
}

// The methods that start the background job (their answer is {"job": KIND}), and the ones that may not be stopped
const JOB_METHODS = new Set(["wificlient.join", "wificlient.leave", "vpn.add_url", "vpn.sub_add", "vpn.sub_refresh", "vpn.install",
  "vpn.engine_install", "update.check", "update.apply", "kernel.set", "extra.install", "extra.remove"]);
export const isJobMethod = (m) => JOB_METHODS.has(m);

// SMS lists of the two kinds (sms json on Ubuntu and plain OpenWrt; the panel's pool on openwrt-luci) as one
export function normalizeSms(data) {
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data.messages)) {
    return data.messages.filter((m) => m && Array.isArray(m.index)).map((m) => ({
      id: m.index.filter((i) => Number.isInteger(i) && i >= 0).join(","), from: String(m.sender || ""), time: String(m.time || ""),
      text: String(m.text || ""), complete: m.have === undefined || m.have === m.parts, pool: false }));
  }
  if (Array.isArray(data.msgs)) {
    return data.msgs.filter((m) => m && /^\d{1,6}$/.test(String(m.id))).map((m) => ({
      id: String(m.id), from: String(m.peer || ""), time: String(m.time || ""), text: String(m.preview || ""), preview: true,
      dir: String(m.dir || ""), unread: /unread|new/i.test(String(m.status || "")), pool: true }));
  }
  return [];
}

// a diagnostics file: what the manager read, without what identifies the user or the device (the page's masker
// does the rest: serial numbers, MAC addresses, phone and identity numbers)
export function diagnostics(data, logText, masker) {
  const scrub = (o) => JSON.parse(JSON.stringify(o || null, (k, v) => {
    if (["ssid", "user", "name", "address", "ip", "operator"].includes(k) && typeof v === "string" && v) return "[masked]";
    if (k === "clients" && Array.isArray(v)) return `[${v.length} clients]`;
    if (k === "text" && typeof v === "string") return `[${v.length} characters]`;
    return v;
  }));
  const parts = { hello: scrub(data.hello), overview: scrub(data.overview), wifi: scrub(data.wifi), data: scrub(data.data),
    lan: scrub(data.lan), system: scrub(data.system), vpn: scrub(data.vpnStatus) };
  const text = "MU300 Linux device manager diagnostics, " + new Date().toISOString() + "\n" +
    "SSIDs, user names, addresses and messages are left out; serial numbers, MAC addresses and phone numbers masked\n\n" +
    JSON.stringify(parts, null, 1) + "\n\n" + (logText || "");
  return masker ? masker.mask(text) : text;
}

export class Manager {
  // dev: a LinuxDevice; hello: its hello() answer; onChange(): something to show changed; log(line, isErr)
  constructor(dev, hello, options = {}) {
    this.dev = dev;
    this.hello = hello;
    this.features = (hello && hello.features) || {};
    this.methods = new Set((hello && hello.methods) || []);
    this.onChange = options.onChange || (() => {});
    this.log = options.log || (() => {});
    this.masker = options.masker || null;
    this.poll = options.pollMs || 1200;
    this.data = { hello };
    this.errors = {};
    this.loading = {};
    this.job = null;          // { kind, state, rc, lines: [], next, cancel, started }
    this.following = null;   // the promise of the following, while it runs
  }

  has(method) { return this.methods.has(method); }

  // a secret the user typed: never in the log, whatever prints it
  secret(value) { if (this.masker && value) this.masker.add(value, "***"); }

  async read(key, method, req = {}) {
    if (!this.has(method)) { this.data[key] = null; return null; }
    this.loading[key] = true;
    try {
      const o = await this.dev.api(method, req, { quiet: true });
      this.data[key] = o;
      delete this.errors[key];
      return o;
    } catch (e) {
      this.errors[key] = e;
      if (!(e instanceof DeviceError)) throw e;
      return null;
    } finally {
      delete this.loading[key];
    }
  }

  // what a tab shows, read again
  async load(tab) {
    const f = this.features;
    const r = (k, m, q) => this.read(k, m, q);
    switch (tab) {
      case "overview": await r("overview", "overview"); break;
      case "wifi":
        await r("wifi", "wifi.get");
        if (f.wifi_client) await r("wificlient", "wificlient.status");
        break;
      case "mobile":
        await r("data", "data.get");
        if (f.locks) await r("locks", "lock.get");
        break;
      case "vpn": {
        const s = await r("vpnStatus", "vpn.status");
        if (s && s.installed) {
          await r("vpnProfiles", "vpn.profiles");
          await r("vpnSubs", "vpn.subs");
          await r("vpnSettings", "vpn.settings");
        }
        break;
      }
      case "sms":
        await r("sms", "sms.list");
        if (f.forward) await r("forward", "sms.forward");
        break;
      case "lan": await r("lan", "lan.get"); break;
      case "system": await r("system", "system.get"); break;
      default: break;
    }
    this.onChange();
  }

  // one request; a job method starts the job and the page follows it
  async act(method, req = {}) {
    const o = await this.dev.api(method, req);
    if (o.job) {
      this.job = { kind: String(o.job), state: "running", rc: null, lines: [], next: 0, cancel: false, started: null };
      this.onChange();
      // (a connection that goes ends the following: the page says so, the job goes on)
      this.follow().catch((e) => { this.followError = e; this.onChange(); });
    }
    return o;
  }

  // Follows the job from where the page is (job.status from the next line) until it ends; every new line goes to
  // the log, masked. A connection that goes ends the following, never the job (it runs in its own session).
  follow() {
    if (!this.following) this.following = this._follow().finally(() => { this.following = null; });
    return this.following;
  }
  async _follow() {
    {
      for (;;) {
        const o = await this.dev.api("job.status", { from: this.job ? this.job.next : 0 }, { quiet: true });
        const j = o.job || {};
        if (!this.job || (j.started && this.job.started && j.started !== this.job.started)) {
          this.job = { kind: "", state: "", rc: null, lines: [], next: 0, cancel: false, started: j.started };
        }
        const fresh = j.text ? String(j.text).split("\n") : [];
        for (const l of fresh) this.log(l);
        const masked = this.masker ? fresh.map((l) => this.masker.mask(l)) : fresh;
        Object.assign(this.job, { kind: String(j.kind || this.job.kind), state: String(j.state || "none"), rc: typeof j.rc === "number" ? j.rc : null,
          next: Number.isInteger(j.next) ? j.next : this.job.next, cancel: j.cancel === true, started: j.started || this.job.started });
        this.job.lines.push(...masked);
        if (this.job.lines.length > 5000) this.job.lines.splice(0, this.job.lines.length - 5000);
        this.onChange(masked);
        if (this.job.state !== "running") return this.job;
        await new Promise((res) => setTimeout(res, this.poll));
      }
    }
  }

  // the job the device had when the page came (hello's): followed when it runs, shown once when it ended
  async attach() {
    const j = this.hello && this.hello.job;
    if (!j || !j.kind || j.state === "none") return null;
    this.job = { kind: String(j.kind), state: String(j.state), rc: null, lines: [], next: 0, cancel: false, started: null };
    return this.follow();
  }

  async cancelJob() { await this.dev.api("job.cancel", {}); }
  async clearJob() { await this.dev.api("job.clear", {}); this.job = null; this.onChange(); }
}

// ---------------------------------------------------------------------------------------------------- the page

// renderManager(ui, M): the manager's section. ui: { h, t, btn, chip, callout, render, masker, saveBlob, stamp,
// leaving(), errorText(e), logText() } from app.js; M: a Manager. The page is drawn anew on every change (app.js's
// render), so what the user types lives in M.forms, bound by oninput.
export function renderManager(ui, M) {
  const { h, t, btn, chip, callout } = ui;
  const F = (M.forms = M.forms || {});
  const form = (name, init) => (F[name] = F[name] || init());
  const tab = (M.tab = M.tab || "overview");
  const off = !!M.busy;
  const out = h("section", { class: "panel wi-panel wi-mgr" });
  out.append(h("div", { class: "wi-device-head" }, h("h2", {}, t("mgr.title")), chip(sysLabel(M.hello && M.hello.system, t), "linux")));

  // the tabs
  const tabs = h("div", { class: "wi-tabs", role: "tablist" });
  for (const name of TABS) {
    if (name === "sms" && !(M.features.sms)) continue;
    tabs.append(h("button", { type: "button", role: "tab", class: "wi-tab" + (name === tab ? " on" : ""), "aria-selected": name === tab ? "true" : "false",
      onclick: () => { M.tab = name; M.note = null; ui.render(); M.load(name).catch(fail); } }, t("mgr.tab." + name)));
  }
  out.append(tabs);

  // the result of the last action, and the job
  if (M.note) out.append(callout(M.note.kind === "ok" ? "" : "warn", M.note.title || "", M.note.text));
  if (M.job) out.append(jobBox());

  const body = h("div", { class: "wi-mgr-body", role: "tabpanel" });
  out.append(body);
  const err = M.errors[{ overview: "overview", wifi: "wifi", mobile: "data", vpn: "vpnStatus", sms: "sms", lan: "lan", system: "system" }[tab]];
  if (err) body.append(callout("warn", t("mgr.read-failed"), ui.errorText(err)));
  const busyNote = Object.keys(M.loading).length ? h("p", { class: "wi-wait" }, t("mgr.reading")) : null;
  if (busyNote) body.append(busyNote);
  ({ overview, wifi, mobile, vpn, sms, lan, system }[tab])(body);
  body.append(h("div", { class: "actions" }, btn(t("mgr.refresh"), () => M.load(tab).catch(fail), "small quiet", { disabled: off })));
  return out;

  // ---- helpers
  function fail(e) { M.busy = ""; M.note = { kind: "bad", title: t("mgr.failed"), text: ui.errorText(e) }; ui.render(); }
  // run(method, req, after): asks first when the action has a consequence; then the request, the note, a new read
  async function run(method, req, options = {}) {
    const c = consequence(method, req, { system: M.hello && M.hello.system, ...(options.ctx || {}) });
    if (c && !window.confirm(t(c.key, ...(c.args || [])))) return null;
    M.busy = method; M.note = null; ui.render();
    if (c && c.leaves) ui.leaving();
    try {
      const o = await M.act(method, req);
      M.busy = "";
      if (!o.job) M.note = { kind: "ok", text: options.done || t("mgr.done") };
      if (options.after) options.after(o);
      ui.render();
      if (!o.job && options.reload !== false) await M.load(M.tab);
      return o;
    } catch (e) {
      if (ui.isGone(e)) return null;
      fail(e);
      return null;
    }
  }
  function facts(rows) {
    const dl = h("dl", { class: "wi-facts" });
    for (const [k, ...v] of rows) if (v.length && v[0] !== null && v[0] !== undefined && v[0] !== "") dl.append(h("dt", {}, k), h("dd", {}, ...v));
    return dl;
  }
  function field(label, input, hint) {
    return h("label", { class: "wi-field" }, label, input, hint ? h("span", { class: "small muted" }, hint) : null);
  }
  function text(f, key, props = {}) {
    return h("input", { type: "text", autocomplete: "off", spellcheck: "false", value: f[key] || "", "data-focus": props.focus || key,
      oninput: (ev) => { f[key] = ev.target.value; }, ...props });
  }
  // a secret's field: empty, "unchanged" until something is typed; shown only while "show" is ticked (the user sees
  // what is about to be set on the device, never what is there: the device does not give it back)
  function secretInput(f, key, focus) {
    const shown = f["show_" + key];
    const input = h("input", { type: shown ? "text" : "password", autocomplete: "new-password", spellcheck: "false", value: f[key] || "",
      placeholder: t("mgr.unchanged"), "data-focus": focus || key, oninput: (ev) => { f[key] = ev.target.value; } });
    return h("span", { class: "wi-secret" }, input,
      h("label", { class: "wi-show" }, h("input", { type: "checkbox", checked: !!shown, onchange: (ev) => { f["show_" + key] = ev.target.checked; ui.render(); } }), t("mgr.show")));
  }
  function select(f, key, options, onchange) {
    return h("select", { "data-focus": key, onchange: (ev) => { f[key] = ev.target.value; if (onchange) onchange(); else ui.render(); } },
      options.map(([v, label]) => h("option", { value: v, selected: String(f[key]) === String(v) }, label)));
  }
  function checkbox(f, key, label) {
    return h("label", { class: "wi-opt" }, h("input", { type: "checkbox", checked: !!f[key], onchange: (ev) => { f[key] = ev.target.checked; ui.render(); } }), h("span", {}, label));
  }
  function table(head, rows) {
    return h("div", { class: "wi-table-wrap" }, h("table", { class: "wi-table" }, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))),
      h("tbody", {}, rows)));
  }
  function bad(msg) { M.note = { kind: "bad", text: msg }; ui.render(); }
  function fmtKb(kb) { return kb >= 1048576 ? (kb / 1048576).toFixed(1) + " GiB" : (kb / 1024).toFixed(0) + " MiB"; }
  function fmtBytes(b) { b = Number(b) || 0; return b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GiB" : (b / 1048576).toFixed(1) + " MiB"; }
  function ago(sec) { return t("linux.uptime.value", String(Math.floor(sec / 3600)), String(Math.floor((sec % 3600) / 60))); }

  // ---- the job
  function jobBox() {
    const j = M.job;
    const running = j.state === "running";
    const state = running ? chip(t("action.running"), "wait") : j.state === "done" ? chip(j.rc === 0 ? t("action.ok") : t("action.failed", String(j.rc)), j.rc === 0 ? "ok" : "bad")
      : chip(t("mgr.job.interrupted"), "bad");
    const pre = h("pre", { class: "wi-log", id: "wi-job-pre", tabindex: "0" }, j.lines.join("\n") + (j.lines.length ? "\n" : ""));
    return h("div", { class: "wi-action wi-mgr-job" },
      h("h3", {}, t("mgr.job.kind." + j.kind) !== "mgr.job.kind." + j.kind ? t("mgr.job.kind." + j.kind) : j.kind, " ", state),
      running ? h("p", { class: "small muted" }, t("mgr.job.survives")) : null,
      pre,
      h("div", { class: "actions" },
        running && j.cancel ? btn(t("button.cancel"), async () => { try { await M.cancelJob(); } catch (e) { fail(e); } }, "small") : null,
        !running ? btn(t("mgr.job.close"), async () => { try { await M.clearJob(); await M.load(M.tab); } catch (e) { fail(e); } }, "small") : null,
        !running ? btn(t("log.save"), () => ui.saveBlob(new Blob([j.lines.join("\n") + "\n"], { type: "text/plain" }), `mu300-${j.kind}-${ui.stamp()}.log`), "small") : null));
  }

  // ---- Overview
  function overview(b) {
    const o = M.data.overview;
    if (!o) return;
    const s = o.signal || {};
    const reg = s.registration ? t("mgr.reg." + s.registration) : "";
    const hottest = (o.temperatures || []).filter((x) => x.millicelsius > 0).sort((a, c) => c.millicelsius - a.millicelsius)[0];
    b.append(h("h3", {}, t("mgr.ov.device")), facts([
      [t("linux.system"), `${o.os || "?"} · ${o.release || "?"}`],
      [t("linux.kernel"), `${o.kernel || "?"}${o.kernel_choice ? " (" + o.kernel_choice + ")" : ""}`],
      [t("mgr.hostname"), o.hostname],
      [t("linux.uptime"), typeof o.uptime === "number" ? ago(o.uptime) : ""],
      [t("mgr.ov.battery"), o.battery ? `${o.battery.capacity ?? "?"} %${o.battery.status ? " (" + o.battery.status + ")" : ""}` : ""],
      [t("mgr.ov.temp"), hottest ? `${(hottest.millicelsius / 1000).toFixed(1)} °C (${hottest.name})` : ""],
      [t("mgr.ov.memory"), o.memory && o.memory.total_kb ? t("mgr.ov.free-of", fmtKb(o.memory.avail_kb || 0), fmtKb(o.memory.total_kb)) : ""],
      [t("mgr.ov.storage"), o.storage && o.storage.disk ? t("mgr.ov.free-of", fmtKb(o.storage.disk.avail), fmtKb(o.storage.disk.size)) : ""]]));
    b.append(h("h3", {}, t("mgr.ov.network")), facts([
      [t("mgr.operator"), s.operator || ""], [t("mgr.rat"), s.rat || ""], [t("mgr.registration"), reg],
      [t("mgr.signal"), s.rsrp !== null && s.rsrp !== undefined ? `RSRP ${s.rsrp} dBm${s.rsrq !== null && s.rsrq !== undefined ? ", RSRQ " + s.rsrq + " dB" : ""}` : ""],
      [t("mgr.data"), o.data ? t("mgr.data." + (["up", "down", "off"].includes(o.data.state) ? o.data.state : "down")) + (o.data.address ? " · " + o.data.address : "") : ""],
      [t("mgr.ov.usage"), o.traffic && o.traffic.cycle ? t("mgr.ov.usage.value", fmtBytes(o.traffic.cycle.used)) : ""],
      [t("mgr.wifi"), o.hotspot ? (o.hotspot.enabled ? t("mgr.on") : t("mgr.off")) + (o.hotspot.ssid ? " · " + o.hotspot.ssid : "") : ""],
      [t("mgr.lan"), o.lan], [t("mgr.vpn"), o.vpn ? (o.vpn.installed ? t("mgr.vpn.installed") : t("mgr.vpn.missing")) : ""]]));
    if (!o.signal) b.append(h("p", { class: "small muted" }, t("mgr.ov.no-signal")));
  }

  // ---- Wi-Fi
  function wifi(b) {
    const w = M.data.wifi;
    if (!w) return;
    const f = form("wifi", () => ({ ssid: w.ssid, band: w.band, channel: w.channel, hidden: !!w.hidden, password: "" }));
    b.append(facts([
      [t("mgr.state"), chip(w.enabled ? t("mgr.on") : t("mgr.off"), w.enabled ? "ok" : "")],
      [t("mgr.wifi.ssid"), w.ssid], [t("mgr.wifi.security"), t("mgr.sec." + w.security)],
      [t("mgr.wifi.channel"), `${w.band} GHz · ${w.channel === "auto" ? t("mgr.auto") : w.channel}${w.channel_live ? " (" + t("mgr.wifi.live", String(w.channel_live)) + ")" : ""}`],
      [t("mgr.wifi.clients"), String((w.clients || []).length)]]));
    if (w.client_mode) b.append(callout("linux", "", t("mgr.wifi.client-mode")));
    b.append(h("div", { class: "actions" }, btn(w.enabled ? t("mgr.wifi.turn-off") : t("mgr.wifi.turn-on"),
      () => run("wifi.power", { state: w.enabled ? "off" : "on" }), "", { disabled: off })));
    // the form
    const chans = (w.channels && w.channels[f.band]) || [];
    b.append(h("h3", {}, t("mgr.wifi.settings")),
      h("div", { class: "wi-form" },
        field(t("mgr.wifi.ssid"), text(f, "ssid", { maxlength: "32" }), t("mgr.wifi.ssid.hint")),
        field(t("mgr.wifi.password"), secretInput(f, "password"), t(w.password_set ? "mgr.wifi.password.hint" : "mgr.wifi.password.none")),
        field(t("mgr.wifi.band"), select(f, "band", [["5", "5 GHz"], ["2.4", "2.4 GHz"]], () => { f.channel = "auto"; ui.render(); })),
        field(t("mgr.wifi.channel"), select(f, "channel", [["auto", t("mgr.auto")], ...chans.map((c) => [String(c), String(c)])]),
          f.band === "5" ? t("mgr.wifi.dfs") : null),
        checkbox(f, "hidden", t("mgr.wifi.hidden"))),
      h("p", { class: "small muted" }, t(w.security === "wpa3" || w.security === "wpa2-wpa3" ? "mgr.wifi.wpa3.kept" : "mgr.wifi.wpa3")),
      h("div", { class: "actions" }, btn(t("mgr.save"), () => {
        const req = {};
        if (f.ssid !== w.ssid) { if (!check.ssid(f.ssid)) return bad(t("mgr.bad.ssid")); req.ssid = f.ssid; }
        if (f.password) { if (!check.psk(f.password)) return bad(t("mgr.bad.psk")); req.password = f.password; M.secret(f.password); }
        if (f.band !== w.band) req.band = f.band;
        if (f.channel !== w.channel || req.band) req.channel = f.channel;
        if (!!f.hidden !== !!w.hidden) req.hidden = !!f.hidden;
        if (!Object.keys(req).length) return bad(t("mgr.nothing"));
        return run("wifi.set", req, { after: () => { delete F.wifi; }, done: t("mgr.wifi.saved") });
      }, "primary", { disabled: off })));
    // the clients
    if ((w.clients || []).length) {
      b.append(h("h3", {}, t("mgr.wifi.clients")), table([t("mgr.name"), t("mgr.ip"), "MAC"],
        w.clients.map((c) => h("tr", {}, h("td", {}, c.name || "-"), h("td", {}, c.ip || "-"), h("td", { class: "wi-mono" }, ui.masker.mask(c.mac))))));
    }
    // Wi-Fi as the uplink
    if (M.features.wifi_client) {
      const c = form("wc", () => ({ ssid: "", password: "", open: false }));
      const st = M.data.wificlient;
      b.append(h("h3", {}, t("mgr.wc.title")), h("p", { class: "small muted" }, t("mgr.wc.text")));
      if (st && st.text) b.append(h("pre", { class: "wi-log" }, ui.masker.mask(st.text)));
      if (M.data.wcScan) b.append(h("pre", { class: "wi-log" }, M.data.wcScan));
      b.append(h("div", { class: "wi-form" },
        field(t("mgr.wifi.ssid"), text(c, "ssid", { focus: "wc-ssid" })),
        c.open ? null : field(t("mgr.wifi.password"), secretInput(c, "password", "wc-pw")),
        checkbox(c, "open", t("mgr.wc.open"))),
      h("div", { class: "actions" },
        btn(t("mgr.wc.scan"), async () => {
          M.busy = "scan"; ui.render();
          try { const o = await M.dev.api("wificlient.scan", {}); M.data.wcScan = ui.masker.mask(o.text || ""); } catch (e) { if (!ui.isGone(e)) fail(e); }
          M.busy = ""; ui.render();
        }, "small", { disabled: off }),
        btn(t("mgr.wc.join"), () => {
          if (!check.ssid(c.ssid) || /^-/.test(c.ssid)) return bad(t("mgr.bad.ssid"));
          if (!c.open && !(check.psk(c.password) || /^[0-9A-Fa-f]{64}$/.test(c.password))) return bad(t("mgr.bad.psk"));
          const req = c.open ? { ssid: c.ssid, open: true } : { ssid: c.ssid, password: c.password };
          M.secret(c.password);
          return run("wificlient.join", req, { after: () => { delete F.wc; } });
        }, "", { disabled: off }),
        st && st.active ? btn(t("mgr.wc.leave"), () => run("wificlient.leave", {}), "", { disabled: off }) : null,
        st && st.active ? btn(t("mgr.wc.forget"), () => run("wificlient.leave", { forget: true }), "", { disabled: off }) : null));
    }
  }

  // ---- mobile data, the SIM, the locks
  // Data roaming (mobile-data roaming, FINDINGS 120): the SIM in use's setting and whether data is held back; a
  // system without the switch (d.roaming null) gets the old sentence
  function roaming(d) {
    const r = d.roaming;
    if (!r || !d.roaming_switch) return h("p", { class: "small muted" }, t("mgr.roaming.none"));
    const state = r.held ? t("mgr.roaming.held") : r.allowed ? t("mgr.roaming.allowed") : t("mgr.roaming.off");
    return h("div", {}, facts([[t("mgr.roaming.title"), chip(state, r.held ? "warn" : r.allowed ? "ok" : "")]]),
      h("div", { class: "actions" }, btn(r.allowed ? t("mgr.roaming.turn-off") : t("mgr.roaming.allow"),
        () => run("data.set", { roaming: !r.allowed }), "", { disabled: off })),
      h("p", { class: "small muted" }, t("mgr.roaming")));
  }
  function mobile(b) {
    const d = M.data.data;
    if (!d) return;
    const s = d.signal || {};
    const sim = d.sim || null;
    b.append(facts([
      [t("mgr.state"), chip(t("mgr.data." + (["up", "down", "off"].includes(d.state) ? d.state : "down")), d.state === "up" ? "ok" : "")],
      [t("mgr.data.address"), d.address ? `${d.address} (${d.iface})` : ""],
      [t("mgr.operator"), s.operator || ""], [t("mgr.rat"), s.rat || ""],
      [t("mgr.registration"), s.registration ? t("mgr.reg." + s.registration) : ""],
      [t("mgr.sim"), sim && sim.active ? t("mgr.sim.value", String(sim.active), String(sim.default || sim.active)) : ""]]));
    b.append(h("div", { class: "actions" }, btn(d.state === "up" ? t("mgr.data.turn-off") : t("mgr.data.turn-on"),
      () => run("data.power", { state: d.state === "up" ? "off" : "on" }, { ctx: { wifiUplink: M.data.wificlient && M.data.wificlient.active } }), "", { disabled: off })),
      roaming(d));
    const f = form("apn", () => ({ apn: d.apn || "", pdp: d.pdp || "IP", auth: d.auth || "", user: d.user || "", password: "", clear: false }));
    b.append(h("h3", {}, t("mgr.apn.title")), h("p", { class: "small muted" }, t("mgr.apn.text")),
      h("div", { class: "wi-form" },
        field(t("mgr.apn"), text(f, "apn", { maxlength: "100" }), t("mgr.apn.hint")),
        field(t("mgr.apn.pdp"), select(f, "pdp", [["IP", "IPv4"], ["IPV4V6", "IPv4 + IPv6"], ["IPV6", "IPv6"]])),
        field(t("mgr.apn.auth"), select(f, "auth", [["", t("mgr.apn.auth.default")], ["none", t("mgr.apn.auth.none")], ["pap", "PAP"], ["chap", "CHAP"], ["both", "PAP / CHAP"]])),
        field(t("mgr.apn.user"), text(f, "user", { maxlength: "64", focus: "apn-user" })),
        field(t("mgr.apn.password"), secretInput(f, "password", "apn-pw"), t(d.password_set ? "mgr.apn.password.set" : "mgr.apn.password.none")),
        d.user || d.password_set ? checkbox(f, "clear", t("mgr.apn.clear")) : null),
      h("div", { class: "actions" }, btn(t("mgr.save"), () => {
        const req = {};
        if (f.clear) req.clear_credentials = true;
        if (f.apn !== (d.apn || "")) { if (!check.apn(f.apn)) return bad(t("mgr.bad.apn")); req.apn = f.apn; }
        if (f.pdp !== (d.pdp || "IP")) req.pdp = f.pdp;
        if (!f.clear) {
          if (f.auth && f.auth !== (d.auth || "")) req.auth = f.auth;
          if (f.user !== (d.user || "")) { if (!check.cred(f.user)) return bad(t("mgr.bad.cred")); req.user = f.user; }
          if (f.password) { if (!check.cred(f.password)) return bad(t("mgr.bad.cred")); req.password = f.password; M.secret(f.password); }
        }
        if (!Object.keys(req).length) return bad(t("mgr.nothing"));
        return run("data.set", req, { after: () => { delete F.apn; } });
      }, "primary", { disabled: off })));
    if (M.features.dual_sim) {
      b.append(h("h3", {}, t("mgr.sim.title")), h("p", { class: "small muted" }, t("mgr.sim.text")),
        h("div", { class: "actions" }, [1, 2].map((n) => btn(t("mgr.sim.slot", String(n)) + (sim && sim.default === n ? " ✓" : ""),
          () => run("sim.select", { slot: n }), "", { disabled: off || (sim && sim.default === n) }))));
    }
    b.append(h("h3", {}, t("mgr.locks.title")));
    if (!M.features.locks) { b.append(h("p", { class: "small muted" }, t("mgr.locks.none"))); return; }
    const l = M.data.locks && M.data.locks.data;
    if (l) b.append(h("pre", { class: "wi-log" }, ui.masker.mask(JSON.stringify(l, null, 1))));
    const lf = form("locks", () => ({ mode: "auto", lte: "", nr: "" }));
    b.append(h("div", { class: "wi-form" },
      field(t("mgr.locks.mode"), select(lf, "mode", [["auto", t("mgr.auto")], ["4g", "4G"], ["nsa", "5G NSA"], ["sa", "5G SA"]])),
      field(t("mgr.locks.lte"), text(lf, "lte", { placeholder: "1,3,20", focus: "lock-lte" }), t("mgr.locks.bands.hint")),
      field(t("mgr.locks.nr"), text(lf, "nr", { placeholder: "78", focus: "lock-nr" }))),
    h("p", { class: "small muted" }, t("mgr.locks.text")),
    h("div", { class: "actions" },
      btn(t("mgr.locks.mode.set"), () => run("lock.set", { kind: "mode", val: lf.mode }), "", { disabled: off }),
      btn(t("mgr.locks.lte.set"), () => (check.bands(lf.lte) ? run("lock.set", { kind: "lte", val: lf.lte }) : bad(t("mgr.bad.bands"))), "", { disabled: off }),
      btn(t("mgr.locks.nr.set"), () => (check.bands(lf.nr) ? run("lock.set", { kind: "nr", val: lf.nr }) : bad(t("mgr.bad.bands"))), "", { disabled: off }),
      btn(t("mgr.locks.reset"), () => run("lock.set", { kind: "reset", val: "auto" }), "", { disabled: off })));
  }

  // ---- VPN
  function vpn(b) {
    const s = M.data.vpnStatus;
    if (!s) return;
    if (!s.installed) {
      b.append(callout("", t("mgr.vpn.missing.title"), t("mgr.vpn.missing.text")),
        h("div", { class: "actions" }, btn(t("mgr.vpn.install"), () => run("vpn.install", {}), "primary", { disabled: off || !s.extra || (M.job && M.job.state === "running") })));
      return;
    }
    if (s.legacy) { b.append(callout("warn", "", t("mgr.vpn.legacy"))); return; }
    const st = s.status || {};
    const settings = (M.data.vpnSettings && M.data.vpnSettings.settings) || {};
    const ks = String(settings.KILL_SWITCH || st.killswitch || "") === "on" || settings.KILL_SWITCH === "1" || st.killswitch === true;
    const on = st.enabled === true || st.running === true || st.state === "up";
    b.append(facts([
      [t("mgr.state"), chip(on ? t("mgr.on") : t("mgr.off"), on ? "ok" : "")],
      [t("mgr.vpn.profile"), String(st.profile_name || st.profile || "")],
      [t("mgr.vpn.exit"), String(st.exit_ip || "")],
      [t("mgr.vpn.killswitch"), ks ? t("mgr.on") : t("mgr.off")],
      [t("mgr.vpn.ipv6"), settings.IPV6 ? t("mgr.vpn.ipv6." + (["0", "1", "auto"].includes(settings.IPV6) ? settings.IPV6 : "auto")) : ""]]),
      h("div", { class: "actions" },
        btn(on ? t("mgr.vpn.off") : t("mgr.vpn.on"), () => run("vpn.service", { op: on ? "off" : "on" }, { ctx: { killswitch: ks } }), on ? "" : "primary", { disabled: off }),
        on ? btn(t("mgr.vpn.restart"), () => run("vpn.service", { op: "restart" }), "", { disabled: off }) : null,
        btn(ks ? t("mgr.vpn.ks-off") : t("mgr.vpn.ks-on"), () => run("vpn.killswitch", { value: ks ? "off" : "on" }), "", { disabled: off }),
        btn(t("mgr.vpn.check-exit"), async () => {
          M.busy = "exit"; ui.render();
          try { M.data.vpnStatus = await M.dev.api("vpn.status", { exit_ip: true }); } catch (e) { if (!ui.isGone(e)) fail(e); }
          M.busy = ""; ui.render();
        }, "small", { disabled: off })),
      field(t("mgr.vpn.ipv6"), select(form("vpnset", () => ({ ipv6: settings.IPV6 || "auto" })), "ipv6",
        [["auto", t("mgr.vpn.ipv6.auto")], ["0", t("mgr.vpn.ipv6.0")], ["1", t("mgr.vpn.ipv6.1")]],
        () => run("vpn.setting", { key: "IPV6", value: F.vpnset.ipv6 }))));
    // the profiles
    const profiles = (M.data.vpnProfiles && M.data.vpnProfiles.profiles) || [];
    const list = Array.isArray(profiles) ? profiles : Array.isArray(profiles.profiles) ? profiles.profiles : [];
    b.append(h("h3", {}, t("mgr.vpn.profiles")));
    if (list.length) {
      b.append(table([t("mgr.name"), t("mgr.vpn.type"), ""], list.filter((p) => p && typeof p.id === "string").map((p) => h("tr", {},
        h("td", {}, String(p.name || p.id), p.active ? " ✓" : ""), h("td", {}, String(p.type || p.engine || "")),
        h("td", { class: "wi-row-actions" },
          btn(t("mgr.vpn.use"), () => run("vpn.use", { id: p.id }), "small", { disabled: off || p.active }),
          btn(t("mgr.rename"), () => {
            const n = window.prompt(t("mgr.vpn.rename.ask"), String(p.name || ""));
            if (n === null) return null;
            return check.vpnName(n) ? run("vpn.rename", { id: p.id, name: n }) : bad(t("mgr.bad.name"));
          }, "small", { disabled: off }),
          btn(t("mgr.remove"), () => run("vpn.remove", { id: p.id }), "small", { disabled: off }))))));
    } else b.append(h("p", { class: "small muted" }, t("mgr.vpn.no-profiles")));
    const a = form("vpnadd", () => ({ source: "link", data: "", name: "" }));
    b.append(h("h3", {}, t("mgr.vpn.add")), h("p", { class: "small muted" }, t("mgr.vpn.add.text")),
      h("div", { class: "wi-form" },
        field(t("mgr.vpn.source"), select(a, "source", [["link", t("mgr.vpn.source.link")], ["text", t("mgr.vpn.source.text")], ["file", t("mgr.vpn.source.file")], ["url", t("mgr.vpn.source.url")]],
          () => { a.data = ""; ui.render(); })),
        a.source === "text" ? field(t("mgr.vpn.config"), h("textarea", { rows: "6", spellcheck: "false", "data-focus": "vpn-text", oninput: (ev) => { a.data = ev.target.value; } }, a.data))
          : a.source === "file" ? field(t("mgr.vpn.file"), h("input", { type: "file", onchange: async (ev) => {
            const file = ev.target.files && ev.target.files[0];
            if (!file) return;
            if (file.size > 65536) { bad(t("mgr.bad.file")); return; }
            a.data = await file.text(); a.fileName = file.name; ui.render();
          } }), a.fileName ? t("mgr.vpn.file.read", a.fileName) : null)
          : field(t(a.source === "url" ? "mgr.vpn.url" : "mgr.vpn.link"), secretInput(a, "data", "vpn-data")),
        field(t("mgr.vpn.name"), text(a, "name", { maxlength: "64", focus: "vpn-name" }), t("mgr.optional"))),
      h("div", { class: "actions" }, btn(t("mgr.vpn.add.button"), () => {
        if (a.name && !check.vpnName(a.name)) return bad(t("mgr.bad.name"));
        const name = a.name ? { name: a.name } : {};
        M.secret(a.data.trim());
        if (a.source === "url") return check.https(a.data.trim()) ? run("vpn.add_url", { url: a.data.trim(), ...name }, { after: () => { delete F.vpnadd; } }) : bad(t("mgr.bad.url"));
        if (a.source === "link") return check.vpnLink(a.data) ? run("vpn.add", { source: "link", data: a.data.trim(), ...name }, { after: () => { delete F.vpnadd; } }) : bad(t("mgr.bad.link"));
        return a.data.trim() ? run("vpn.add", { source: "text", data: a.data, ...name }, { after: () => { delete F.vpnadd; } }) : bad(t("mgr.nothing"));
      }, "primary", { disabled: off })));
    // the subscriptions
    const subsRaw = (M.data.vpnSubs && M.data.vpnSubs.subscriptions) || [];
    const subs = Array.isArray(subsRaw) ? subsRaw : Array.isArray(subsRaw.subscriptions) ? subsRaw.subscriptions : [];
    b.append(h("h3", {}, t("mgr.vpn.subs")));
    if (subs.length) {
      b.append(table([t("mgr.name"), t("mgr.vpn.host"), ""], subs.filter((x) => x && typeof x.id === "string").map((x) => h("tr", {},
        h("td", {}, String(x.name || x.id)), h("td", {}, String(x.host || "")),
        h("td", { class: "wi-row-actions" },
          btn(t("mgr.vpn.sub.refresh"), () => run("vpn.sub_refresh", { id: x.id }), "small", { disabled: off }),
          btn(t("mgr.remove"), () => run("vpn.sub_remove", { id: x.id }), "small", { disabled: off }))))),
      h("div", { class: "actions" }, btn(t("mgr.vpn.sub.refresh-all"), () => run("vpn.sub_refresh", { id: "all" }), "small", { disabled: off })));
    }
    const sf = form("vpnsub", () => ({ name: "", url: "", interval: "" }));
    b.append(h("div", { class: "wi-form" },
      field(t("mgr.vpn.name"), text(sf, "name", { maxlength: "64", focus: "sub-name" })),
      field(t("mgr.vpn.url"), secretInput(sf, "url", "sub-url")),
      field(t("mgr.vpn.interval"), text(sf, "interval", { inputmode: "numeric", focus: "sub-int", placeholder: "24" }), t("mgr.optional"))),
    h("div", { class: "actions" }, btn(t("mgr.vpn.sub.add"), () => {
      if (!check.vpnName(sf.name)) return bad(t("mgr.bad.name"));
      if (!check.https(sf.url)) return bad(t("mgr.bad.url"));
      if (sf.interval && !/^\d{1,3}$/.test(sf.interval)) return bad(t("mgr.bad.interval"));
      M.secret(sf.url);
      return run("vpn.sub_add", { name: sf.name, url: sf.url, ...(sf.interval ? { interval: Number(sf.interval) } : {}) }, { after: () => { delete F.vpnsub; } });
    }, "", { disabled: off })));
    // engines and the log
    const ef = form("engine", () => ({ name: "xray" }));
    b.append(h("h3", {}, t("mgr.vpn.engines")),
      h("div", { class: "wi-form" }, field(t("mgr.vpn.engine"), select(ef, "name", [["xray", "Xray"], ["sing-box", "sing-box"], ["mihomo", "mihomo"], ["dae", "dae"]]))),
      h("div", { class: "actions" },
        btn(t("mgr.vpn.engine.install"), () => run("vpn.engine_install", { name: ef.name }), "small", { disabled: off }),
        btn(t("mgr.vpn.logs"), async () => {
          M.busy = "logs"; ui.render();
          try { M.data.vpnLog = ui.masker.mask((await M.dev.api("vpn.logs", { lines: 200 })).log || ""); } catch (e) { if (!ui.isGone(e)) fail(e); }
          M.busy = ""; ui.render();
        }, "small", { disabled: off })));
    if (M.data.vpnLog !== undefined) b.append(h("pre", { class: "wi-log" }, M.data.vpnLog));
  }

  // ---- SMS
  function sms(b) {
    const raw = M.data.sms && M.data.sms.data;
    const list = normalizeSms(raw);
    const f = form("sms", () => ({ number: "", text: "" }));
    b.append(h("h3", {}, t("mgr.sms.send")),
      h("div", { class: "wi-form" },
        field(t("mgr.sms.number"), text(f, "number", { inputmode: "tel", maxlength: "21", placeholder: "+90…" })),
        field(t("mgr.sms.text", String([...(f.text || "")].length)), h("textarea", { rows: "3", "data-focus": "sms-text", oninput: (ev) => { f.text = ev.target.value; } }, f.text))),
      h("div", { class: "actions" }, btn(t("mgr.sms.send.button"), () => {
        if (!check.number(f.number)) return bad(t("mgr.bad.number"));
        if (!check.smsText(f.text)) return bad(t("mgr.bad.sms"));
        return run("sms.send", { number: f.number, text: f.text }, { after: () => { delete F.sms; }, done: t(list.some((m) => m.pool) || M.hello.system === "openwrt-luci" ? "mgr.sms.queued" : "mgr.sms.sent") });
      }, "primary", { disabled: off })));
    b.append(h("h3", {}, t("mgr.sms.inbox", String(list.length))));
    if (!list.length) b.append(h("p", { class: "small muted" }, t("mgr.sms.none")));
    for (const m of list) {
      b.append(h("div", { class: "wi-sms" + (m.unread ? " unread" : "") },
        h("div", { class: "wi-sms-head" }, h("strong", {}, m.from || "?"), " ", h("span", { class: "muted small" }, m.time), m.dir ? h("span", { class: "muted small" }, " · " + m.dir) : null),
        h("p", {}, (M.data.smsOpen && M.data.smsOpen[m.id]) || m.text),
        h("div", { class: "actions" },
          m.preview ? btn(t("mgr.sms.read"), async () => {
            try { const o = await M.dev.api("sms.read", { id: m.id }); M.data.smsOpen = { ...(M.data.smsOpen || {}), [m.id]: String((o.data && o.data.text) || o.text || "") }; ui.render(); } catch (e) { if (!ui.isGone(e)) fail(e); }
          }, "small") : null,
          btn(t("mgr.remove"), async () => {
            if (!window.confirm(t("mgr.confirm.remove"))) return;
            M.busy = "sms"; ui.render();
            try { for (const id of m.id.split(",")) await M.dev.api("sms.delete", { id }); M.note = { kind: "ok", text: t("mgr.done") }; } catch (e) { if (!ui.isGone(e)) fail(e); }
            M.busy = ""; await M.load("sms").catch(fail);
          }, "small", { disabled: off }))));
    }
    if (M.features.forward) {
      const fw = M.data.forward && M.data.forward.data;
      b.append(h("h3", {}, t("mgr.sms.forward")), h("p", { class: "small muted" }, t("mgr.sms.forward.text")));
      if (fw) {
        const on = (k) => (fw[k] === true || fw[k] === 1 || fw[k] === "1" ? t("mgr.on") : t("mgr.off"));
        b.append(facts([["Webhook", on("webhook")], ["Telegram", on("telegram")], [t("mgr.sms.forward.email"), on("email")]]));
      }
    }
  }

  // ---- USB, the LAN, adb
  function lan(b) {
    const l = M.data.lan;
    if (!l) return;
    b.append(facts([
      [t("mgr.lan"), l.ip + (l.default_ip && l.default_ip !== l.ip ? " " + t("mgr.lan.default", l.default_ip) : "")],
      [t("mgr.usb.role"), l.usb_role_switch ? t("mgr.usb.role." + (l.usb_role === "host" ? "host" : "device")) : ""],
      ["adb", l.adb ? t("linux.adb." + (l.adb.state === "off" ? "off" : l.adb.auth === "keys" ? "keys" : "open")) : ""],
      [t("mgr.adb.keys"), l.adb && l.adb.auth === "keys" ? String(l.adb.keys) : ""]]));
    const f = form("lan", () => ({ ip: l.ip }));
    b.append(h("h3", {}, t("mgr.lan.title")), h("p", { class: "small muted" }, t("mgr.lan.text")),
      h("div", { class: "wi-form" }, field(t("mgr.lan.address"), text(f, "ip", { inputmode: "decimal", maxlength: "15", focus: "lan-ip" }), t("mgr.lan.hint"))),
      h("div", { class: "actions" }, btn(t("mgr.save"), () => {
        if (f.ip === l.ip) return bad(t("mgr.nothing"));
        return check.lanIp(f.ip) ? run("lan.set", { ip: f.ip }) : bad(t("mgr.bad.lan"));
      }, "", { disabled: off })));
    if (l.usb_role_switch) {
      b.append(h("h3", {}, t("mgr.usb.title")), h("p", { class: "small muted" }, t("mgr.usb.text")),
        h("div", { class: "actions" }, btn(t("mgr.usb.host"), () => run("usb.role", { role: "host" }), "", { disabled: off || l.usb_role === "host" })));
    }
    b.append(h("h3", {}, t("mgr.adb.title")), h("p", { class: "small muted" }, t("mgr.adb.text")),
      h("div", { class: "actions" },
        btn(t("mgr.adb.keys-only"), () => run("adb.auth", { mode: "keys", key: ui.publicKey() }), "", { disabled: off || !ui.publicKey() || (l.adb && l.adb.auth === "keys") }),
        btn(t("mgr.adb.open"), () => run("adb.auth", { mode: "open" }), "", { disabled: off || (l.adb && l.adb.auth === "open") })));
  }

  // ---- the system
  function system(b) {
    const s = M.data.system;
    if (!s) return;
    const zones = ui.timeZones();
    b.append(facts([
      [t("mgr.hostname"), s.hostname], [t("mgr.tz"), s.timezone || "UTC"], [t("mgr.user"), s.user],
      ["SSH", s.ssh ? t("mgr.on") : t("mgr.off")], [t("linux.system"), s.release], [t("linux.kernel"), `${s.kernel}${s.kernel_choice ? " (" + s.kernel_choice + ")" : ""}`],
      [t("home.default"), s.boot ? (s.boot.default === "linux" ? t("mgr.boot.linux") : t("mgr.boot.android")) + (s.boot.locked ? " · " + t("mgr.boot.locked") : "") : ""],
      [t("home.systems"), s.boot ? s.boot.systems.map((x) => sysLabel(x, t)).join(", ") : ""]]));
    const f = form("sys", () => ({ hostname: s.hostname, tz: s.timezone || "UTC", pw1: "", pw2: "" }));
    b.append(h("h3", {}, t("mgr.sys.names")), h("div", { class: "wi-form" },
      field(t("mgr.hostname"), text(f, "hostname", { maxlength: "63", focus: "hostname" })),
      field(t("mgr.tz"), select(f, "tz", ["UTC", ...zones].map((z) => [z, z])))),
    h("div", { class: "actions" }, btn(t("mgr.save"), async () => {
      if (f.hostname !== s.hostname) {
        if (!check.hostname(f.hostname)) return bad(t("mgr.bad.hostname"));
        if (!(await run("system.hostname", { name: f.hostname }, { reload: false }))) return null;
      }
      if (f.tz !== (s.timezone || "UTC")) return run("system.timezone", { zone: f.tz }, { after: () => { delete F.sys; } });
      delete F.sys;
      return M.load("system");
    }, "", { disabled: off })));
    b.append(h("h3", {}, t("mgr.pw.title")), h("p", { class: "small muted" }, t("mgr.pw.text", s.user)),
      h("div", { class: "wi-form" },
        field(t("mgr.pw.new"), secretInput(f, "pw1", "pw1")),
        field(t("mgr.pw.again"), h("input", { type: f.show_pw1 ? "text" : "password", autocomplete: "new-password", value: f.pw2 || "", "data-focus": "pw2", oninput: (ev) => { f.pw2 = ev.target.value; } }))),
      h("div", { class: "actions" }, btn(t("mgr.pw.set"), () => {
        if (!check.password(f.pw1)) return bad(t("mgr.bad.password"));
        if (f.pw1 !== f.pw2) return bad(t("mgr.bad.password-differs"));
        M.secret(f.pw1);
        return run("system.password", { password: f.pw1 }, { ctx: { user: s.user }, after: () => { f.pw1 = ""; f.pw2 = ""; }, done: t("mgr.pw.done", s.user) });
      }, "", { disabled: off })));
    b.append(h("h3", {}, t("mgr.sys.access")), h("div", { class: "actions" },
      btn(s.ssh ? t("mgr.ssh.off") : t("mgr.ssh.on"), () => run("system.ssh", { state: s.ssh ? "off" : "on" }), "", { disabled: off })));
    b.append(h("h3", {}, t("mgr.sys.boot")), h("p", { class: "small muted" }, t("mgr.sys.boot.text")),
      h("div", { class: "actions" },
        btn(t("linux.reboot"), () => run("system.reboot", {}), "", { disabled: off }),
        s.boot && s.boot.default !== "linux" ? btn(t("linux.default-linux"), () => run("system.boot", { target: "linux" }), "", { disabled: off }) : null,
        s.boot && s.boot.default === "linux" ? btn(t("mgr.boot.to-android"), () => run("system.boot", { target: "android" }), "", { disabled: off }) : null,
        s.boot && s.boot.default === "linux" ? btn(s.boot.locked ? t("mgr.boot.unlock") : t("mgr.boot.lock"), () => run("system.boot", { target: s.boot.locked ? "unlock" : "lock" }), "small", { disabled: off }) : null),
      s.boot && s.boot.systems.length > 1 ? h("div", { class: "actions" }, s.boot.systems.map((o) => btn(t("linux.os.set", sysLabel(o, t)) + (s.boot.os === o ? " ✓" : ""),
        () => run("system.bootos", { os: o }), "", { disabled: off || s.boot.os === o }))) : null);
    const jobOn = M.job && M.job.state === "running";
    b.append(h("h3", {}, t("linux.updates")), h("p", { class: "small muted" }, t("mgr.update.text")),
      h("div", { class: "actions" },
        btn(t("linux.check"), () => run("update.check", {}), "", { disabled: off || jobOn }),
        btn(t("linux.apply"), () => run("update.apply", {}), "primary", { disabled: off || jobOn })),
      h("h3", {}, t("choice.kernel")),
      h("div", { class: "actions" }, ["5.4", "6.18", "7.2", "7.3"].map((k) => btn(k + (s.kernel_choice === k ? " ✓" : ""), () => run("kernel.set", { version: k }), "", { disabled: off || jobOn || s.kernel_choice === k }))),
      h("p", { class: "small muted" }, t("linux.kernel.note")));
    b.append(h("h3", {}, t("mgr.extras")), h("p", { class: "small muted" }, t("mgr.extras.text")),
      table([t("mgr.name"), t("mgr.extras.release"), ""], (s.extras || []).filter((x) => !(x.name === "lang" && M.hello.system === "ubuntu") && !(x.name === "panel" && M.hello.system !== "ubuntu")).map((x) => h("tr", {},
        h("td", {}, x.name), h("td", {}, x.installed ? x.release || "?" : "-"),
        h("td", { class: "wi-row-actions" }, x.installed ? btn(t("mgr.remove"), () => run("extra.remove", { name: x.name }), "small", { disabled: off || jobOn })
          : btn(t("mgr.install"), () => run("extra.install", { name: x.name }), "small", { disabled: off || jobOn }))))));
    b.append(h("h3", {}, t("linux.logs")), h("div", { class: "actions" },
      btn(t("linux.logs.get"), async () => {
        M.busy = "logs"; ui.render();
        try { M.data.logs = ui.masker.mask((await M.dev.api("logs", { lines: 300 })).text || ""); } catch (e) { if (!ui.isGone(e)) fail(e); }
        M.busy = ""; ui.render();
      }, "", { disabled: off }),
      btn(t("mgr.diag"), async () => {
        M.busy = "diag"; ui.render();
        try {
          for (const [k, m] of [["overview", "overview"], ["wifi", "wifi.get"], ["data", "data.get"], ["lan", "lan.get"], ["system", "system.get"], ["vpnStatus", "vpn.status"]]) await M.read(k, m);
          const logs = (await M.dev.api("logs", { lines: 500 })).text || "";
          ui.saveBlob(new Blob([diagnostics(M.data, logs + "\n\n--- this page's log\n" + ui.logText(), ui.masker)], { type: "text/plain" }), `mu300-diagnostics-${ui.stamp()}.txt`);
        } catch (e) { if (!ui.isGone(e)) fail(e); }
        M.busy = ""; ui.render();
      }, "", { disabled: off })),
    h("p", { class: "small muted" }, t("mgr.diag.text")));
    if (M.data.logs) b.append(h("pre", { class: "wi-log" }, M.data.logs));
  }
}

function sysLabel(system, t) {
  return { ubuntu: "Ubuntu", openwrt: "OpenWrt", "openwrt-luci": t("sys.openwrt-luci") }[system] || String(system || "");
}
