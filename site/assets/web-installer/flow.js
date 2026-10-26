// The decisions of the wizard, without a screen: what fits where, which choices the installer on the device would
// refuse (said before a file is sent), the answers file of each run, and what the installer's marked lines mean.
// The rules are the installers' own (install.sh, mu300-install.sh, tools/storage.sh); the device decides again in
// the end, and its word counts.
export const MIB = 1024 * 1024, GIB = 1024 * MIB;
// what each choice needs on the Linux filesystem (two copies of a system during an update, and room to work)
export const NEED = { openwrt: 800 * MIB, ubuntu: 1600 * MIB, both: 2400 * MIB };
export const SD_MIN = 700 * MIB;
export const MIN_ANDROID = 4 * GIB;           // what userdata keeps when room is made
export const TESTED_LAYOUT = 20 * GIB;        // a free region of this size: the layout of the tested 64 GB device
export const INSTALL_STEPS = ["check", "unpack", "plan", "vendor", "image", "systems", "boot", "arm", "done"];
export const UNINSTALL_STEPS = ["check", "misc", "boot", "erase", "card", "cleanup", "done"];

export const osOf = (system) => (system.startsWith("ubuntu") ? "ubuntu" : system);
export const familyOf = (system) => (system.startsWith("ubuntu") ? "ubuntu" : "openwrt");

export function needFor(osList) {
  const fams = new Set(osList.map((o) => (o === "ubuntu" ? "ubuntu" : "openwrt")));
  if (fams.has("ubuntu") && fams.has("openwrt")) return NEED.both;
  return fams.has("ubuntu") ? NEED.ubuntu : NEED.openwrt;
}

// analyseStorage(status): the picture of the device's storage the wizard draws and decides with
export function analyseStorage(status) {
  const disk = status.disk * 512;
  const parts = (status.parts || []).map(([name, start, size]) => ({ name, start: start * 512, size: size * 512 }))
    .sort((a, b) => a.start - b.start);
  // a GPT partition named mu300root is not Android's: another installer made it for Linux (kanoqwq's desktop
  // installer), and the region is that partition (region.part; tools/storage.sh, FINDINGS 179)
  const android = parts.filter((p) => p.name !== "mu300root");
  const last = android.length ? android[android.length - 1] : null;
  const region = status.region;
  const internal = { off: region.off, size: region.size, existing: region.existing === "yes", dirty: region.dirty,
    part: !!region.part, partFs: region.part_fs || "none", partCount: region.part_count || 0, foreignImage: !!region.foreign_image };
  if (internal.partCount > 1) internal.verdict = "part-ambiguous";
  else if (internal.part && !internal.existing && internal.partFs === "ext4") internal.verdict = "part-foreign";
  else if (internal.existing) internal.verdict = internal.part ? "part-existing" : "existing";
  else if (internal.size < SD_MIN) internal.verdict = "too-small";
  else if (internal.part) internal.verdict = internal.dirty > 0 ? "part-dirty" : "part-free";
  else if (internal.dirty > 0) internal.verdict = "dirty";
  else if (internal.size >= TESTED_LAYOUT) internal.verdict = "tested-layout";
  else if (internal.size >= NEED.both) internal.verdict = "both";
  else if (internal.size >= NEED.ubuntu) internal.verdict = "one";
  else internal.verdict = "openwrt-only";
  internal.usable = internal.verdict !== "part-ambiguous" && internal.verdict !== "part-foreign" &&
    (internal.existing || internal.size >= SD_MIN);

  const sd = { present: !!status.sd.dev, dev: status.sd.dev, size: status.sd.bytes, small: !!status.sd.small, existing: status.sd.existing };
  sd.usable = sd.present && sd.existing !== "foreign";

  // Making room (the 32 GB variant): userdata is the last partition and fills the disk. Only its end would move.
  // The page shows the figures; the repartitioning itself stays with the installer for computers.
  let makeRoom = null;
  if (!internal.usable && !internal.part && !internal.partCount && last && last.name === "userdata") {
    const usableEnd = (status.disk - 34) * 512;
    const total = usableEnd - last.start;
    makeRoom = { userdata: last.size, freeBehind: Math.max(0, usableEnd - (last.start + last.size)), total,
      maxLinux: total - MIN_ANDROID, minLinux: NEED.openwrt, possible: total - MIN_ANDROID >= NEED.openwrt };
  }

  // the bar: Android's own partitions, userdata, then Linux or the free space, to scale
  const bar = [];
  if (disk > 0 && last) {
    const ud = last && last.name === "userdata" ? last : null;
    const systemEnd = ud ? ud.start : last.start + last.size;
    bar.push({ kind: "android-system", size: systemEnd });
    if (ud) bar.push({ kind: "userdata", size: ud.size });
    const end = last.start + last.size;
    if (internal.existing || internal.part) {
      if (internal.off > end) bar.push({ kind: "gap", size: internal.off - end });
      bar.push({ kind: internal.existing ? "linux" : "free", size: Math.min(internal.size, disk - internal.off) });
      const rest = disk - internal.off - internal.size;
      if (rest > 0) bar.push({ kind: "free", size: rest });
    } else if (disk > end) {
      bar.push({ kind: "free", size: disk - end });
    }
  }

  // where an installation goes when nobody says: a card that holds one (it is what boots), else inside when there
  // is room, else a usable card
  let target = null;
  if (sd.usable && sd.existing === "yes") target = "sd";
  else if (internal.usable) target = "internal";
  else if (sd.usable) target = "sd";
  return { disk, parts, internal, sd, makeRoom, bar, target };
}

export function targetSize(analysis, target) { return target === "sd" ? analysis.sd.size : analysis.internal.size; }
export function targetExisting(analysis, target) { return target === "sd" ? analysis.sd.existing === "yes" : analysis.internal.existing; }

// What must be true before the page may talk about installing at all: [{ code }] for each thing that is not
export function deviceBlockers(status) {
  const b = [];
  if (!status.device) b.push({ code: "unknown-model" });
  if (!status.slots_ok) b.push({ code: "no-slots" });
  if (!status.misc || !status.misc.valid) b.push({ code: "misc-invalid" });
  if (!status.magiskboot) b.push({ code: status.root_kind === "magisk" ? "no-magiskboot" : "not-magisk" });
  if (status.stalled) b.push({ code: "stalled" });
  if (!status.boot_completed) b.push({ code: "still-booting" });
  return b;
}

export function validPassword(p) {
  if (p === "") return true;                                   // the images' own
  if (typeof p !== "string" || p.length < 6 || p.length > 128) return false;
  if (/[\u0000-\u001f\u007f]/.test(p)) return false;           // one line, nothing a terminal would act on
  if (p !== p.trim()) return false;                            // the settings file drops blanks at both ends
  if (/^(".*"|'.*')$/.test(p)) return false;                   // ... and one pair of quotes around a value
  return true;
}

// validateChoices(choices, ctx) -> [{ code, ...detail }]; empty: the installer on the device has no reason to refuse
// choices: { target, systems: [system...], kernel, bootOs, defaultLinux, attempts, hotspot, gpu, vpn, mode, password,
//            eraseConfirmed, overwriteConfirmed }
// ctx: { status, analysis, release (github.js), zips: { system: asset } }
export function validateChoices(c, ctx) {
  const e = [];
  const { status, analysis } = ctx;
  const systems = c.systems || [];
  if (!systems.length) e.push({ code: "no-system" });
  const fams = systems.map(familyOf);
  if (new Set(fams).size !== fams.length || systems.length > 2) e.push({ code: "one-of-each" });
  if (!["5.4", "6.18", "7.2", "7.3"].includes(c.kernel)) e.push({ code: "no-kernel" });
  if (systems.includes("ubuntu-26.04") && c.kernel === "5.4") e.push({ code: "ubuntu-2604-needs-mainline" });
  if (status && status.device === "v50" && c.kernel === "5.4") e.push({ code: "v50-needs-mainline" });   // FINDINGS 95
  for (const s of systems) if (ctx.zips && !ctx.zips[s]) e.push({ code: "no-zip", system: s, kernel: c.kernel });

  if (c.target !== "internal" && c.target !== "sd") { e.push({ code: "no-target" }); return e; }
  if (c.target === "sd" && !analysis.sd.present) e.push({ code: "no-card" });
  if (c.target === "sd" && analysis.sd.existing === "foreign") e.push({ code: "card-foreign" });
  // another installer's partition named mu300root that cannot be used says why (two of them, another ext4 in it)
  if (c.target === "internal" && !analysis.internal.usable) {
    const v = analysis.internal.verdict;
    e.push({ code: v === "part-foreign" || v === "part-ambiguous" ? v : "internal-too-small" });
  }
  // init starts a mu300sd card before anything inside: an installation inside would never start
  if (c.target === "internal" && analysis.sd.existing === "yes") e.push({ code: "card-boots-first" });

  const existing = targetExisting(analysis, c.target);
  const keep = existing && c.mode !== "wipe";
  if (existing && c.mode !== "update" && c.mode !== "wipe") e.push({ code: "no-mode" });
  const formats = !keep;
  if (c.target === "sd" && formats && !c.eraseConfirmed) e.push({ code: "confirm-erase" });
  if (c.target === "internal" && !existing && analysis.internal.dirty > 0 && !c.overwriteConfirmed) e.push({ code: "confirm-overwrite" });

  // what the filesystem holds afterwards: what is there and kept, plus what is installed now
  const have = keep && status.linux && status.linux.systems ? Object.keys(status.linux.systems) : [];
  const after = [...new Set([...have, ...systems.map(osOf)])];
  const need = needFor(after);
  const size = targetSize(analysis, c.target);
  if (systems.length && size < need) e.push({ code: "too-small", need, size });
  if (keep && c.kernel === "5.4" && have.includes("ubuntu") && status.linux.ubuntu === "26.04" && !systems.includes("ubuntu-24.04")) {
    e.push({ code: "installed-2604-needs-mainline" });
  }
  if (systems.length && !after.includes(c.bootOs)) e.push({ code: "boot-os-not-installed" });
  if (![1, 2, 3, 4, 5, 6].includes(c.attempts)) e.push({ code: "attempts" });
  if (!validPassword(c.password || "")) e.push({ code: "password" });
  return e;
}

// How much free space /data needs before the zip of one run is pushed: the zip itself (kept in root's directory
// while its installer runs), the installer's copy of the payload, and the 512 MiB the installer insists on.
export function dataNeeded(zipSize, extraSize = 0) { return zipSize * 2 + extraSize * 2 + 512 * MIB; }

// answersFor(choices, run, ctx): the text of the answers file of run number `run` (0 or 1: a second system is a
// second zip). ctx: { lang, adbKeyLine, status, analysis }. The password is not in it: it goes to the device on the
// stdin of a root command, never through a file the adb user can read.
export function answersFor(c, run, ctx) {
  const { status, analysis } = ctx;
  const existing = targetExisting(analysis, c.target);
  const keep = existing && c.mode !== "wipe";
  const lines = [`MU300_LANG=${["en", "tr", "zh"].includes(ctx.lang) ? ctx.lang : "en"}`, `MU300_STORAGE=${c.target}`];
  if (run === 0) {
    lines.push(`MU300_MODE=${keep || !existing ? "update" : "wipe"}`);
    if (c.target === "sd" && !keep && c.eraseConfirmed) lines.push("MU300_SD_ERASE=yes");
    if (c.target === "internal" && !existing && analysis.internal.dirty > 0 && c.overwriteConfirmed) lines.push("MU300_REGION_OVERWRITE=yes");
  } else {
    // the first run made the filesystem and put a system on it: never anything that erases
    lines.push("MU300_MODE=update");
  }
  // the system that boots must be on the filesystem when this run ends, or the installer refuses the setting
  const have = keep && status.linux && status.linux.systems ? Object.keys(status.linux.systems) : [];
  const soFar = new Set([...have, ...c.systems.slice(0, run + 1).map(osOf)]);
  if (soFar.has(c.bootOs)) lines.push(`MU300_BOOT_OS=${c.bootOs}`);
  lines.push(`MU300_BOOT=${c.defaultLinux ? "linux" : "android"}`, `MU300_BOOT_ATTEMPTS=${c.attempts}`,
    `MU300_HOTSPOT=${c.hotspot ? "yes" : "no"}`, `MU300_GPU=${c.gpu ? "yes" : "no"}`);
  if (ctx.adbKeyLine) lines.push(`MU300_ADB_KEY=${ctx.adbKeyLine}`);
  return lines.join("\n") + "\n";
}

// ---- the installer's lines

// MU300-PLAN key=value lines -> { key: value }
export function parsePlan(lines) {
  const plan = {};
  for (const l of lines) {
    const m = /^MU300-PLAN ([a-z_]+)=(.*)$/.exec(l);
    if (m) plan[m[1]] = m[2];
  }
  return plan;
}

// One line of a job's output, sorted: { type: "step", n, of, name } | { type: "plan", key, value } |
// { type: "progress", done, total } | { type: "exit", rc } | { type: "error", text } (the installer's "! ..."
// lines) | { type: "text", text }
export function classifyLine(line) {
  let m;
  if ((m = /^MU300-STEP (\d+)\/(\d+) ([a-z-]+)$/.exec(line))) return { type: "step", n: Number(m[1]), of: Number(m[2]), name: m[3] };
  if ((m = /^MU300-PLAN ([a-z_]+)=(.*)$/.exec(line))) return { type: "plan", key: m[1], value: m[2] };
  if ((m = /^MU300-PROGRESS (\d+) (\d+)$/.exec(line))) return { type: "progress", done: Number(m[1]), total: Number(m[2]) };
  if ((m = /^MU300-WEB-EXIT (\d+|interrupted)$/.exec(line))) return { type: "exit", rc: m[1] === "interrupted" ? "interrupted" : Number(m[1]) };
  if (line.startsWith("! ")) return { type: "error", text: line.slice(2) };
  return { type: "text", text: line };
}

// What a cut - the tab closed, the cable pulled, the power gone - leaves behind at each stage of an installation.
// The words for it are the page's (i18n); this is the classification the page and the tests share.
//   nothing   nothing has been written to the eMMC or the card
//   systems   the Linux filesystem is being written: an unfinished system, which nothing boots yet (the boot
//             partition of the Linux slot and misc are as they were, so the device starts Android)
//   boot      the Linux slot's boot partition is being written: Android's own slot and misc are untouched, so the
//             device starts Android; the half-written image is never the boot target
//   arm       32 bytes of misc are written, read back, and written back if they do not verify: either Android's
//             block or the Linux trial block is there
//   done      Linux is armed for a trial boot: a Linux that does not start falls back to Android by itself
export function stageRisk(name) {
  if (["check", "unpack", "plan", "vendor", "image"].includes(name)) return "nothing";
  if (["systems", "boot", "arm", "done"].includes(name)) return name;
  return "nothing";
}

export function fmtSize(bytes) {
  if (bytes >= GIB) return (bytes / GIB).toFixed(1) + " GiB";
  if (bytes >= MIB) return Math.round(bytes / MIB) + " MiB";
  return Math.round(bytes / 1024) + " KiB";
}
