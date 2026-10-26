// A release's Magisk zip, downloaded by this browser: the way for a device that has no internet of its own.
//
// GitHub does not let a page read a release's files (no Access-Control-Allow-Origin on github.com/.../releases/
// download nor on the object store it redirects to, FINDINGS 72). raw.githubusercontent.com does answer a page
// (Access-Control-Allow-Origin: *), but only for files of a repository, where a file may have at most 100 MB. So a
// mirror is a branch of a repository, named after the release's tag, that holds every zip of that release cut into
// parts of at most 45 MiB and a manifest.json that lists them (tools/make-release-mirror.sh writes it, the release
// workflow publishes it). Where it is, is MIRROR_BASE in config.js; empty means there is none, and the page then
// never asks for one.
//
// Nothing a mirror says is trusted. A part must have the size and the SHA-256 the manifest names; the parts must
// add up to the size the release names for the zip, and the whole file must have the SHA-256 that GitHub's API
// gives for the release's own asset - the same checksum the device computes again after the file was sent to it.
// A mirror that serves anything else gets an error, not an installation. Redirects are refused: a part comes from
// the mirror's own address or not at all.
import { Sha256 } from "./sha256.js";

export const PART_MAX = 45 * 1024 * 1024;

export class MirrorError extends Error {
  // code: none (no mirror, or not for this release) | network | truncated | mismatch | bad-manifest | aborted
  constructor(code, message) { super(message || code); this.name = "MirrorError"; this.code = code; }
}

const TAG = /^v[0-9][0-9A-Za-z._]{0,40}$/;
const ZIP = /^mu300-magisk-v[0-9][A-Za-z0-9._-]{0,80}\.zip$/;
const HEX = /^[0-9a-f]{64}$/;

// The mirror's address for a tag, or null when the configured base is not one this page talks to: https, a
// raw.githubusercontent.com address of a repository ("https://raw.githubusercontent.com/OWNER/REPO/"), nothing else.
export function mirrorUrl(base, tag, name) {
  if (typeof base !== "string" || !/^https:\/\/raw\.githubusercontent\.com\/[A-Za-z0-9._-]{1,60}\/[A-Za-z0-9._-]{1,100}\/$/.test(base) || base.includes("..")) return null;
  if (!TAG.test(tag || "") || tag.includes("..")) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,140}$/.test(name || "") || name.includes("..")) return null;
  return `${base}${tag}/${name}`;
}

async function get(fetchImpl, url, signal) {
  let res;
  try {
    // redirect: "error" - the mirror answers itself or the download fails; nothing follows it to another host
    res = await fetchImpl(url, { redirect: "error", credentials: "omit", referrerPolicy: "no-referrer", cache: "no-store", signal });
  } catch (e) {
    if (signal && signal.aborted) throw new MirrorError("aborted", "cancelled");
    throw new MirrorError("network", "the mirror could not be reached");
  }
  return res;
}

// What the mirror has for a release: { tag, files: { zipName: { size, sha256, parts: [{ name, size, sha256 }] } } },
// every value checked for its shape. Rejects with "none" when there is no mirror for this tag.
export async function loadMirror(base, tag, fetchImpl = globalThis.fetch.bind(globalThis), signal) {
  const url = mirrorUrl(base, tag, "manifest.json");
  if (!url) throw new MirrorError("none");
  const res = await get(fetchImpl, url, signal);
  if (res.status === 404) throw new MirrorError("none", "the mirror has no copy of this release");
  if (!res.ok) throw new MirrorError("network", `the mirror answered ${res.status}`);
  let m;
  try { m = await res.json(); } catch (e) { throw new MirrorError("bad-manifest"); }
  if (!m || m.v !== 1 || m.tag !== tag || !m.files || typeof m.files !== "object") throw new MirrorError("bad-manifest");
  const files = {};
  for (const [name, f] of Object.entries(m.files)) {
    if (!ZIP.test(name) || name.includes("..") || !f || !Number.isSafeInteger(f.size) || f.size <= 0 || !HEX.test(f.sha256 || "") || !Array.isArray(f.parts) || !f.parts.length || f.parts.length > 64) {
      throw new MirrorError("bad-manifest");
    }
    let sum = 0;
    const parts = f.parts.map((p, i) => {
      // a part is this zip's, numbered: no manifest can make the page ask for another file
      if (!p || p.name !== `${name}.${String(i).padStart(3, "0")}` || !Number.isSafeInteger(p.size) || p.size <= 0 || p.size > PART_MAX || !HEX.test(p.sha256 || "")) throw new MirrorError("bad-manifest");
      sum += p.size;
      return { name: p.name, size: p.size, sha256: p.sha256 };
    });
    if (sum !== f.size) throw new MirrorError("bad-manifest");
    files[name] = { size: f.size, sha256: f.sha256, parts };
  }
  return { tag, files };
}

const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");

// downloadZip(base, mirror, asset, options) -> Blob: the zip of the release, put together from the mirror's parts.
// asset: { name, size, sha256 } as GitHub's API names the release's own file (github.js): the mirror's copy must be
// that file. options: fetchImpl, onProgress(done, total), signal, cryptoImpl.
export async function downloadZip(base, mirror, asset, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch.bind(globalThis);
  const cryptoImpl = options.cryptoImpl || globalThis.crypto;
  const onProgress = options.onProgress || (() => {});
  const f = mirror && mirror.files && Object.prototype.hasOwnProperty.call(mirror.files, asset.name) ? mirror.files[asset.name] : null;
  if (!f) throw new MirrorError("none", "the mirror has no copy of this file");
  // the release decides what the file is; a mirror that describes another file is not used at all
  if (f.size !== asset.size || f.sha256 !== asset.sha256) throw new MirrorError("mismatch", "the mirror's copy is not the release's file");
  const blobs = [];
  const whole = new Sha256();
  let done = 0;
  onProgress(0, f.size);
  for (const p of f.parts) {
    const res = await get(fetchImpl, mirrorUrl(base, mirror.tag, p.name), options.signal);
    if (!res.ok) throw new MirrorError(res.status === 404 ? "truncated" : "network", `the mirror answered ${res.status} for a part`);
    const chunks = [];
    let got = 0;
    if (res.body && typeof res.body.getReader === "function") {
      const reader = res.body.getReader();
      for (;;) {
        let r;
        try { r = await reader.read(); } catch (e) {
          if (options.signal && options.signal.aborted) throw new MirrorError("aborted", "cancelled");
          throw new MirrorError("truncated", "the connection broke in the middle of a part");
        }
        if (r.done) break;
        got += r.value.length;
        if (got > p.size) { try { await reader.cancel(); } catch (e) { /* closing anyway */ } throw new MirrorError("mismatch", "a part is longer than the mirror's list says"); }
        chunks.push(r.value);
        onProgress(done + got, f.size);
      }
    } else {
      const all = new Uint8Array(await res.arrayBuffer());
      got = all.length; chunks.push(all);
    }
    if (got !== p.size) throw new MirrorError(got < p.size ? "truncated" : "mismatch", `a part arrived with ${got} of ${p.size} bytes`);
    const part = new Blob(chunks);
    const bytes = new Uint8Array(await part.arrayBuffer());
    if (hex(await cryptoImpl.subtle.digest("SHA-256", bytes)) !== p.sha256) throw new MirrorError("mismatch", "a part does not have the checksum the mirror names");
    whole.update(bytes);
    blobs.push(part);
    done += got;
    onProgress(done, f.size);
  }
  if (done !== asset.size || whole.hex() !== asset.sha256) throw new MirrorError("mismatch", "the file does not have the release's checksum");
  return new Blob(blobs, { type: "application/zip" });
}
