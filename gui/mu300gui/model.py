"""What the window knows and decides, without a window: the facts an installer's check reports about a device, the
choices of an installation and the answers they become, what a run's events mean for the progress display. No
install logic lives here - the choices are the installers' questions, the answers are their variables
(docs/INSTALLER-AUTOMATION.md), and whatever an answer is not fit for, the installer itself refuses.
"""
import time
from collections import OrderedDict

from .i18n import N_, _

MIB = 1 << 20
GIB = 1 << 30
SECTOR = 512
# what the installers say each choice needs (their `need.*` facts say it for a device; these are for before a check)
NEED = {'openwrt': 800 * MIB, 'ubuntu': 1600 * MIB, 'both': 2400 * MIB}
KERNELS = ('5.4', '6.18', '7.2', '7.3')
UBUNTUS = ('24.04', '26.04')
MIN_PASSWORD = 6


def size(n):
    """A size for people: 32.4 GiB, 800 MiB, 12 KiB."""
    n = int(n or 0)
    if n >= GIB:
        return '%.1f GiB' % (n / GIB)
    if n >= MIB:
        return '%d MiB' % round(n / MIB)
    if n >= 1024:
        return '%d KiB' % round(n / 1024)
    return '%d B' % n


def duration(seconds):
    """A time left for people: 45 s, 3 min, 1 h 20 min."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return '%d s' % seconds
    if seconds < 3600:
        return '%d min' % round(seconds / 60)
    return '%d h %02d min' % (seconds // 3600, (seconds % 3600) // 60)


class Facts(dict):
    """The `info` events of a check: {key: value}, all text, with the readings the window needs."""

    def num(self, key, default=0):
        try:
            return int(self.get(key, ''))
        except (TypeError, ValueError):
            return default

    @property
    def kind(self):
        return self.get('device.kind', '')

    @property
    def root(self):
        """True, False, or None while nothing has said."""
        return {'yes': True, 'no': False}.get(self.get('root'))

    @property
    def bootloader(self):
        """locked, unlocked or unknown, as the installer's check says it (bootloader.state), or from the two
        properties themselves before a check: ro.boot.flash.locked and ro.boot.vbmeta.device_state. Locked is a
        warning, never a refusal: a locked bootloader with a patched TrustOS boots Linux, and no property tells
        whether TrustOS is patched."""
        state = self.get('bootloader.state', '')
        if state in ('locked', 'unlocked', 'unknown'):
            return state
        flash, device = self.get('bootloader.locked', ''), self.get('bootloader.device_state', '').lower()
        if flash == '1' or device == 'locked':
            return 'locked'
        if flash == '0' or device == 'unlocked':
            return 'unlocked'
        return 'unknown'

    @property
    def existing(self):
        return self.get('region.existing') == 'yes'

    @property
    def sd(self):
        """The usable SD card: (device, bytes, state) or None."""
        if not self.get('sd.device'):
            return None
        return self['sd.device'], self.num('sd.bytes'), self.get('sd.state', 'no')

    @property
    def checked(self):
        """The check got as far as the storage."""
        return 'region.size' in self

    def need(self, what):
        return self.num('need.' + what, NEED[what])

    def partitions(self):
        """[(name, first sector, sectors)] in the order of the disk."""
        out = []
        for part in self.get('partitions', '').split():
            bits = part.rsplit(':', 2)
            if len(bits) == 3 and bits[1].isdigit() and bits[2].isdigit():
                out.append((bits[0], int(bits[1]), int(bits[2])))
        return sorted(out, key=lambda p: p[1])


def layout(facts):
    """The eMMC as a row of segments for a picture: [{'kind', 'label', 'bytes'}] that add up to the whole disk.
    Kinds: android (everything in front of userdata, as one), userdata, linux (an installation in the free region),
    free (the region an installation would use), other (alignment, the backup partition table, what follows)."""
    disk = facts.num('emmc.sectors') * SECTOR
    if disk <= 0:
        return []
    parts = facts.partitions()
    segs = []
    last_end = facts.num('emmc.last_end') * SECTOR
    user = [p for p in parts if p[0] == 'userdata']
    if user:
        u_start, u_size = user[0][1] * SECTOR, user[0][2] * SECTOR
        if u_start > 0:
            segs.append({'kind': 'android', 'label': N_('Android (boot, system, vendor, modem)'), 'bytes': u_start})
        segs.append({'kind': 'userdata', 'label': N_('userdata (Android apps and data)'), 'bytes': u_size})
        behind = last_end - (u_start + u_size)
        if behind > 0:
            segs.append({'kind': 'android', 'label': N_('partitions behind userdata'), 'bytes': behind})
    elif last_end > 0:
        segs.append({'kind': 'android', 'label': N_('Android partitions'), 'bytes': last_end})
    off, region = facts.num('region.offset'), facts.num('region.size')
    used = last_end
    if region > 0 and off >= last_end:
        if off > used:
            segs.append({'kind': 'other', 'label': '', 'bytes': off - used})
        segs.append({'kind': 'linux' if facts.existing else 'free',
                     'label': N_('MU300 Linux') if facts.existing else N_('free: Linux goes here'), 'bytes': region})
        used = off + region
    if disk > used:
        # behind an installation smaller than the gap there is more free space; otherwise this is alignment and the
        # backup partition table
        rest = disk - used
        segs.append({'kind': 'free' if rest > 64 * MIB else 'other', 'label': N_('unused') if rest > 64 * MIB else '', 'bytes': rest})
    return segs


def make_room(facts):
    """The figures of the make-room path (userdata fills the disk), or None when the check did not report them:
    what userdata and Linux share, the least and the most Linux can get, in bytes, and the size offered first."""
    if 'makeroom.first' not in facts:
        return None
    first, end, usable = facts.num('makeroom.first'), facts.num('makeroom.end'), facts.num('makeroom.usable_end')
    lo, hi = facts.num('makeroom.min_linux') * SECTOR, facts.num('makeroom.max_linux') * SECTOR
    return {'userdata': (end - first + 1) * SECTOR, 'total': (usable - first + 1) * SECTOR, 'min': lo, 'max': hi,
            'min_android': facts.num('makeroom.min_android') * SECTOR, 'possible': hi >= lo > 0,
            'suggest': min(max(4 * GIB, lo), hi) if hi >= lo else 0}


class Choices:
    """One installation's answers to the installers' questions, with the installers' own defaults. Nothing here is
    a consent: the words that allow something destructive (overwrite, ERASE, internal) are fields that stay empty
    until the user types them, and INSTALL is sent only when the installer shows its summary."""

    def __init__(self):
        self.ubuntu = True
        self.openwrt = True
        self.ubuntu_version = '24.04'
        self.openwrt_flavor = 'plain'       # plain | luci (with the MU300 control panel)
        self.boot_os = 'ubuntu'             # with two systems: ubuntu | openwrt
        self.kernel = '5.4'
        self.release = ''                   # '' : the newest full release, as the installer finds it; else a tag
        self.release_url = ''               # advanced: a mirror with the same files
        self.default_linux = True
        self.boot_attempts = 5
        self.hotspot = True
        self.gpu = True
        self.vpn = False
        self.adb = False                    # adb to Linux is on for every computer; True: for this computer's key only
        self.panel = False                  # the web panel for Ubuntu
        self.storage = 'internal'           # internal | sd
        self.existing = 'update'            # with an installation there: update | wipe
        self.password = ''
        self.password2 = ''
        # an update keeps the login password of the systems it replaces, once the installer has checked the current
        # one on the device (MU300_ANS_KEEP_PASSWORD, MU300_ANS_CURRENT_PASSWORD); the new one above is then only for a
        # system the update installs new
        self.keep_password = True
        self.current_password = ''          # empty: none (OpenWrt's root as it ships)
        self.overwrite_word = ''            # "overwrite": the free space holds data
        self.erase_sd_word = ''             # "ERASE": a new filesystem on the card
        self.internal_word = ''             # "internal": internal storage although the card holds an installation
        self.unknown_model = False          # advanced: go on with a device that is no F50/U30 Air
        self.device = ''                    # the kind the last check found (f50, u30air, v50): not a choice
        self.sync_bootchain = True          # advanced: MU300_SYNC_BOOTCHAIN
        self.fetch_jobs = 8                 # advanced: MU300_FETCH_JOBS
        self.vpn_url = ''                   # advanced: MU300_VPN_URL
        self.vpn_module = ''                # advanced: MU300_VPN_MODULE
        self.package = None                 # an unpacked Magisk zip (package.load): its system and kernel only

    def apply_package(self, pkg):
        """The choices a package makes itself: its system (one), its Ubuntu, its kernel and its release."""
        self.package = pkg
        self.ubuntu = pkg['os'] == 'ubuntu'
        self.openwrt = not self.ubuntu
        self.openwrt_flavor = 'luci' if pkg['os'] == 'openwrt-luci' else 'plain'
        self.ubuntu_version = pkg['ubuntu'] or '24.04'
        self.boot_os = 'ubuntu' if self.ubuntu else 'openwrt'
        self.kernel = pkg['kernel']
        self.release = pkg['tag']
        self.release_url = ''
        self.panel = False                  # a file of the release, not of the zip

    def systems(self):
        """The systems as the installers name them: ubuntu, openwrt or openwrt-luci."""
        out = []
        if self.ubuntu:
            out.append('ubuntu')
        if self.openwrt:
            out.append('openwrt-luci' if self.openwrt_flavor == 'luci' else 'openwrt')
        return out

    def boot_system(self):
        names = self.systems()
        if len(names) < 2:
            return names[0] if names else ''
        return names[0] if self.boot_os == 'ubuntu' else names[1]

    def needs(self, facts):
        if self.ubuntu and self.openwrt:
            return facts.need('both')
        return facts.need('ubuntu') if self.ubuntu else facts.need('openwrt')

    def kernel_ok(self, kernel=None):
        """Ubuntu 26.04 needs a mainline kernel: its programs use system calls 5.4 does not have. The ZTE V50 runs a
        mainline kernel only (device: the kind the check found)."""
        kernel = self.kernel if kernel is None else kernel
        if kernel == '5.4' and self.device == 'v50':
            return False
        return not (self.ubuntu and self.ubuntu_version == '26.04' and kernel == '5.4')

    def formats(self, facts):
        """Whether this installation makes a new filesystem (and so erases what the place holds)."""
        if self.storage == 'sd':
            sd = facts.sd
            return not (sd and sd[2] == 'yes') or self.existing == 'wipe'
        return not facts.existing or self.existing == 'wipe'

    def place_existing(self, facts):
        """Whether the place this installation goes to holds one already (then: update or wipe)."""
        if self.storage == 'sd':
            sd = facts.sd
            return bool(sd and sd[2] == 'yes')
        return facts.existing

    def keeps_password(self, facts):
        """Whether this installation is an update that keeps the current password (checked by the installer)."""
        return bool(self.place_existing(facts)) and self.existing == 'update' and self.keep_password

    def place_bytes(self, facts):
        if self.storage == 'sd':
            sd = facts.sd
            return sd[1] if sd else 0
        return facts.num('region.size')


def problems(c, facts):
    """Why this installation cannot start yet: [(field, message)], empty when it can. The installer checks all of
    it again; this is so that the window says it before the download, in the place where it is chosen."""
    out = []
    if facts.root is False:
        out.append(('device', _('The device has no working root (su).')))
    if facts.kind == 'unknown' and not c.unknown_model:
        out.append(('device', _('This is not a ZTE F50/MU300 or U30 Air.')))
    if not facts.checked:
        out.append(('device', _('The device has not been checked yet.')))
        return out
    if not c.ubuntu and not c.openwrt:
        out.append(('systems', _('Choose at least one system.')))
    sd = facts.sd
    if c.storage == 'sd':
        if not sd:
            out.append(('storage', _('There is no usable SD card in the device.')))
        elif sd[2] == 'foreign':
            out.append(('storage', _('The SD card holds another Linux (ext4) filesystem, and the installer never formats one that is not its own.')))
        elif c.formats(facts) and c.erase_sd_word != 'ERASE':
            out.append(('storage', _('Type ERASE to allow a new filesystem on the SD card: everything on it is erased.')))
    else:
        if facts.num('region.size') < 700 * MIB and not facts.existing:
            out.append(('storage', _('There is no free space for Linux on the internal storage.')))
        if sd and sd[2] == 'yes' and c.internal_word != 'internal':
            out.append(('storage', _('The SD card holds an installation, and the device always starts that one first. Type internal to install to internal storage anyway.')))
        if facts.num('region.dirty') > 0 and not facts.existing and c.overwrite_word != 'overwrite':
            out.append(('storage', _('The free space is not empty. Type overwrite to use it anyway.')))
    if (c.ubuntu or c.openwrt) and c.place_bytes(facts) and c.place_bytes(facts) < c.needs(facts):
        out.append(('systems', _('That choice needs about {1} and the place has {2}.', size(c.needs(facts)), size(c.place_bytes(facts)))))
    if c.kernel == '5.4' and c.device == 'v50' and c.package:
        out.append(('kernel', _('The ZTE V50 runs a mainline kernel only: take the zip for kernel 6.18, 7.2 or 7.3 of this release')))
    elif c.kernel == '5.4' and c.device == 'v50':
        out.append(('kernel', _('The ZTE V50 runs a mainline kernel only (6.18, 7.2 or 7.3).')))
    elif not c.kernel_ok():
        out.append(('kernel', _('Ubuntu 26.04 needs a mainline kernel: choose 6.18, 7.2 or 7.3.')))
    if c.kernel not in KERNELS:
        out.append(('kernel', _('Choose a kernel.')))
    if c.default_linux and not 1 <= int(c.boot_attempts or 0) <= 6:
        out.append(('boot', _('Failed boots before Android: a number from 1 to 6.')))
    # an update that keeps the current password needs a new one only for a system it installs new: none typed is fine
    if c.keeps_password(facts) and not c.password and not c.password2:
        pass
    elif len(c.password) < MIN_PASSWORD:
        out.append(('password', _('The password needs {1} characters or more.', MIN_PASSWORD)))
    elif c.password != c.password2:
        out.append(('password', _('The two passwords differ.')))
    if '\n' in c.password or '\r' in c.password or '\n' in c.current_password or '\r' in c.current_password:
        out.append(('password', _('The password cannot hold a line end.')))
    return out


def yn(flag):
    return 'yes' if flag else 'no'


def answers(c, facts, lang, serial):
    """The variables of an installation, in the order the installer asks. A question that will not be asked gets no
    variable - and one that needs a typed word gets it only as the user typed it, so the installer's own refusal
    stays the last word. MU300_ANS_CONFIRM is @stdin: the window sends INSTALL when the installer shows its
    summary and the user says yes to that."""
    a = OrderedDict()
    a['MU300_LANG'] = lang
    if serial:
        a['ANDROID_SERIAL'] = serial
    if facts.kind == 'unknown':
        a['MU300_ANS_UNKNOWN_MODEL'] = yn(c.unknown_model)
    if not c.sync_bootchain:
        a['MU300_SYNC_BOOTCHAIN'] = '0'
    if facts.sd:
        a['MU300_STORAGE'] = c.storage
        if c.storage == 'internal' and facts.sd[2] == 'yes' and c.internal_word:
            a['MU300_ANS_INTERNAL_OVER_CARD'] = c.internal_word
    if c.storage != 'sd' and facts.num('region.dirty') > 0 and not facts.existing and c.overwrite_word:
        a['MU300_ANS_OVERWRITE'] = c.overwrite_word
    # a package (an unpacked Magisk zip) is its own release, system, Ubuntu and kernel: the installer asks none of these
    if not c.package:
        if c.release_url:
            a['MU300_RELEASE_URL'] = c.release_url
        if c.release:
            a['MU300_RELEASE'] = c.release
        else:
            a['MU300_ANS_BETA'] = 'no'     # "the newest full release" was chosen here, with the beta on offer beside it
        a['MU300_ANS_SYSTEMS'] = '3' if (c.ubuntu and c.openwrt) else ('1' if c.ubuntu else '2')
        if c.openwrt:
            a['MU300_OPENWRT'] = c.openwrt_flavor
        if c.ubuntu and c.openwrt:
            a['MU300_ANS_BOOT_OS'] = c.boot_system()
        if c.ubuntu:
            a['MU300_ANS_UBUNTU'] = c.ubuntu_version
    a['MU300_ANS_DEFAULT_LINUX'] = yn(c.default_linux)
    if c.default_linux:
        a['MU300_ANS_BOOT_ATTEMPTS'] = str(c.boot_attempts)
    a['MU300_ANS_HOTSPOT'] = yn(c.hotspot)
    a['MU300_ANS_GPU'] = yn(c.gpu)
    a['MU300_ANS_VPN'] = yn(c.vpn)
    if c.vpn and c.vpn_url:
        a['MU300_VPN_URL'] = c.vpn_url
    if c.vpn and c.vpn_module:
        a['MU300_VPN_MODULE'] = c.vpn_module
    a['MU300_ANS_ADB'] = yn(c.adb)
    if c.ubuntu and not c.package:
        a['MU300_ANS_PANEL'] = yn(c.panel)
    if not c.package:
        a['MU300_ANS_KERNEL'] = c.kernel
    if c.place_existing(facts):
        a['MU300_ANS_EXISTING'] = c.existing
        if c.existing == 'update':
            a['MU300_ANS_KEEP_PASSWORD'] = yn(c.keep_password)
            if c.keep_password:
                a['MU300_ANS_CURRENT_PASSWORD'] = c.current_password
    if c.storage == 'sd' and c.formats(facts) and c.erase_sd_word:
        a['MU300_ANS_ERASE_SD'] = c.erase_sd_word
    if int(c.fetch_jobs or 8) != 8:
        a['MU300_FETCH_JOBS'] = str(int(c.fetch_jobs))
    # (none typed with an update that keeps the current one: the installer then asks for it only for a system the
    # update installs new, and stops naming the variable)
    if c.password or not c.keeps_password(facts):
        a['MU300_ANS_PASSWORD'] = c.password
    a['MU300_ANS_CONFIRM'] = '@stdin'
    return a


def make_room_answers(lang, serial, gib, word, reboot=True):
    """The variables of a make-room run: the size, and ERASE only as it was typed."""
    a = OrderedDict()
    a['MU300_LANG'] = lang
    if serial:
        a['ANDROID_SERIAL'] = serial
    a['MU300_ANS_MAKE_ROOM'] = 'yes'
    a['MU300_ANS_LINUX_GIB'] = ('%.2f' % gib).rstrip('0').rstrip('.')
    if word:
        a['MU300_ANS_ERASE_ANDROID'] = word
    a['MU300_ANS_REBOOT'] = yn(reboot)
    return a


def uninstall_answers(lang, serial, wipe, sd_wipe, grow=False, erase_word=''):
    """The variables of an uninstallation. MU300_ANS_CONFIRM is @stdin: UNINSTALL is sent when the uninstaller shows
    what it will do. Growing userdata back (uninstall.sh only) erases Android's data: ERASE as it was typed."""
    a = OrderedDict()
    a['MU300_LANG'] = lang
    if serial:
        a['ANDROID_SERIAL'] = serial
    a['MU300_ANS_WIPE'] = wipe
    a['MU300_ANS_SD_WIPE'] = sd_wipe
    a['MU300_ANS_CONFIRM'] = '@stdin'
    a['MU300_ANS_GROW'] = yn(grow)
    if grow and erase_word:
        a['MU300_ANS_ERASE_ANDROID'] = erase_word
    return a


def download_plan(c, files):
    """The files of the release this installation downloads, as the installers choose them: [(name, bytes)] and
    their sum. FILES is {name: bytes} of the release (releases.py); a size the list does not have counts as 0. A
    package downloads nothing."""
    if c.package:
        return [], 0
    names = ['mu300-kernel.tar.gz']
    if c.kernel != '5.4':
        names.append('mu300-kernel-%s.tar.gz' % c.kernel)
    for s in c.systems():
        names.append('mu300-ubuntu-26.04-rootfs.tar.gz' if (s == 'ubuntu' and c.ubuntu_version == '26.04') else 'mu300-%s-rootfs.tar.gz' % s)
    if c.vpn and 'mu300-extra-vpn.tar.gz' in files:
        names.append('mu300-extra-vpn.tar.gz')
    if c.panel and c.ubuntu and 'mu300-extra-panel-offline.tar.gz' in files:
        names.append('mu300-extra-panel-offline.tar.gz')
    plan = [(n, int(files.get(n, 0))) for n in names]
    return plan, sum(b for _n, b in plan)


def work_need(c, files):
    """About how much room the work directory on this computer needs: the downloads, the kernel bundles unpacked,
    the device's own files and the images that are built from them."""
    _plan, total = download_plan(c, files)
    if c.package:
        total = c.package.get('bytes', 0)       # its files are there already; what is built from them is not
    return int(total * 1.6) + 400 * MIB


def summary(c, facts, release):
    """The choices as lines for a last look before the installer is started: [(label, value)]."""
    names = {'ubuntu': 'Ubuntu ' + c.ubuntu_version, 'openwrt': 'OpenWrt', 'openwrt-luci': _('OpenWrt with the MU300 control panel')}
    systems = [names[s] for s in c.systems()]
    rows = [(_('Systems'), ', '.join(systems) + ('  (' + _('boots: {1}', names[c.boot_system()]) + ')' if len(systems) > 1 else ''))]
    rows.append((_('Kernel'), c.kernel))
    rows.append((_('Release'), c.release or release or _('the newest release')))
    if c.package:
        rows.append((_('From'), _('this unpacked Magisk zip, nothing downloaded: {1}', c.package['dir'])))
    rows.append((_('Where'), _('SD card') if c.storage == 'sd' else _('internal storage')))
    if c.place_existing(facts):
        rows.append((_('Existing installation'), _('update: settings and data are kept') if c.existing == 'update' else _('wipe: start from scratch')))
        if c.existing == 'update':
            rows.append((_('Password'), _('kept: the current one is checked on the device') if c.keep_password else _('the new one')))
    rows.append((_('Default boot'), _('Linux (Android after {1} failed boots in a row)', c.boot_attempts) if c.default_linux else _('Android, Linux on demand')))
    extras = [n for flag, n in ((c.vpn, _('VPN module')), (c.panel and c.ubuntu, _('web panel for Ubuntu')), (c.gpu, _('GPU userspace')),
                                (c.hotspot, _('hotspot name and password from Android')), (c.adb, _('adb to Linux for this computer only'))) if flag]
    rows.append((_('Also'), ', '.join(extras) if extras else _('nothing else')))
    return rows


def connect(kind, boot_system, systems=(), panel=False):
    """How to reach the device once Linux runs: its address on the USB network, the SSH login, the web pages."""
    ip = {'u30air': '192.168.78.1', 'v50': '192.168.79.1'}.get(kind, '192.168.77.1')
    ubuntu = boot_system == 'ubuntu'
    info = {'ip': ip, 'user': 'ubuntu' if ubuntu else 'root', 'ssh': '%s@%s' % ('ubuntu' if ubuntu else 'root', ip), 'web': []}
    if not ubuntu:
        info['web'].append(('LuCI', 'http://%s' % ip))
    elif panel:
        info['web'].append(('Cockpit', 'https://%s:9090' % ip))
    return info


# the stages of a run, as the installers name them on the progress stream, with what the window calls them
INSTALL_STEPS = (('preflight', N_('Check the computer and the device')), ('storage', N_('Find the place for Linux')),
                 ('choices', N_('Take the choices')), ('pull', N_('Read the device\'s own files')),
                 ('download', N_('Download and verify the release')), ('vendor', N_('Add the device\'s files to the images')),
                 ('bootimage', N_('Build the boot image')), ('confirm', N_('Show the summary and wait for your yes')),
                 ('copy', N_('Copy to the device')), ('device', N_('Install on the device')),
                 ('boot', N_('Write the boot image and arm Linux')), ('switch', N_('Install the on-device switch')),
                 ('done', N_('Reboot into Linux')))
UNINSTALL_STEPS = (('preflight', N_('Check the computer and the device')), ('find', N_('Look for the Linux installation')),
                   ('choices', N_('Take the choices')), ('confirm', N_('Show the summary and wait for your yes')),
                   ('misc', N_('Make Android the boot slot')), ('boot', N_('Restore the boot image')),
                   ('erase', N_('Erase the Linux filesystem')), ('sd', N_('Erase the SD card\'s installation')),
                   ('userdata', N_('Look at userdata')), ('switch', N_('Remove the on-device switch')),
                   ('done', N_('Done')))
CHECK_STEPS = (('preflight', N_('Check the computer and the device')), ('storage', N_('Find the place for Linux')))
BACKUP_STEPS = (('preflight', N_('Check the computer and the device')), ('backup', N_('Copy the boot image and the start of misc')))
MAKEROOM_STEPS = (('preflight', N_('Check the computer and the device')), ('storage', N_('Read the partition table')),
                  ('backup', N_('Back up the partition table, misc, boot and the boot chain')),
                  ('repartition', N_('Rewrite the partition table and ask Android\'s recovery for the reset')))
# From these stages on a run writes to the device: stopping it there can leave a write half done. Before them
# nothing on the device has changed and a run can be stopped at any moment.
NO_CANCEL = {'install': ('device', 'boot', 'switch', 'done'), 'uninstall': ('misc', 'boot', 'erase', 'sd', 'userdata', 'switch', 'done'),
             'makeroom': ('repartition',)}


class Progress:
    """A run as its events tell it: the stages and their states, how far it is, the file that is coming in with its
    speed and time left, the files that were verified, the question it waits on, how it ended."""

    def __init__(self, steps=INSTALL_STEPS, kind='install', clock=time.monotonic):
        self.kind = kind
        self.steps = [[sid, label, 'pending'] for sid, label in steps]      # pending | running | done | failed | skipped
        self.current = ''
        self.percent = 0
        self.title = ''
        self.files = OrderedDict()      # name -> {'bytes', 'total', 'state', 'speed', 'eta', 'verify'}
        self.file = ''                  # the file that is coming in
        self.waiting = None             # the question event a run waits on (standard input)
        self.error = ''
        self.result = None              # the `done` event
        self.finished = False
        self.exit = None
        self.facts = Facts()
        self.questions = []
        self.heading_lines = []         # the text since the current stage began (the installer's summary, for one)
        self.plan_total = 0             # the bytes of all the files this run downloads, when the window knows
        self._clock = clock
        self._samples = {}

    def state(self, sid):
        for s in self.steps:
            if s[0] == sid:
                return s[2]
        return ''

    def can_cancel(self):
        """Whether stopping the run now is safe: nothing has been written to the device yet."""
        return not self.finished and self.current not in NO_CANCEL.get(self.kind, ())

    def event(self, ev):
        kind = ev.get('event')
        if kind == 'step':
            self.title = ev.get('title', '')
            sid = ev.get('id', '')
            if not sid:
                return
            self.heading_lines = []
            try:
                self.percent = max(self.percent, int(ev.get('percent') or 0))
            except ValueError:
                pass
            ids = [s[0] for s in self.steps]
            for i, s in enumerate(self.steps):
                if s[2] == 'running':
                    s[2] = 'done'
                elif sid in ids and i < ids.index(sid) and s[2] == 'pending':
                    s[2] = 'skipped'            # a stage this run had no use for (no card to erase, say)
            if sid in ids:
                self.steps[ids.index(sid)][2] = 'done' if sid == 'done' else 'running'
            self.current = sid
        elif kind == 'info':
            self.facts[ev.get('key', '')] = ev.get('value', '')
        elif kind == 'question':
            if ev.get('state') == 'waiting':
                self.waiting = ev
            else:
                self.waiting = None
                self.questions.append(ev)
        elif kind == 'download':
            self._download(ev)
        elif kind == 'verify':
            f = self.files.setdefault(ev.get('file', ''), {'bytes': 0, 'total': 0, 'state': '', 'speed': 0.0, 'eta': None, 'verify': ''})
            f['verify'] = ev.get('result', '')
        elif kind == 'error':
            self.error = ev.get('message', '')
            for s in self.steps:
                if s[2] == 'running':
                    s[2] = 'failed'
        elif kind == 'done':
            self.result = ev
            self.percent = 100 if self.kind in ('install', 'uninstall') else self.percent
            for s in self.steps:
                if s[2] == 'running':
                    s[2] = 'done'

    def _download(self, ev):
        name = ev.get('file', '')
        try:
            got, total = int(ev.get('bytes') or 0), int(ev.get('total') or 0)
        except ValueError:
            return
        f = self.files.setdefault(name, {'bytes': 0, 'total': 0, 'state': '', 'speed': 0.0, 'eta': None, 'verify': ''})
        now = self._clock()
        state = ev.get('state', '')
        if state == 'start' or name not in self._samples:
            self._samples[name] = [(now, got)]
        samples = self._samples[name]
        samples.append((now, got))
        # the speed over the last ten seconds: steady enough to read, quick enough to follow
        while len(samples) > 2 and now - samples[0][0] > 10:
            samples.pop(0)
        dt, db = samples[-1][0] - samples[0][0], samples[-1][1] - samples[0][1]
        speed = db / dt if dt > 0 and db >= 0 else 0.0
        f.update(bytes=got, total=total, state=state, speed=speed,
                 eta=((total - got) / speed) if speed > 0 and total > got else None)
        if state in ('done', 'failed'):
            f.update(speed=0.0, eta=None)
            self.file = '' if self.file == name else self.file
        else:
            self.file = name

    def line(self, text):
        self.heading_lines.append(text)
        if len(self.heading_lines) > 200:
            del self.heading_lines[0]

    def end(self, status):
        """The process is gone. A run that ended without saying how failed."""
        self.exit = status
        self.finished = True
        self.waiting = None
        if status != 0 and not self.error:
            self.error = _('The installer stopped (exit status {1}).', status)
        if self.error or status != 0:
            for s in self.steps:
                if s[2] == 'running':
                    s[2] = 'failed'

    @property
    def ok(self):
        return self.finished and self.exit == 0 and not self.error and self.result is not None

    def overall(self):
        """0..100 for the whole run: the stage's own figure, with the download filling its share (20 to 60)."""
        if self.current == 'download' and self.files:
            got = sum(f['bytes'] for f in self.files.values())
            total = max(self.plan_total, sum(f['total'] for f in self.files.values()))
            if total > 0:
                return min(60, 20 + int(40 * min(1.0, got / total)))
        return self.percent
