# SPDX-License-Identifier: MIT
"""LuCI session transport for a sibling CGI whose URL is outside the cookie path."""
import http.cookies
import json
import re

def session(environ, reader):
    if environ.get('REQUEST_METHOD') == 'POST':
        if environ.get('CONTENT_TYPE', '').split(';')[0].strip() != 'application/json':
            raise ValueError('JSON session body required')
        size = int(environ.get('CONTENT_LENGTH', '0'))
        if not 0 < size <= 1024:
            raise ValueError('Invalid session body size')
        body = json.loads(reader.read(size))
        sid = body.get('session') if isinstance(body, dict) else None
    else:
        cookie = http.cookies.SimpleCookie()
        cookie.load(environ.get('HTTP_COOKIE', ''))
        sid = next((cookie[k].value for k in ('sysauth_https', 'sysauth_http', 'sysauth') if k in cookie), '')
    if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{32}', sid):
        raise ValueError('Invalid session')
    return sid
