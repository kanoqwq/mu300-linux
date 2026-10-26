// The release tags in JavaScript (site/assets/web-installer/version.js, and its copy in the panel's update.js) against
// the table every language is held to (tests/fixtures/release-tags.tsv, docs/FINDINGS.md 125), and what the web
// installer does with a four-field release in GitHub's list.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

import { newerTag, releaseKey } from "../../site/assets/web-installer/version.js";
import { LAST_RELEASE_WITHOUT_WEB, loadReleases, releaseSupportsWeb } from "../../site/assets/web-installer/github.js";

const TOP = new URL("../../", import.meta.url);
const table = fs.readFileSync(new URL("tests/fixtures/release-tags.tsv", TOP), "utf8").split("\n")
  .filter((l) => l && !l.startsWith("#")).map((l) => l.split("\t"));

function check(key, newer, where) {
  for (const row of table) {
    if (row[0] === "ok") assert.notEqual(key(row[1]), null, `${where}: ${row[1]} is a release tag`);
    if (row[0] === "bad") assert.equal(key(row[1]), null, `${where}: [${row[1]}] is not`);
    if (row[0] !== "cmp") continue;
    const [, a, r, b] = row;
    const got = newer(a, b) ? (newer(b, a) ? "both" : ">") : newer(b, a) ? "<" : key(a) && key(b) ? "=" : "!";
    assert.equal(got, r, `${where}: ${a} ${r} ${b}`);
  }
}

test("release tags: version.js follows the table", () => {
  check(releaseKey, newerTag, "version.js");
  assert.equal(releaseKey(undefined), null);
  assert.equal(newerTag(null, "v2026.10.27"), false);
});

test("release tags: the panel's update.js has the same two functions and follows the table", () => {
  const fns = (src) => ["releaseKey", "newerTag"].map((n) => {
    const m = new RegExp(`\\nfunction ${n}\\([^)]*\\) \\{\\n[\\s\\S]*?\\n\\}\\n`).exec("\n" + src);
    assert.ok(m, n);
    return m[0];
  });
  const lib = fs.readFileSync(new URL("site/assets/web-installer/version.js", TOP), "utf8");
  const panel = fs.readFileSync(new URL("openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/update.js", TOP), "utf8");
  assert.deepEqual(fns(panel), fns(lib));
  const ctx = {};
  vm.runInNewContext(fns(panel).join("\n") + "\nthis.k = releaseKey; this.n = newerTag;", ctx);
  check(ctx.k, ctx.n, "update.js");
});

test("releases: a four-field release is newer than every three-field one, and the web installer takes it", async () => {
  const rel = (tag, published_at, prerelease = false) => ({ tag_name: tag, prerelease, draft: false, published_at, assets: [] });
  const fetchOf = (body) => async () => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
  let r = await loadReleases(fetchOf([rel("v2026.10.16.1", "2026-10-16T10:00:00Z"), rel("v2026.10.27", "2026-10-15T07:56:46Z")]));
  assert.equal(r.stable.tag, "v2026.10.16.1");
  assert.equal(releaseSupportsWeb(r.stable), true);
  // the same moment: the tag decides, .10 after .9
  r = await loadReleases(fetchOf([rel("v2026.10.16.9", "2026-10-16T10:00:00Z"), rel("v2026.10.16.10", "2026-10-16T10:00:00Z")]));
  assert.equal(r.stable.tag, "v2026.10.16.10");
  // a prerelease published later, but older by its tag, is no beta
  r = await loadReleases(fetchOf([rel("v2026.10.16.2", "2026-10-16T10:00:00Z"), rel("v2026.10.27", "2026-10-17T10:00:00Z", true)]));
  assert.equal(r.beta, null);
  r = await loadReleases(fetchOf([rel("v2026.10.27", "2026-10-15T10:00:00Z"), rel("v2026.10.16.1", "2026-10-16T10:00:00Z", true)]));
  assert.equal(r.beta.tag, "v2026.10.16.1");
  for (const t of ["v2026.10.16.1", "v2026.10.01.1", "v2026.10.24"]) assert.equal(releaseSupportsWeb({ tag: t }), true, t);
  for (const t of [LAST_RELEASE_WITHOUT_WEB, "v2026.10.19", "v2026.10.23-fix1"]) assert.equal(releaseSupportsWeb({ tag: t }), false, t);
  assert.equal(releaseSupportsWeb({ tag: "test-issue-102" }), true);   // not a dated tag: decided by the zip
});
