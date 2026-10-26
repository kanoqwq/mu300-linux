// One HTTP(S) POST for mu300-sms-forward, with OpenWrt's own client (ucode's uclient module, libustream-ssl).
//
// The request comes on stdin, never in the command line, because the URL and the headers can hold a secret (a
// Telegram bot token, an Authorization header):
//   URL
//   Header: value          (any number, Content-Type among them)
//   <empty line>
//   body                   (to the end)
// One line goes to stdout: "status N" (the HTTP status), or "error WHAT" when no status came (the address could not
// be reached, TLS failed, no answer within the timeout). Nothing else: not the URL, not the reply's body.
//
// The one exception is asked for by the caller, never by the request: with MU300_FWD_ANSWER=N in the environment
// (N bytes, 65536 at most) the first N bytes of a 2xx reply's body follow the status line, for the caller's check of
// what the service answered (mu300-sms-forward answer_verdict reads them from this pipe and nowhere else). The rest
// of the reply is read and dropped as before; nothing is kept of any other reply.
//
// HTTPS checks the server's certificate against the system's CA bundle. Redirects are not followed (a 3xx is the
// status). uclient sends a POST body chunked (Transfer-Encoding: chunked), as HTTP/1.1 servers must accept.
// connect() is checked before request(): a request on a client that never connected crashes this ucode.
import * as uclient from 'uclient';
import * as uloop from 'uloop';
import { stdin } from 'fs';

const CA = '/etc/ssl/certs/ca-certificates.crt';
const TIMEOUT_MS = 15000;

let keep = +(getenv('MU300_FWD_ANSWER') ?? 0);
keep = (keep > 0) ? min(keep, 65536) : 0;
let answer = '';

function finish(line) {
	print(line, '\n');
	exit(0);
}

let req = stdin.read('all') ?? '';
let cut = index(req, '\n\n');
let head = (cut < 0) ? req : substr(req, 0, cut);
let body = (cut < 0) ? '' : substr(req, cut + 2);
let lines = split(head, '\n');
let url = trim(lines[0] ?? '');
if (!match(url, /^https?:\/\/[^\/[:space:]]+/))
	finish('error bad url');

let headers = {};
for (let i = 1; i < length(lines); i++) {
	let m = match(lines[i], /^([A-Za-z0-9-]+):[ \t]*(.*)$/);
	if (m)
		headers[m[1]] = m[2];
}

uloop.init();
let result = null;
let cl;
cl = uclient.new(url, null, {
	header_done: function() {},
	data_read: function() {
		let d;
		while (length(d = cl.read() ?? '') > 0)
			if (length(answer) < keep)
				answer += substr(d, 0, keep - length(answer));
	},
	data_eof: function() { result = result ?? 'eof'; uloop.end(); },
	error: function(c, code) { result = 'error ' + code; uloop.end(); },
});
if (!cl)
	finish('error bad url');
if (substr(url, 0, 8) == 'https://' && !cl.ssl_init({ verify: true, ca_files: [ CA ] }))
	finish('error tls setup');
cl.set_timeout(TIMEOUT_MS);
if (!cl.connect())
	finish('error connect');
if (!cl.request('POST', { headers: headers, post_data: body }))
	finish('error request');
uloop.run();

let st = cl.status() ?? {};
if (st.status >= 200 && st.status < 300 && keep > 0) {
	print('status ' + st.status + '\n', answer);
	exit(0);
}
if (st.status > 0)
	finish('status ' + st.status);
// uclient's error codes (UCLIENT_ERROR_*)
const WHY = { '1': 'connect', '2': 'timeout', '3': 'tls certificate', '4': 'tls name mismatch', '5': 'tls setup' };
if (result && substr(result, 0, 6) == 'error ')
	finish('error ' + (WHY[substr(result, 6)] ?? 'unknown'));
finish('error no answer');
