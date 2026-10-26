#!/usr/bin/env python3
"""Mechanical 5.4 -> 6.18 API fixes for the vendor out-of-tree drivers.

  port54.py DIR...   rewrites *.c in place:
  - file_operations passed to proc_create*() become proc_ops
  - platform_driver .remove callbacks return void
  - sysfs bin_attribute read/write callbacks take a const bin_attribute
"""
import re, sys, pathlib

PROC_RENAMES = {'open': 'proc_open', 'read': 'proc_read', 'write': 'proc_write', 'llseek': 'proc_lseek',
                'release': 'proc_release', 'poll': 'proc_poll', 'unlocked_ioctl': 'proc_ioctl',
                'compat_ioctl': 'proc_compat_ioctl', 'mmap': 'proc_mmap', 'read_iter': 'proc_read_iter'}

def body_span(s, start):
    """index range of the {...} block that starts at or after start"""
    i = s.index('{', start)
    depth = 0
    for j in range(i, len(s)):
        if s[j] == '{': depth += 1
        elif s[j] == '}':
            depth -= 1
            if depth == 0: return i, j
    raise ValueError('unbalanced')

def fix_proc_ops(s):
    names = set(re.findall(r'proc_create(?:_data|_seq_data)?\s*\([^;]*?&\s*(\w+)\s*[,)]', s, re.S))
    for n in names:
        m = re.search(r'(static\s+)?(const\s+)?struct\s+file_operations\s+' + n + r'\s*=\s*\{', s)
        if not m: continue
        a, b = body_span(s, m.start())
        body = s[a:b+1]
        body = re.sub(r'\n\s*\.owner\s*=\s*THIS_MODULE\s*,', '', body)
        body = re.sub(r'\.(\w+)(\s*)=', lambda x: '.' + PROC_RENAMES.get(x.group(1), x.group(1)) + x.group(2) + '=', body)
        s = s[:m.start()] + 'static const struct proc_ops ' + n + ' = ' + body + s[b+1:]
    return s

def fix_remove(s):
    for n in set(re.findall(r'\.remove\s*=\s*(\w+)\s*,', s)):
        m = re.search(r'static\s+int\s+' + n + r'\s*\(\s*struct\s+platform_device\s*\*\s*\w+\s*\)\s*\n?\{', s)
        if not m: continue
        a, b = body_span(s, m.start())
        body = s[a:b+1]
        body = re.sub(r'\breturn\s+[^;]+;', 'return;', body)
        head = s[m.start():a].replace('static int', 'static void', 1)
        s = s[:m.start()] + head + body + s[b+1:]
    return s

def fix_bin_attr(s):
    return re.sub(r'(struct\s+kobject\s*\*\s*\w+\s*,\s*)struct\s+bin_attribute\s*\*', r'\1const struct bin_attribute *', s)

for d in sys.argv[1:]:
    for p in pathlib.Path(d).rglob('*.c'):
        s0 = p.read_text(errors='surrogateescape')
        s = fix_bin_attr(fix_remove(fix_proc_ops(s0)))
        if s != s0:
            p.write_text(s, errors='surrogateescape')
            print('fixed', p)
