// The release tags (docs/FINDINGS.md 125): vYYYY.MM.DD up to v2026.10.27, where the "day" was a counter, and from then
// on vYYYY.MM.DD.N, the real (UTC) day the release was built and N = 1..99 for the releases of that day. Every
// four-field tag is newer than every three-field one; four-field tags compare by the date, then by N, as numbers.
// The two functions are written for the browsers of the control panel too (no arrows, no let): its update.js has them
// word for word (tests/test_releases.py holds both, and the other languages, to tests/fixtures/release-tags.tsv).
function releaseKey(t) {
	var m = /^v(\d{4})\.(\d\d)\.(\d\d)$/.exec(typeof t === 'string' ? t : '');
	if (m) return [ 1, +(m[1] + m[2] + m[3]), 0 ];
	m = /^v(\d{4})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.([1-9]\d?)$/.exec(typeof t === 'string' ? t : '');
	return m ? [ 2, +(m[1] + m[2] + m[3]), +m[4] ] : null;
}
function newerTag(a, b) {
	var x = releaseKey(a), y = releaseKey(b);
	if (!x || !y) return false;
	for (var i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
	return false;
}

export { releaseKey, newerTag };
