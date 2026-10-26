"""Not a test: an installer for the tests of the graphical installer (MU300_GUI_INSTALLER=<this file>). It plays the
part of install.sh / uninstall.sh from the moment the questions are answered - the stages, the downloads, the
summary and its question on standard input, the end - on the progress stream of docs/INSTALLER-AUTOMATION.md,
without a device, a download or a write. A check, a backup and --adb-path are handed to the real install.sh, so with
tests/fakeadb.py in front those are the real thing.

    python fakeinstaller.py install|uninstall [--check|--backup|--adb-path] [--answers FILE]

FAKEINSTALL          ok (default) | fail-download | fail-device | no-summary-answer
FAKEINSTALL_SPEED    seconds per tick (default 0.02; 0.25 looks like a real run)
FAKEINSTALL_RECORD   a file that gets what this run was started with: its arguments, the answers, the answers file's
                     permissions, and which answer variables were in the environment
The text it prints holds a serial number, an IMEI, an ICCID and the password it was given, on purpose: none of
them may reach a log the window keeps or saves.
"""
import json
import os
import stat
import sys
import time

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEED = float(os.environ.get('FAKEINSTALL_SPEED', '0.02'))
MODE = os.environ.get('FAKEINSTALL', 'ok')


def emit(event, **fields):
    obj = {'event': event}
    obj.update({k: str(v) for k, v in fields.items()})
    sys.stdout.write('@@mu300 ' + json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def say(text):
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def tick(n=1):
    time.sleep(SPEED * n)


def step(sid, pct, title):
    emit('step', id=sid, percent=pct, title=title)
    say('\n==> ' + title)
    tick()


def fail(message):
    emit('error', message=message)
    say('\nERROR: ' + message)
    sys.exit(1)


def read_answers(argv):
    path = None
    for i, a in enumerate(argv):
        if a == '--answers' and i + 1 < len(argv):
            path = argv[i + 1]
    answers, mode = {}, None
    if path:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n')
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    answers[k] = v
    rec = os.environ.get('FAKEINSTALL_RECORD')
    if rec:
        with open(rec, 'w', encoding='utf-8') as f:
            json.dump({'argv': argv, 'answers': answers, 'answers_file': path, 'answers_mode': mode,
                       'env_answers': sorted(k for k in os.environ if k.startswith('MU300_ANS_')),
                       'env': {k: os.environ.get(k, '') for k in ('MU300_LANG', 'ANDROID_SERIAL', 'MU300_PROGRESS', 'MU300_FROM_GUI',
                                                                    'MU300_NO_SELF_UPDATE', 'MU300_GUI', 'MU300_NONINTERACTIVE')}}, f)
    return answers


def ask(answers, var, text, default):
    """An answer as tools/answers.sh takes it: the variable, or a line of standard input for @stdin."""
    if var not in answers:
        fail('no answer to the question "%s": set %s (nothing is asked and nothing is assumed in answers mode)' % (text, var))
    value = answers[var]
    if value == '@stdin':
        emit('question', id=var, text=text, default=default, state='waiting')
        sys.stdout.write('%s [%s]: ' % (text, default))
        sys.stdout.flush()
        value = sys.stdin.readline()
        if not value:
            fail('no answer to the question "%s" on standard input (%s=@stdin)' % (text, var))
        value = value.rstrip('\r\n')
        say('')
    value = value or default
    emit('question', id=var, text=text, default=default, answer=value)
    return value


def install(answers):
    serial = os.environ.get('ANDROID_SERIAL', '324950664950')
    emit('hello', script='install.sh', version=1, mode='prebuilt')
    step('preflight', 2, 'Checking host tools and device')
    say('device: F50 / MU300')
    emit('info', key='device.kind', value='f50')
    say('adb -s %s shell getprop ro.serialno -> [ro.serialno]: [%s]' % (serial, serial))
    step('storage', 6, 'Locating free eMMC space after the last partition')
    release = answers.get('MU300_RELEASE', 'v2026.10.23')
    emit('info', key='release', value=release)
    step('choices', 10, 'What should be installed?')
    systems = {'1': ['ubuntu'], '2': ['openwrt'], '3': ['ubuntu', 'openwrt']}[ask(answers, 'MU300_ANS_SYSTEMS', 'Choice', '3')]
    if 'openwrt' in systems and answers.get('MU300_OPENWRT') == 'luci':
        systems = [s.replace('openwrt', 'openwrt-luci') for s in systems]
    boot = systems[0]
    if len(systems) == 2:
        boot = ask(answers, 'MU300_ANS_BOOT_OS', 'Which one should boot', systems[0])
    kernel = ask(answers, 'MU300_ANS_KERNEL', 'Kernel', '1')
    if 'MU300_ANS_PASSWORD' not in answers:
        fail('no answer to the question "Password": set MU300_ANS_PASSWORD')
    emit('question', id='MU300_ANS_PASSWORD', text='Password', default='', secret=1)
    say('Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt): ******')
    # what a careless installer might print: the window's log must not keep any of it
    say('debug: MU300_ANS_PASSWORD=%s IMEI: 867530912345678 ICCID 8990011234567890123 imsi=286011234567890' % answers['MU300_ANS_PASSWORD'])
    step('pull', 15, 'Pulling device data into work (stays on this computer)')
    tick(3)
    step('download', 20, 'Downloading release ' + release)
    files = [('mu300-kernel.tar.gz', 26971198)]
    if kernel not in ('1', '5.4'):
        files.append(('mu300-kernel-%s.tar.gz' % kernel, 18529345))
    files += [('mu300-%s-rootfs.tar.gz' % s, 97303297 if s == 'ubuntu' else 21658084) for s in systems]
    for i, (name, total) in enumerate(files):
        say('  ' + name)
        emit('download', file=name, bytes=0, total=total, state='start')
        for part in range(1, 9):
            tick()
            emit('download', file=name, bytes=total * part // 8, total=total, state='running')
            if MODE == 'fail-download' and i == 1 and part == 4:
                emit('download', file=name, bytes=total * part // 8, total=total, state='failed')
                fail('download of %s failed' % name)
        emit('download', file=name, bytes=total, total=total, state='done')
        emit('verify', file=name, result='ok')
    step('vendor', 60, 'Adding the vendor files from your device to the images')
    tick(2)
    step('bootimage', 68, 'Building the boot image')
    tick(2)
    step('confirm', 72, 'Ready to install')
    say('  source:         prebuilt release %s + vendor files from this device' % release)
    say('  systems:        %s (boots: %s)' % (' '.join(systems), boot))
    say('  kernel:         %s' % kernel)
    say('  default boot:   Linux (Android after %s failed boots in a row)' % answers.get('MU300_ANS_BOOT_ATTEMPTS', '5'))
    say('  filesystem:     CREATE new ext4 (erases the Linux region)')
    say('  writes:         Linux region at offset 27762098176, boot_b, 32 bytes of misc (boot_a, GPT and userdata are not touched)')
    say('  boot chain:     identical on both slots')
    if ask(answers, 'MU300_ANS_CONFIRM', 'Type INSTALL to continue', 'no') != 'INSTALL':
        fail('cancelled')
    step('copy', 75, 'Copying to the device')
    tick(4)
    step('device', 80, 'Installing on the device')
    for line in ('formatting the Linux region', 'unpacking ubuntu', 'unpacking openwrt', 'MU300-INSTALL-OK'):
        say(line)
        tick(2)
        if MODE == 'fail-device' and line.startswith('unpacking ubuntu'):
            fail('installation on the device failed; boot_b and misc were not changed')
    step('boot', 92, 'Writing boot_b and arming slot b')
    tick(3)
    step('switch', 96, 'Installing the on-device switch (Magisk module)')
    tick(2)
    step('done', 100, 'Done. Rebooting into ' + boot)
    emit('done', result='ok', boot_os=boot, systems=' '.join(systems), ip='192.168.77.1', release=release, kernel=kernel)
    return 0


def uninstall(answers):
    emit('hello', script='uninstall.sh', version=1, mode='uninstall')
    step('preflight', 5, 'Checking host tools and device')
    step('find', 15, 'Looking for the Linux installation')
    emit('info', key='region.existing', value='yes')
    step('choices', 25, 'What should be removed?')
    wipe = ask(answers, 'MU300_ANS_WIPE', 'Erase the Linux filesystem: secure / quick / keep', 'secure')
    ask(answers, 'MU300_ANS_SD_WIPE', 'Linux filesystem on the SD card: erase / keep', 'erase') if 'MU300_ANS_SD_WIPE' in answers else None
    emit('step', id='confirm', percent=30, title='What will be done')
    say('  misc:     boot slot a (Android), Linux boot disabled')
    say('  boot_b:   replaced with a copy of boot_a (stock Android boot image)')
    say('  Linux:    %s' % ('kept on the eMMC (not bootable)' if wipe == 'keep' else wipe + ' erase of 33168 MiB at offset 27762098176'))
    say('  untouched: boot_a, GPT, userdata and all other partitions')
    if ask(answers, 'MU300_ANS_CONFIRM', 'Type UNINSTALL to continue', 'no') != 'UNINSTALL':
        fail('cancelled')
    for sid, pct, title in (('misc', 40, 'Making slot a the boot slot'), ('boot', 55, 'Restoring boot_b from boot_a'),
                            ('erase', 65, 'Erasing the Linux filesystem (%s)' % wipe), ('userdata', 88, 'Checking whether userdata was shrunk to make room'),
                            ('switch', 95, 'Removing the on-device switch (Magisk module)')):
        if sid == 'erase' and wipe == 'keep':
            continue
        step(sid, pct, title)
        tick(2)
    step('done', 100, 'Done. The device boots stock Android; reboot it once to check.')
    emit('done', result='ok')
    return 0


def main(argv):
    name, args = argv[0], argv[1:]
    if any(a in ('--check', '--backup', '--adb-path') for a in args):
        os.execvp('sh', ['sh', os.path.join(TOP, name + '.sh')] + args)
    answers = read_answers(args)
    return install(answers) if name == 'install' else uninstall(answers)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
