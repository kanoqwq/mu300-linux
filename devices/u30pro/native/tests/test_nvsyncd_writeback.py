#!/usr/bin/env python3
"""Offline test of tools/u30pro_nvsyncd.c (default mode and --writeback=dry).  Run by tests/run_nvsyncd_offline.sh inside WSL.

usage: test_nvsyncd_writeback.py BUILD_DIR
BUILD_DIR holds nvsyncd (new), nvsyncd-s2 (the deployed s2 source, tests/fixtures), nvwb_check (+ modem-native-re/src/nv.c) and
nvwb_openlog.so.  Inputs are only read: live snapshots of runtimenv1/2 taken on the device (build/kernel-port-20261017/p12 = before,
p15 = after the stock cp_diskserver ran under stock Android), the live factorynv (p12), the SPD backup of runtimenv1 (headerless)
and the CP's real snv_nr units (modem-native-re/evidence/snv-drain-20261023).

The synthetic "fixnv RAM" (what the CP holds in its fixnv region) is the image the loader builds from runtimenv + factorynv
(nvwb_check ddr = src/nv.c, the modem_control logic), so item offsets map as the CP sees them:
  DDR offset = 0x200 + dataOffset[core] + prefix + (flash data offset - fragment data start - fragment core offset)
  prefix = 0 for overwrite items, overwrite core size for inherit items (each core block is [overwrite][inherit][factory][pad][0xff])
"""
import json, os, pty, re, select, shutil, struct, subprocess, sys, time, tty

BUILD = sys.argv[1]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = REPO + '/build/kernel-port-20261017/'
P12, P15 = K + 'p12-before-a-r2/current-nv/', K + 'p15-before-control-nv/'
FAC = P12 + 'nr_factorynv.bin'
BACKUP_R1 = REPO + '/build/full-backup-20261016-2343/partitions/nr_runtimenv1.bin'
SNV = REPO + '/modem-native-re/evidence/snv-drain-20261023/snv_nr.bin'
W = '/tmp/nvwb-test'
UNIT, NVHDR = 0x40014, 0x200
LBA = {19: 661504, 20: 733184}
NAME = {19: 'nr_runtimenv1', 20: 'nr_runtimenv2'}


def ck(b, wide=False):
    s = sum(struct.unpack_from('<%dH' % (len(b) // 2), b)) + (b[-1] if len(b) & 1 else 0)
    if not wide:
        s &= 0xffffffff
    s = (s >> 16) + (s & 0xffff)
    return ~(s + (s >> 16)) & 0xffff


def frag(img, o):
    size, c, n = struct.unpack_from('<I', img, o + 4)[0], struct.unpack_from('<I', img, o + 8)[0], struct.unpack_from('<I', img, o + 16)[0]
    cores = [struct.unpack_from('<II', img, o + 0x24 + 0x18 * i) for i in range(n)]
    return size, c, cores


def layout(img):
    isz = frag(img, 0)[0]
    return [0, isz + NVHDR], [isz + NVHDR, frag(img, isz + NVHDR)[0] + NVHDR]   # starts, lengths


def entries(img, start):
    end, o, out = start + NVHDR + frag(img, start)[0], start + NVHDR + 4, {}
    while o + 4 <= end:
        i, l = struct.unpack_from('<HH', img, o)
        if i == 0xffff:
            break
        out[i] = (l, o + 4)
        o = (o + 4 + l + 3) & ~3
    return out


def pad(total):
    rem = total & 0x1ff
    return (0x200 if rem < 0x1fc else 0x400) - rem


def ddr_offset(img, fac, g, flash):
    starts, _ = layout(img)
    inh, ow, fc = frag(img, 0)[2], frag(img, starts[1])[2], frag(fac, 0)[2]
    fr = (inh, ow)[g]
    rel = flash - (starts[g] + NVHDR)
    core = max(i for i in range(len(fr)) if fr[i][0] <= rel)
    base = 0
    for i in range(core):
        p = ow[i][1] + inh[i][1] + fc[i][1]
        base += p + pad(p) + 0x10000
    return NVHDR + base + (ow[core][1] if g == 0 else 0) + rel - fr[core][0]


def unit(grp, sub, payload):
    return struct.pack('<5I', 0x524d4e56, 0, grp, sub, len(payload)) + payload + bytes(0x40000 - len(payload))


def tok(t, seq, a, b):
    return struct.pack('<4I', t, seq, a, b)


def item(i, l, off, c, g):
    return struct.pack('<HHIHH', i, l, off, c, g)


def reply(grp, sub):
    return struct.pack('<5I', 0x524d4e56, 0, grp, sub, 1) + b'\x01' + bytes(15)


def setup(r1, r2, partname=None, start=None):
    shutil.rmtree(W, ignore_errors=True)
    for d in ('dev', 'out', 'run'):
        os.makedirs(W + '/' + d)
    for n, src in ((19, r1), (20, r2)):
        shutil.copyfile(src, '%s/dev/mmcblk0p%d' % (W, n))
        s = '%s/sys/class/block/mmcblk0p%d' % (W, n)
        os.makedirs(s)
        open(s + '/uevent', 'w').write('MAJOR=179\nMINOR=%d\nDEVNAME=mmcblk0p%d\nDEVTYPE=partition\nPARTN=%d\nPARTNAME=%s\n'
                                       % (n, n, n, (partname or {}).get(n, NAME[n])))
        open(s + '/start', 'w').write('%d\n' % (start or {}).get(n, LBA[n]))
        open(s + '/size', 'w').write('71680\n')


def dry_args(ram):
    return ['--writeback=dry', '--out-dir', W + '/out', '--runtimenv1', W + '/dev/mmcblk0p19', '--runtimenv2', W + '/dev/mmcblk0p20',
            '--sysfs', W + '/sys', '--fixnv-ram', ram]


def run(binary, extra, units, chunk=None, junk=b'', env=None):
    """feed the units through a pty; returns (replies, per-unit latency, stdout, status json)"""
    m, s = pty.openpty()
    tty.setraw(m)
    name, st = os.ttyname(s), W + '/run/status.json'
    p = subprocess.Popen([binary, '--port', name, '--status', st] + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=env)
    time.sleep(0.4)
    replies, lat = b'', []
    if chunk:   # all at once in odd-sized chunks, as the old test did
        blob = junk + b''.join(units)
        for i in range(0, len(blob), chunk):
            os.write(m, blob[i:i + chunk])
    end = time.time() + 15
    for k, u in enumerate(units if not chunk else []):
        os.write(m, u)
        t0 = time.time()
        while len(replies) < 0x24 * (k + 1) and time.time() < end:
            if select.select([m], [], [], 0.05)[0]:
                replies += os.read(m, 4096)
        lat.append(time.time() - t0)
    while len(replies) < 0x24 * len(units) and time.time() < end:
        if select.select([m], [], [], 0.05)[0]:
            replies += os.read(m, 4096)
    time.sleep(0.2)
    p.terminate()
    out, err = p.communicate(timeout=10)
    os.close(m); os.close(s)
    assert p.returncode == 0, (p.returncode, err)
    return replies, lat, out, json.load(open(st)), name


def refused(extra, want):
    """a failed check switches the write-back off; the units are still answered and nothing goes into out-dir"""
    r, _, out, st, _ = run(BUILD + '/nvsyncd', extra, sample_units())
    assert r == reply(1, 0) + reply(2, 0) and 'WRITEBACK OFF: ' in out and want in out and st['writeback'] == 'off', (out, st)
    assert not os.listdir(W + '/out'), 'refused run wrote into out-dir'
    print('  write-back off as expected:', want)


def checker(mode, f, r1, r2):
    p = subprocess.run([BUILD + '/nvwb_check', mode, f, r1, r2, FAC], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr[-2000:]
    return p.stdout.strip()


def report(n):
    return json.load(open('%s/out/report-%06d.json' % (W, n)))


def sample_units():
    d = open(SNV, 'rb').read()
    assert len(d) == 2 * UNIT
    return [d[:UNIT], d[UNIT:]]


def test_default_unchanged():
    """the default mode must behave exactly like the deployed s2 build: replies, log lines, status file"""
    units = sample_units()
    got = []
    for b in ('nvsyncd-s2', 'nvsyncd'):
        setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
        r, _, out, st, name = run(BUILD + '/' + b, [], units, chunk=3001, junk=b'junk!')
        assert r == reply(1, 0) + reply(2, 0), r.hex()
        assert st['units'] == 2 and st['replies'] == 2 and st['items'] == 6 and st['resyncs'] >= 1
        got.append((re.sub(r'^\d+\.\d{3} ', 'T ', out.replace(name, 'PTY'), flags=re.M), json.dumps(st).replace(name, 'PTY')))
    assert got[0] == got[1], 'default mode differs from the s2 build'
    print('default mode: replies, log and status identical to the s2 build')


def test_golden_p12_to_p15():
    """the stock cp_diskserver turned p12 into p15 (ids 2498, 2900, 2957 + overwrite header checksum).  The same dirty items, read
    from a fixnv RAM built from p15, must turn p12 into p15 byte for byte."""
    a, b = open(P12 + 'nr_runtimenv2.bin', 'rb').read(), open(P15 + 'nr_runtimenv2.bin', 'rb').read()
    setup(P12 + 'nr_runtimenv1.bin', P12 + 'nr_runtimenv2.bin')
    ram = W + '/ram15.bin'
    checker('ddr', ram, P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    rb, fac = open(ram, 'rb').read(), open(FAC, 'rb').read()
    starts, lens = layout(b)
    L = starts[1] + lens[1]
    changed = [i for i in range(L) if a[i] != b[i]]
    ow = entries(b, starts[1])
    ids = sorted({i for i, (l, o) in ow.items() for x in changed if o <= x < o + l})
    assert ids == [2498, 2900, 2957], ids
    its = b''
    for i in ids:
        l, o = ow[i]
        d = ddr_offset(b, fac, 1, o)
        assert struct.unpack_from('<HH', rb, d - 4) == (i, l) and rb[d:d + l] == b[o:o + l], 'DDR mapping wrong for %d' % i
        its += item(i, l, d, ck(rb[d:d + l]), 2)
    u = unit(7, 0, tok(0, 0, 2, 1) + tok(1, 1, len(ids), 1) + its + tok(2, 2, 2, 1))
    r, lat, out, st, _ = run(BUILD + '/nvsyncd', dry_args(ram), [u])
    assert r == reply(7, 0)
    rep = report(1)
    assert [x['result'] for x in rep['items']] == ['applied'] * 3 and rep['header']['new_checksum'] == struct.unpack_from('<I', b, starts[1] + 8)[0]
    for k in (1, 2):
        sim = open('%s/out/runtimenv%d.img' % (W, k), 'rb').read()
        assert sim == b[:L], 'runtimenv%d.img differs from the stock result p15' % k
    spans = rep['copies'][0]['spans']
    assert spans[0] == [starts[1], NVHDR] and all(any(s <= ow[i][1] and ow[i][1] + ow[i][0] <= s + n for s, n in spans) for i in ids)
    print(checker('check', ram, W + '/out/runtimenv1.img', W + '/out/runtimenv2.img'))
    print('golden p12 -> p15: identical to what the stock cp_diskserver wrote; spans', spans, 'reply %.0f ms' % (lat[0] * 1000))


def with_checksum(old, want):
    """bytes of old's length whose 16-bit item checksum is want, differing from old if possible (word 0 is the free variable)"""
    if len(old) < 2:
        return old
    rest = old[2:]
    s = sum(struct.unpack_from('<%dH' % (len(rest) // 2), rest)) + (rest[-1] if len(rest) & 1 else 0)
    for w in range(0x10000):
        t = (s + w >> 16) + (s + w & 0xffff)
        if ~(t + (t >> 16)) & 0xffff == want and struct.pack('<H', w) != old[:2]:
            return struct.pack('<H', w) + rest
    if ck(old) == want and len(old) >= 4 and old[0:2] != old[2:4]:
        return old[2:4] + old[0:2] + old[4:]
    return old


def test_sample_replay():
    """snv_nr.bin (the CP's two real units) + skipped items + an inherit item over two units + a factory unit, against p15"""
    img, fac = open(P15 + 'nr_runtimenv2.bin', 'rb').read(), open(FAC, 'rb').read()
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    ram = W + '/ram.bin'
    checker('ddr', ram, P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    rb = bytearray(open(ram, 'rb').read())
    starts, lens = layout(img)
    L = starts[1] + lens[1]
    inh, ow = entries(img, 0), entries(img, starts[1])
    units = sample_units()
    expect = {}   # (group index, id) -> new bytes
    for i, l, off, c, g in [struct.unpack_from('<HHIHH', units[1], 0x14 + 32 + 12 * k) for k in range(4)]:
        assert struct.unpack_from('<HH', rb, off - 4) == (i, l) and off == ddr_offset(img, fac, 1, ow[i][1]), 'sample item %d does not map' % i
        new = with_checksum(bytes(rb[off:off + l]), c)
        assert ck(new) == c
        rb[off:off + l] = new
        expect[(1, i)] = new
    iid = next(i for i, (l, o) in sorted(inh.items()) if l >= 4)
    il, io = inh[iid]
    ioff = ddr_offset(img, fac, 0, io)
    assert struct.unpack_from('<HH', rb, ioff - 4) == (iid, il), 'inherit mapping wrong'
    inew = struct.pack('<H', struct.unpack_from('<H', rb, ioff)[0] ^ 0x5a5a) + bytes(rb[ioff + 2:ioff + il])
    rb[ioff:ioff + il] = inew
    expect[(0, iid)] = inew
    open(ram, 'wb').write(rb)
    l2048, o2048 = ow[2048]
    d2048 = ddr_offset(img, fac, 1, o2048)
    c2048 = ck(expect[(1, 2048)])
    bad = (tok(0, 6, 2, 1) + tok(1, 7, 4, 1) + item(2048, l2048, d2048, c2048 ^ 1, 2) + item(2004, 8, 0x281c, 0x1126, 2)
           + item(0x7777, 4, 0x100, 0, 2) + item(2048, l2048, d2048, c2048, 1) + tok(2, 8, 2, 1))
    inherit = tok(0, 9, 1, 1) + tok(1, 10, 1, 1) + item(iid, il, ioff, ck(inew), 1) + tok(2, 11, 1, 1)
    factory = tok(0, 12, 0, 1) + tok(1, 13, 1, 1) + item(6100, 4, 0x300000, 0, 0) + tok(2, 14, 0, 1)
    units += [unit(3, 0, bad), unit(4, 0, inherit[:36]), unit(4, 1, inherit[36:]), unit(5, 0, factory)]
    log = W + '/open.log'
    env = dict(os.environ, LD_PRELOAD=BUILD + '/nvwb_openlog.so', NVWB_OPENLOG=log)
    r, lat, out, st, pts = run(BUILD + '/nvsyncd', dry_args(ram), units, env=env)
    print(out)
    assert r == b''.join(reply(g, s) for g, s in ((1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (5, 0))), r.hex()
    assert max(lat) < 1.0, lat
    assert st['txns'] == 5 and st['applied'] == 7 and st['skipped'] == 4 and st['wb_errors'] == 0, st
    res = [x['result'] for x in report(3)['items']]
    assert res == ['checksum mismatch', 'length differs from the image', 'id not in the image', 'group mismatch'], res
    assert report(5)['result'] == 'factory group ignored' and 'factory group ignored' in out
    assert report(4)['group'] == 1 and report(4)['items'][0]['result'] == 'applied'
    allowed = set()
    for (g, i), new in expect.items():
        l, o = (inh, ow)[g][i]
        allowed |= set(range(o, o + l))
    allowed |= set(range(4, 12)) | set(range(starts[1] + 4, starts[1] + 12))
    for k in (1, 2):
        sim = open('%s/out/runtimenv%d.img' % (W, k), 'rb').read()
        diff = {x for x in range(L) if sim[x] != img[x]}
        assert diff <= allowed, 'unexpected bytes changed: %s' % sorted(diff - allowed)[:20]
        for (g, i), new in expect.items():
            l, o = (inh, ow)[g][i]
            assert sim[o:o + l] == new
        print('runtimenv%d.img: %d bytes changed, all inside the applied items and the two header checksum fields' % (k, len(diff)))
    # the loader copies the overwrite header (with its new checksum) into the DDR header; everything else must be the CP's RAM
    rb[8:12] = sim[starts[1] + 8:starts[1] + 12]
    open(W + '/ram-expected.bin', 'wb').write(rb)
    print(checker('check', W + '/ram-expected.bin', W + '/out/runtimenv1.img', W + '/out/runtimenv2.img'))
    opens = [l.split(' ', 1) for l in open(log).read().splitlines()]
    assert {W + '/dev/mmcblk0p19', W + '/dev/mmcblk0p20'} <= {p for _, p in opens}, 'open() audit saw no partition open'
    for fl, path in opens:
        fl = int(fl)
        if path.startswith(W + '/dev/'):
            assert fl & 3 == os.O_RDONLY, 'partition opened for writing: %s' % path
        elif fl & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) and path not in (pts, W + '/run/status.json.tmp'):
            assert path.startswith(W + '/out/.') and fl & os.O_EXCL, 'unexpected write open: %s %x' % (path, fl)
    print('sample replay: replies ok (max %.0f ms), %d skipped items reported, open() audit ok (%d calls)' % (max(lat) * 1000, 4, len(opens)))


def test_damaged_runtimenv1():
    """the SPD backup of runtimenv1 has no VN header: it is reported invalid and gets the whole image, runtimenv2 only spans"""
    setup(BACKUP_R1, P15 + 'nr_runtimenv2.bin')
    ram = W + '/ram.bin'
    checker('ddr', ram, P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    r, _, out, st, _ = run(BUILD + '/nvsyncd', dry_args(ram), sample_units()[1:])
    assert 'runtimenv1 INVALID (differs), runtimenv2 valid' in out, out
    c = report(1)['copies']
    assert c[0]['whole'] and c[0]['valid'] and c[0]['equals_image'] and not c[1]['whole'] and c[1]['equals_image'], c
    assert open(W + '/out/runtimenv1.img', 'rb').read() == open(W + '/out/runtimenv2.img', 'rb').read()
    print('damaged runtimenv1: whole image planned, both copies end identical and valid')


def test_refusals():
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin', partname={19: 'nr_factorynv'})
    refused(dry_args(W + '/x'), 'PARTNAME is not nr_runtimenv1')
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin', start={20: 733185})
    refused(dry_args(W + '/x'), 'not at sector 733184')
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    os.rename(W + '/sys/class/block/mmcblk0p20', W + '/sys/class/block/other')
    refused(dry_args(W + '/x'), 'no ' + W + '/sys/class/block/mmcblk0p20/uevent')
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    refused(['--writeback=dry'], '--out-dir must name an existing directory')
    p = subprocess.run([BUILD + '/nvsyncd', '--writeback=sometimes'], capture_output=True, text=True, timeout=20)
    assert p.returncode == 2 and 'usage' in p.stderr
    print('refusals ok')


# ---- --writeback=on: the partition files are written; a fake /proc/cmdline holds (or lacks) the kernel write windows
WINDOWS = 'console=ttyS1 u30pro_rwx=1968128+131072,2304000+204800,2525184+16384,14862336+16777216,661504+71680,733184+71680 quiet\n'


def on_args(ram, cmdline=WINDOWS):
    open(W + '/cmdline', 'w').write(cmdline)
    return ['--writeback=on'] + dry_args(ram)[1:] + ['--cmdline', W + '/cmdline']


def reply0(grp, sub):
    return struct.pack('<5I', 0x524d4e56, 0, grp, sub, 1) + b'\x00' + bytes(15)


def golden():
    """p12 partitions, a fixnv RAM built from p15, and a transaction of the three ids the stock cp_diskserver changed"""
    a, b = open(P12 + 'nr_runtimenv2.bin', 'rb').read(), open(P15 + 'nr_runtimenv2.bin', 'rb').read()
    setup(P12 + 'nr_runtimenv1.bin', P12 + 'nr_runtimenv2.bin')
    ram = W + '/ram15.bin'
    checker('ddr', ram, P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    rb, fac = open(ram, 'rb').read(), open(FAC, 'rb').read()
    starts, lens = layout(b)
    ow = entries(b, starts[1])
    its = b''.join(item(i, ow[i][0], ddr_offset(b, fac, 1, ow[i][1]), ck(rb[ddr_offset(b, fac, 1, ow[i][1]):][:ow[i][0]]), 2)
                   for i in (2498, 2900, 2957))
    payload = tok(0, 0, 2, 1) + tok(1, 1, 3, 1) + its + tok(2, 2, 2, 1)
    return a, b, starts[1] + lens[1], ram, payload


def part(n):
    return open('%s/dev/mmcblk0p%d' % (W, n), 'rb').read()


def test_on_writes_like_stock():
    """the golden transaction written for real: both partition files end as p15 (what stock wrote), the rest untouched"""
    a, b, L, ram, payload = golden()
    before = part(19), part(20)
    r, lat, out, st, _ = run(BUILD + '/nvsyncd', on_args(ram), [unit(7, 0, payload)])
    print(out)
    assert r == reply(7, 0), r.hex()
    assert st['writeback'] == 'on' and st['writes_partitions'] and st['writes_ok'] == 1 and st['write_failures'] == 0, st
    rep = report(1)
    assert rep['mode'] == 'on' and rep['write_ok'] and all(c['written'] and c['valid'] and c['equals_image'] for c in rep['copies']), rep
    for n, old in zip((19, 20), before):
        now = part(n)
        assert len(now) == len(old) == 0x2300000
        assert now[:L] == b[:L], 'mmcblk0p%d differs from the stock result' % n
        assert now[L:] == old[L:], 'bytes after the image changed'
    assert not os.path.exists(W + '/out/runtimenv1.img'), 'on mode keeps no image copies'
    print(checker('check', ram, W + '/dev/mmcblk0p19', W + '/dev/mmcblk0p20'))
    print('on: both partitions written, identical to the stock result, reply in %.0f ms' % (lat[0] * 1000))


def test_on_without_window():
    """no write window on the command line: nothing is written, the CP is answered 1, the status says why"""
    a, b, L, ram, payload = golden()
    before = part(19), part(20)
    r, _, out, st, _ = run(BUILD + '/nvsyncd', on_args(ram, 'console=ttyS1 u30pro_rwx=1968128+131072,661504+71680\n'), [unit(7, 0, payload)])
    assert r == reply(7, 0) and 'WRITEBACK STOPPED before the first write' in out and 'nr_runtimenv2' in st['stop_reason'], (out, st)
    assert st['stopped'] and not st['writes_partitions'] and (part(19), part(20)) == before
    print('on without the runtimenv2 window: dry run only, partitions untouched')


def test_on_write_failure():
    """runtimenv1 cannot be opened for writing: result 0, runtimenv2 never touched; after three failures writing stops, result 1"""
    a, b, L, ram, payload = golden()
    os.chmod(W + '/dev/mmcblk0p19', 0o444)
    before = part(19), part(20)
    units = [unit(7, 0, payload), unit(8, 0, payload), unit(9, 0, payload), unit(10, 0, payload)]
    r, _, out, st, _ = run(BUILD + '/nvsyncd', on_args(ram), units)
    assert r == reply0(7, 0) + reply0(8, 0) + reply(9, 0) + reply(10, 0), r.hex()
    assert st['write_failures'] == 3 and st['stopped'] and st['writes_ok'] == 0 and 'WRITEBACK STOPPED' in out, st
    assert (part(19), part(20)) == before, 'a partition changed although the write failed'
    assert not report(1)['copies'][1]['written'] and report(4)['mode'] == 'dry'
    print('on write failure: replies 0, 0, then stopped (1, 1); runtimenv2 never written')


def test_on_damaged_runtimenv1():
    """runtimenv1 without its header: written whole, runtimenv2 only the spans; both end identical"""
    setup(BACKUP_R1, P15 + 'nr_runtimenv2.bin')
    ram = W + '/ram.bin'
    checker('ddr', ram, P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin')
    r, _, out, st, _ = run(BUILD + '/nvsyncd', on_args(ram), sample_units()[1:])
    assert r == reply(2, 0) and st['writes_ok'] == 1, (r.hex(), st)
    c = report(1)['copies']
    assert c[0]['whole'] and c[0]['written'] and not c[1]['whole'] and c[1]['written'] and c[0]['equals_image'] and c[1]['equals_image'], c
    L = layout(part(20))[0][1] + layout(part(20))[1][1]
    assert part(19)[:L] == part(20)[:L]
    print('on with damaged runtimenv1: whole image written, both copies identical and valid')


def test_on_bad_partition_keeps_answering():
    """on mode is started by the system: a wrong partition only switches the writing off, the CP is still answered"""
    setup(P15 + 'nr_runtimenv1.bin', P15 + 'nr_runtimenv2.bin', partname={19: 'nr_factorynv'})
    before = part(19), part(20)
    r, _, out, st, _ = run(BUILD + '/nvsyncd', on_args(W + '/x'), sample_units())
    assert r == reply(1, 0) + reply(2, 0) and 'WRITEBACK OFF: ' in out and 'PARTNAME is not nr_runtimenv1' in out, out
    assert not st['writes_partitions'] and (part(19), part(20)) == before
    print('on with a wrong partition: answering without writing')


if __name__ == '__main__':
    test_default_unchanged()
    test_refusals()
    test_golden_p12_to_p15()
    test_sample_replay()
    test_damaged_runtimenv1()
    test_on_writes_like_stock()
    test_on_without_window()
    test_on_write_failure()
    test_on_damaged_runtimenv1()
    test_on_bad_partition_keeps_answering()
    shutil.rmtree(W, ignore_errors=True)
    print('NVSYNCD WRITEBACK OFFLINE TEST OK')
