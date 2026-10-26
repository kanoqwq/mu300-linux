// site/assets/web-installer/mirror.js: a release's zip downloaded by the browser from the project's mirror, in
// parts. Nothing the mirror says is trusted: the file must be the one GitHub's API names for the release, a part
// must be what the manifest says, nothing is followed to another address, and a short or changed download is an
// error, never a file handed on.
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";

import { MirrorError, PART_MAX, downloadZip, loadMirror, mirrorUrl } from "../../site/assets/web-installer/mirror.js";
import { MIRROR_BASE } from "../../site/assets/web-installer/config.js";

const BASE = "https://raw.githubusercontent.com/someone/mu300-linux-mirror/";
const TAG = "v2026.11.01";
const NAME = `mu300-magisk-${TAG}-openwrt-k6.18.zip`;
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const code = (c) => (e) => { assert.ok(e instanceof MirrorError, String(e && e.stack)); assert.equal(e.code, c); return true; };

// a zip of 2500 bytes in parts of 1000, as tools/make-release-mirror.sh cuts it
function mirror() {
  const zip = crypto.randomBytes(2500);
  const parts = [zip.subarray(0, 1000), zip.subarray(1000, 2000), zip.subarray(2000)];
  const manifest = { v: 1, tag: TAG, files: { [NAME]: { size: zip.length, sha256: sha(zip),
    parts: parts.map((p, i) => ({ name: `${NAME}.${String(i).padStart(3, "0")}`, size: p.length, sha256: sha(p) })) } } };
  const asset = { name: NAME, size: zip.length, sha256: sha(zip) };
  const files = new Map([[`${BASE}${TAG}/manifest.json`, Buffer.from(JSON.stringify(manifest))]]);
  parts.forEach((p, i) => files.set(`${BASE}${TAG}/${NAME}.${String(i).padStart(3, "0")}`, Buffer.from(p)));
  const asked = [];
  const fetchImpl = async (url, opts) => {
    asked.push({ url, opts });
    if (!files.has(url)) return new Response("not found", { status: 404 });
    const v = files.get(url);
    if (v instanceof Error) throw v;
    return v instanceof Response ? v : new Response(v, { status: 200 });
  };
  return { zip, parts, manifest, asset, files, asked, fetchImpl };
}

test("the mirror the page ships with is the project's, at an address the page accepts; without one it asks for none", () => {
  assert.equal(MIRROR_BASE, "https://raw.githubusercontent.com/dikeckaan/mu300-linux-mirror/");
  assert.equal(mirrorUrl(MIRROR_BASE, TAG, "manifest.json"), `https://raw.githubusercontent.com/dikeckaan/mu300-linux-mirror/${TAG}/manifest.json`);
  assert.equal(mirrorUrl("", TAG, "manifest.json"), null);
});

test("a mirror's address is a repository on raw.githubusercontent.com over https, and nothing else", () => {
  assert.equal(mirrorUrl(BASE, TAG, "manifest.json"), `${BASE}${TAG}/manifest.json`);
  for (const base of ["", "http://raw.githubusercontent.com/a/b/", "https://raw.githubusercontent.com/a/b", "https://raw.githubusercontent.com/a/",
    "https://raw.githubusercontent.com.evil.example/a/b/", "https://evil.example/a/b/", "https://raw.githubusercontent.com/a/b/c/", "https://raw.githubusercontent.com/../b/",
    "https://user@raw.githubusercontent.com/a/b/", "//raw.githubusercontent.com/a/b/", null, 5]) {
    assert.equal(mirrorUrl(base, TAG, "manifest.json"), null, String(base));
  }
  for (const tag of ["", "main", "v1/../x", "v1..2", "v1 2", "../v1", "v1?x"]) assert.equal(mirrorUrl(BASE, tag, "manifest.json"), null, tag);
  for (const name of ["", "../x", "a/b", "a..b", ".hidden", "x y", "x?y"]) assert.equal(mirrorUrl(BASE, TAG, name), null, name);
});

test("the manifest: read, and every value checked for its shape", async () => {
  const m = mirror();
  const got = await loadMirror(BASE, TAG, m.fetchImpl);
  assert.deepEqual(got, { tag: TAG, files: m.manifest.files });
  // asked without credentials, and a redirect is an error, not something to follow
  assert.equal(m.asked[0].url, `${BASE}${TAG}/manifest.json`);
  assert.equal(m.asked[0].opts.redirect, "error");
  assert.equal(m.asked[0].opts.credentials, "omit");
  // no mirror for this release, or none at all
  await assert.rejects(loadMirror(BASE, "v2026.12.01", m.fetchImpl), code("none"));
  await assert.rejects(loadMirror("", TAG, m.fetchImpl), code("none"));
  await assert.rejects(loadMirror("https://evil.example/a/b/", TAG, m.fetchImpl), code("none"));
  const bad = (change) => { const x = mirror(); const man = JSON.parse(JSON.stringify(x.manifest)); change(man, man.files[NAME]); x.files.set(`${BASE}${TAG}/manifest.json`, Buffer.from(JSON.stringify(man))); return loadMirror(BASE, TAG, x.fetchImpl); };
  await assert.rejects(bad((man) => { man.v = 2; }), code("bad-manifest"));
  await assert.rejects(bad((man) => { man.tag = "v1"; }), code("bad-manifest"));
  await assert.rejects(bad((man, f) => { f.size += 1; }), code("bad-manifest"), "the parts must add up to the file");
  await assert.rejects(bad((man, f) => { f.sha256 = "x"; }), code("bad-manifest"));
  await assert.rejects(bad((man, f) => { f.parts = []; }), code("bad-manifest"));
  await assert.rejects(bad((man, f) => { f.parts[1].sha256 = "0"; }), code("bad-manifest"));
  await assert.rejects(bad((man, f) => { f.parts[0].size = PART_MAX + 1; f.size += PART_MAX + 1 - 1000; }), code("bad-manifest"), "a part larger than a repository may hold");
  // a part is this zip's, numbered: a manifest cannot make the page ask for another file
  for (const name of ["../../../other/repo/main/x", "https://evil.example/x", `${NAME}.001`, "manifest.json", `${NAME}.000/../x`]) {
    await assert.rejects(bad((man, f) => { f.parts[0].name = name; }), code("bad-manifest"), name);
  }
  await assert.rejects(bad((man) => { man.files["../evil.zip"] = man.files[NAME]; }), code("bad-manifest"));
  const junk = mirror(); junk.files.set(`${BASE}${TAG}/manifest.json`, Buffer.from("<html>"));
  await assert.rejects(loadMirror(BASE, TAG, junk.fetchImpl), code("bad-manifest"));
  const down = mirror(); down.files.set(`${BASE}${TAG}/manifest.json`, new TypeError("Failed to fetch"));
  await assert.rejects(loadMirror(BASE, TAG, down.fetchImpl), code("network"));
  const err = mirror(); err.files.set(`${BASE}${TAG}/manifest.json`, new Response("x", { status: 500 }));
  await assert.rejects(loadMirror(BASE, TAG, err.fetchImpl), code("network"));
});

test("the zip, put together from its parts, is the release's file byte for byte", async () => {
  const m = mirror();
  const man = await loadMirror(BASE, TAG, m.fetchImpl);
  const prog = [];
  const blob = await downloadZip(BASE, man, m.asset, { fetchImpl: m.fetchImpl, onProgress: (d, t) => prog.push([d, t]) });
  assert.equal(sha(Buffer.from(await blob.arrayBuffer())), m.asset.sha256);
  assert.equal(blob.size, 2500);
  assert.deepEqual(prog[0], [0, 2500]);
  assert.deepEqual(prog[prog.length - 1], [2500, 2500]);
  assert.ok(prog.every(([d], i) => i === 0 || d >= prog[i - 1][0]), "progress never goes back");
  // the parts were asked for at the mirror's own address, in order, redirects refused
  assert.deepEqual(m.asked.slice(1).map((a) => a.url), [0, 1, 2].map((i) => `${BASE}${TAG}/${NAME}.00${i}`));
  assert.ok(m.asked.every((a) => a.opts.redirect === "error" && a.opts.credentials === "omit"));
});

test("a mirror whose copy is not the release's file is not used at all", async () => {
  const m = mirror();
  const man = await loadMirror(BASE, TAG, m.fetchImpl);
  const before = m.asked.length;
  // the release (GitHub's API) names another checksum, or another size, than the mirror's manifest
  await assert.rejects(downloadZip(BASE, man, { ...m.asset, sha256: "a".repeat(64) }, { fetchImpl: m.fetchImpl }), code("mismatch"));
  await assert.rejects(downloadZip(BASE, man, { ...m.asset, size: 2501 }, { fetchImpl: m.fetchImpl }), code("mismatch"));
  await assert.rejects(downloadZip(BASE, man, { ...m.asset, name: `mu300-magisk-${TAG}-ubuntu-24.04-k6.18.zip` }, { fetchImpl: m.fetchImpl }), code("none"));
  assert.equal(m.asked.length, before, "nothing was downloaded for a file the mirror does not hold as the release has it");
});

test("a part that is changed, short, long or missing ends the download with an error", async () => {
  const run = async (change) => { const m = mirror(); const man = await loadMirror(BASE, TAG, m.fetchImpl); change(m); return downloadZip(BASE, man, m.asset, { fetchImpl: m.fetchImpl }); };
  const part = (i) => `${BASE}${TAG}/${NAME}.00${i}`;
  // one byte of a part changed on the way: the same length, another checksum
  await assert.rejects(run((m) => { const b = Buffer.from(m.parts[1]); b[10] ^= 1; m.files.set(part(1), b); }), code("mismatch"));
  // a truncated download
  await assert.rejects(run((m) => { m.files.set(part(2), Buffer.from(m.parts[2].subarray(0, 100))); }), code("truncated"));
  await assert.rejects(run((m) => { m.files.set(part(0), Buffer.alloc(0)); }), code("truncated"));
  // more than the manifest says
  await assert.rejects(run((m) => { m.files.set(part(0), Buffer.concat([m.parts[0], Buffer.from("x")])); }), code("mismatch"));
  // a part that is not there, a server error, a connection that fails
  await assert.rejects(run((m) => { m.files.delete(part(1)); }), code("truncated"));
  await assert.rejects(run((m) => { m.files.set(part(1), new Response("x", { status: 503 })); }), code("network"));
  await assert.rejects(run((m) => { m.files.set(part(1), new TypeError("Failed to fetch")); }), code("network"));
  // the connection breaks in the middle of a part
  await assert.rejects(run((m) => {
    let n = 0;
    const body = new ReadableStream({ pull(c) { if (n++ === 0) c.enqueue(new Uint8Array(m.parts[0].subarray(0, 300))); else c.error(new TypeError("network error")); } });
    m.files.set(part(0), new Response(body, { status: 200 }));
  }), code("truncated"));
});

test("a redirect to another host is never followed: the browser is told to treat it as an error", async () => {
  const m = mirror();
  const man = await loadMirror(BASE, TAG, m.fetchImpl);
  // what fetch() does with redirect: "error" when the server answers 302: it rejects. A fetch that followed it
  // would hand over another host's bytes; this one is asked not to, and the page reports the failure.
  const followed = [];
  const fetchImpl = async (url, opts) => {
    if (url.endsWith(".001")) {
      if (opts.redirect !== "error") { followed.push(url); return new Response(Buffer.from(m.parts[1]), { status: 200 }); }
      throw new TypeError("Failed to fetch: redirect mode is set to error");
    }
    return m.fetchImpl(url, opts);
  };
  await assert.rejects(downloadZip(BASE, man, m.asset, { fetchImpl }), code("network"));
  assert.deepEqual(followed, []);
});

test("cancelling stops the download", async () => {
  const m = mirror();
  const man = await loadMirror(BASE, TAG, m.fetchImpl);
  const ac = new AbortController();
  const fetchImpl = async (url, opts) => { if (url.endsWith(".001")) { ac.abort(); throw new DOMException("aborted", "AbortError"); } return m.fetchImpl(url, opts); };
  await assert.rejects(downloadZip(BASE, man, m.asset, { fetchImpl, signal: ac.signal }), code("aborted"));
});
