// The page's own code for a rooted Android (site/assets/web-installer/device.js and flow.js) against the real
// device script: a scripted adbd whose shell runs tools/android-web.sh and the Magisk installer on the fake device
// of tests/fakedevice.py. tests/test_android_web.py (EndToEnd) builds that device and the zip, runs this, and reads
// the JSON it prints; it is not a test file of its own (node --test does not pick it up).
//
// The environment is the fake device's (MU300_* paths), plus:
//   E2E_STAGE     the directory that stands for /data/local/tmp/mu300-web
//   E2E_SITE      device/ as site/tools/stage.sh staged it (the scripts and their SHA256SUMS)
//   E2E_BUSYBOX   busybox (Magisk's, on a device)
//   E2E_ZIP       a Magisk zip as a release has it;  E2E_ZIP_SHA256 its checksum
//   E2E_VPN       optional: a VPN module tarball
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { AdbConnection } from "../../site/assets/web-installer/adb/connection.js";
import { generateKey, publicKeyLine } from "../../site/assets/web-installer/adb/key.js";
import { AndroidDevice, STAGE, identify, memorySink } from "../../site/assets/web-installer/device.js";
import { analyseStorage, answersFor, deviceBlockers, parsePlan, validateChoices } from "../../site/assets/web-installer/flow.js";
import { inspectMagiskZip } from "../../site/assets/web-installer/zip.js";
import { FakeAdbd } from "./fake-adbd.mjs";

const E = process.env;
const stage = E.E2E_STAGE;
const local = (p) => path.join(stage, p.slice(STAGE.length));

// the device's files for adb's sync service: /data/local/tmp/mu300-web/... on disk
const files = {
  get: (p) => (p.startsWith(STAGE + "/") && fs.existsSync(local(p)) ? new Uint8Array(fs.readFileSync(local(p))) : undefined),
  has: (p) => p.startsWith(STAGE + "/") && fs.existsSync(local(p)),
  set: (p, data) => { if (!p.startsWith(STAGE + "/")) throw new Error("push outside the stage: " + p); fs.mkdirSync(path.dirname(local(p)), { recursive: true }); fs.writeFileSync(local(p), data); },
};

// adb shell: the command runs in a real shell, with the stage directory where the device has it and busybox as the
// shell of the device script (on a device the script finds Magisk's busybox itself)
async function* runShell(cmd, ctx) {
  const real = cmd.split(`sh ${STAGE}/android-web.sh`).join(`"${E.E2E_BUSYBOX}" sh "${stage}/android-web.sh"`).split(STAGE).join(stage);
  const child = spawn("sh", ["-c", real], { env: E, stdio: ["pipe", "pipe", "pipe"] });
  if (/android-web\.sh" password/.test(real)) { await ctx.waitStdin(); child.stdin.write(ctx.stdin); }
  child.stdin.end();
  const queue = [];
  let wake = null, done = null;
  const push = (x) => { queue.push(x); if (wake) { const w = wake; wake = null; w(); } };
  child.stdout.on("data", (d) => push({ out: d }));
  child.stderr.on("data", (d) => push({ err: d }));
  child.on("close", (rc) => { done = rc ?? 1; push(null); });
  try {
    for (;;) {
      while (queue.length) { const x = queue.shift(); if (x) yield x; }
      if (done !== null && !queue.length) return done;
      await new Promise((r) => { wake = r; });
    }
  } finally {
    if (done === null) child.kill("SIGHUP");            // the stream was closed: adbd sends the command SIGHUP
  }
}

async function connect() {
  const adbd = new FakeAdbd({ shell: runShell, files });
  const conn = new AdbConnection(adbd.transport(), { key: await generateKey("e2e") });
  await conn.connect("e2e");
  return { adbd, conn };
}

const out = {};
const log = [];
const PASSWORD = "correct horse battery";

const { conn } = await connect();
out.kind = (await identify(conn)).kind;
const dev = new AndroidDevice(conn, { log: (l) => log.push(l) });
const probe = await dev.probe();
out.model = probe.props["ro.product.model"];
out.lock = probe.lock;
out.su = probe.su;
out.root = await dev.root(20000);
await dev.stage(async (name) => new Uint8Array(fs.readFileSync(path.join(E.E2E_SITE, name))));

let status = await dev.status(true);
out.blockers = deviceBlockers(status).map((b) => b.code);
out.before = { linux_boot: status.linux_boot, region: status.region, job: status.job.state };

// a backup of what the installation is about to write, checked against the device while it is pulled
const store = new Map();
const backup = await dev.backup(memorySink(store));
out.backup = backup.files.map((f) => f.name);
out.backupSha = {};
for (const f of backup.files) out.backupSha[f.name] = crypto.createHash("sha256").update(Buffer.from(await store.get(f.name).arrayBuffer())).digest("hex");
out.backupMatches = backup.files.every((f) => out.backupSha[f.name] === f.sha256);

const zipBlob = await fs.openAsBlob(E.E2E_ZIP);
const zipName = path.basename(E.E2E_ZIP);
const inspected = await inspectMagiskZip(zipBlob);
out.inspected = inspected;

const analysis = analyseStorage(status);
const key = await generateKey("web-installer");
const choices = { target: analysis.target, systems: [inspected.system], kernel: inspected.kernel, bootOs: inspected.os, defaultLinux: true, attempts: 4,
  hotspot: false, gpu: true, vpn: !!E.E2E_VPN, mode: "update", password: PASSWORD, eraseConfirmed: false, overwriteConfirmed: false };
out.errors = validateChoices(choices, { status, analysis }).map((e) => e.code);

const taken = await dev.takeZip(zipBlob, zipName, E.E2E_ZIP_SHA256);
out.taken = taken;
if (E.E2E_VPN) {
  const vpn = await fs.openAsBlob(E.E2E_VPN);
  await dev.addExtra("vpn", vpn, crypto.createHash("sha256").update(fs.readFileSync(E.E2E_VPN)).digest("hex"));
}
const answers = answersFor(choices, 0, { lang: "en", status, analysis, adbKeyLine: publicKeyLine(key) });

// the plan: a dry run
await dev.setAnswers(answers, PASSWORD);
await dev.start("plan", zipName);
const planLines = [];
out.planRc = await dev.follow((e) => { if (e.type === "plan") planLines.push(`MU300-PLAN ${e.key}=${e.value}`); });
out.plan = parsePlan(planLines);
status = await dev.status();
out.afterPlan = { linux_boot: status.linux_boot, job: status.job };

// the installation; the first watcher goes away in the middle (the tab closed), a second one picks the job up
await dev.setAnswers(answers, PASSWORD);
await dev.start("install", zipName);
const steps1 = [];
const ac = new AbortController();
let cut = false;
try {
  await dev.follow((e) => { if (e.type === "step") { steps1.push(e.name); if (e.name === "systems") ac.abort(); } }, ac.signal);
} catch (e) { cut = e.code === "aborted"; }
out.watcherCut = cut;
out.stepsBeforeCut = steps1;
const again = await connect();
const dev2 = new AndroidDevice(again.conn, { log: (l) => log.push(l) });
const steps2 = [], errors = [];
out.installRc = await dev2.follow((e) => { if (e.type === "step") steps2.push(e.name); if (e.type === "error") errors.push(e.text); });
out.steps = steps2;
out.installErrors = errors;
status = await dev2.status(true);
out.after = { linux_boot: status.linux_boot, module: status.module, job: status.job, linux: status.linux || null, existing: status.region.existing };

if (E.E2E_UNINSTALL) {
  await dev2.start("uninstall", E.E2E_UNINSTALL, "keep");
  const usteps = [];
  out.uninstallRc = await dev2.follow((e) => { if (e.type === "step") usteps.push(e.name); });
  out.uninstallSteps = usteps;
  status = await dev2.status();
  out.final = { linux_boot: status.linux_boot, existing: status.region.existing, module: status.module };
}
await dev2.clean(true);
const text = log.join("\n");
out.logLines = log.length;
out.passwordInLog = text.includes(PASSWORD);
out.keyInLog = text.includes(publicKeyLine(key).slice(0, 60));
await again.conn.close();
await conn.close();
console.log("E2E-RESULT " + JSON.stringify(out));
process.exit(0);
