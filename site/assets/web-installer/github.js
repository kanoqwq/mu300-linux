// The releases, from GitHub's API (api.github.com answers cross-origin requests; the release files themselves do
// not - FINDINGS 72 - so the page never reads them from there: the device downloads its zip, or the browser gets it
// from a mirror, or the user downloads it and picks the file). What the API says is
// data from the network: every tag, name, size and checksum is checked for its shape before it is used, and a
// download link is only ever one into this project's releases.
import { newerTag, releaseKey } from "./version.js";

export const REPO = "dikeckaan/mu300-linux";
export const VPN_REPO = "dikeckaan/mu300-linux-vpn";
export const VPN_ASSET = "mu300-linux-vpn.tar.gz";
// the newest release whose Magisk zips do not know the web installer yet: its tag and every older one are not offered
export const LAST_RELEASE_WITHOUT_WEB = "v2026.10.23";

export const SYSTEMS = ["ubuntu-24.04", "ubuntu-26.04", "openwrt", "openwrt-luci"];
export const KERNELS = ["5.4", "6.18", "7.2", "7.3"];

export class GithubError extends Error {
  constructor(code, message, extra) { super(message || code); this.name = "GithubError"; this.code = code; Object.assign(this, extra || {}); }
}

const TAG = /^v[0-9][0-9A-Za-z._-]{0,40}$/;
const NAME = /^[0-9A-Za-z][0-9A-Za-z._-]{0,120}$/;

function cleanRelease(r, repo) {
  if (!r || typeof r.tag_name !== "string" || !TAG.test(r.tag_name) || r.tag_name.includes("..") || r.draft) return null;
  const assets = new Map();
  for (const a of Array.isArray(r.assets) ? r.assets : []) {
    if (!a || typeof a.name !== "string" || !NAME.test(a.name) || a.name.includes("..")) continue;
    if (!Number.isSafeInteger(a.size) || a.size <= 0) continue;
    const m = /^sha256:([0-9a-f]{64})$/.exec(typeof a.digest === "string" ? a.digest : "");
    assets.set(a.name, {
      name: a.name, size: a.size, sha256: m ? m[1] : null,
      // built here, never taken from the answer: a link on this page goes to this project's release and nowhere else
      url: `https://github.com/${repo}/releases/download/${encodeURIComponent(r.tag_name)}/${encodeURIComponent(a.name)}`,
    });
  }
  const t = Date.parse(r.published_at || "");
  return { tag: r.tag_name, prerelease: !!r.prerelease, published: Number.isFinite(t) ? t : 0, assets };
}

async function getJson(fetchImpl, url) {
  let res;
  try {
    res = await fetchImpl(url, { headers: { Accept: "application/vnd.github+json" }, credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" });
  } catch (e) { throw new GithubError("network", "GitHub could not be reached", { cause: e }); }
  if (res.status === 403 || res.status === 429) {
    const reset = Number(res.headers && res.headers.get && res.headers.get("x-ratelimit-reset")) || 0;
    throw new GithubError("rate-limit", "GitHub's limit for requests without an account is used up for now", { reset });
  }
  if (!res.ok) throw new GithubError("http", `GitHub answered ${res.status}`, { status: res.status });
  try { return await res.json(); } catch (e) { throw new GithubError("bad-answer", "GitHub's answer could not be read"); }
}

// by when it was published; the same moment: by the tag (the release tags' order, docs/FINDINGS.md 125)
const newer = (a, b) => (a.published !== b.published ? a.published > b.published : newerTag(a.tag, b.tag) || (!releaseKey(a.tag) && a.tag > b.tag));

// loadReleases() -> { stable, beta }: the newest release, and the newest prerelease when it is newer than that
// (else null: there is no beta to ask about)
export async function loadReleases(fetchImpl = globalThis.fetch.bind(globalThis), repo = REPO) {
  const list = await getJson(fetchImpl, `https://api.github.com/repos/${repo}/releases?per_page=30`);
  if (!Array.isArray(list)) throw new GithubError("bad-answer", "GitHub's answer could not be read");
  const rel = list.map((r) => cleanRelease(r, repo)).filter(Boolean);
  let stable = null, beta = null;
  for (const r of rel) {
    if (r.prerelease) { if (!beta || newer(r, beta)) beta = r; } else if (!stable || newer(r, stable)) stable = r;
  }
  if (!stable && !beta) throw new GithubError("no-release", "no release was found");
  // and a beta is newer than the release by its tag too, when both are release tags (as mu300-update decides)
  if (beta && stable && (!newer(beta, stable) || (releaseKey(beta.tag) && releaseKey(stable.tag) && !newerTag(beta.tag, stable.tag)))) beta = null;
  return { stable, beta };
}

export async function loadVpnModule(fetchImpl = globalThis.fetch.bind(globalThis), repo = VPN_REPO) {
  const r = cleanRelease(await getJson(fetchImpl, `https://api.github.com/repos/${repo}/releases/latest`), repo);
  const a = r && r.assets.get(VPN_ASSET);
  if (!a || !a.sha256) throw new GithubError("no-release", "the VPN module's release was not found");
  return { tag: r.tag, ...a };
}

export function zipName(tag, system, kernel) { return `mu300-magisk-${tag}-${system}-k${kernel}.zip`; }

// Can the web installer use this release at all? Its zips' installer must know MU300_ANSWERS; releases up to
// LAST_RELEASE_WITHOUT_WEB are known not to (the page still checks the zip itself once it is picked).
export function releaseSupportsWeb(release) {
  if (releaseKey(release.tag)) return newerTag(release.tag, LAST_RELEASE_WITHOUT_WEB);   // vYYYY.MM.DD(.N)
  if (!/^v\d{4}\.\d{2}\.\d{2}/.test(release.tag)) return true;      // not a dated tag: decided by the zip
  return release.tag.slice(0, 11) > LAST_RELEASE_WITHOUT_WEB;      // a three-field tag with something after it
}

// the zip of a release for a system and a kernel: { name, size, sha256, url } or null when the release has none
// (Ubuntu 26.04 with kernel 5.4, or a release from before a system or kernel existed) or names no checksum for it
export function findZip(release, system, kernel) {
  if (!SYSTEMS.includes(system) || !KERNELS.includes(kernel)) return null;
  const a = release.assets.get(zipName(release.tag, system, kernel));
  return a && a.sha256 ? a : null;
}

// { "ubuntu-24.04": ["5.4","6.18","7.2","7.3"], ... }: what the release has zips for
export function combos(release) {
  const out = {};
  for (const s of SYSTEMS) {
    const ks = KERNELS.filter((k) => findZip(release, s, k));
    if (ks.length) out[s] = ks;
  }
  return out;
}
