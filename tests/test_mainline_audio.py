"""The unsupported call-audio stack must not return through builds or extras."""
import re
import unittest
from helpers import TOP
from test_extra import ExtrasBase

UP = TOP / 'upstream'
OVERLAY = TOP / 'rootfs' / 'overlay'


class NoCallAudio(ExtrasBase):
    def test_extra_list_keeps_other_components_but_not_call_audio(self):
        for shell in self.each_shell():
            r = self.up(shell, 'printf "%s\n" "$EXTRAS"; extra_known audio && exit 4; '
                        'extra_is_module audio && exit 5; '
                        'extra_known lang && extra_known vpn && extra_known panel')
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertNotIn('audio', r.stdout.split())
            r = self.ex(shell, 'install', 'audio')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no extra called 'audio'", r.stderr)

    def test_audio_assets_are_not_accepted_as_official_modules(self):
        for shell in self.each_shell():
            r = self.up(shell, 'module_asset_ok mu300-audio-7.2.tar.gz && exit 4; '
                        'module_asset_ok mu300-linux-vpn-core.tar.gz')
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))

    def test_legacy_extra_is_unlinked_before_removal_and_is_not_relinked(self):
        for shell in self.each_shell():
            old = self.disk / 'extra/audio'
            (old / 'hooks').mkdir(parents=True)
            (old / 'bin').mkdir()
            (old / 'hooks/unlink').write_text('echo "unlink [$1]" >> "$STUBLOG/hooks"\n')
            (old / 'hooks/link').write_text('echo BAD-LINK >> "$STUBLOG/hooks"\n')
            (old / 'bin/mu300-voice').write_text('#!/bin/sh\n')
            (old / 'bin/mu300-voice').chmod(0o755)
            cache = self.disk / 'extra/.audio-firmware'
            cache.mkdir()
            (cache / 'agdsp.bin').write_bytes(b'old DSP')
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertFalse(old.exists())
            self.assertFalse(cache.exists())
            self.assertFalse((self.links / 'mu300-voice').exists())
            self.assertNotIn('BAD-LINK', self.hooks())
            self.assertEqual(self.ex(shell, 'link').returncode, 0)

    def test_failed_legacy_uninstall_preserves_files_for_retry(self):
        for shell in self.each_shell():
            old = self.disk / 'extra/audio'
            (old / 'hooks').mkdir(parents=True, exist_ok=True)
            (old / 'hooks/unlink').write_text('exit 1\n')
            r = self.up(shell, 'retire_call_audio')
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(old.exists())
            (old / 'hooks/unlink').write_text('exit 0\n')
            self.assertEqual(self.up(shell, 'retire_call_audio').returncode, 0)
            self.assertFalse(old.exists())

    def test_help_stops_before_shell_implementation(self):
        for shell in self.each_shell():
            r = self.ex(shell, '--help')
            self.assertEqual(r.returncode, 0)
            self.assertIn('mu300-extra install NAME', r.stdout)
            self.assertNotIn('BIN=', r.stdout)
            self.assertNotIn('install audio', r.stdout)

    def test_no_call_audio_kernel_reservations_or_build_hooks(self):
        for f in ('upstream/mu300-mainline.config', 'kernel/mu300-linux.fragment'):
            text = (TOP / f).read_text()
            self.assertNotIn('CONFIG_OF_RESERVED_MEM_ADD=', text)
            self.assertNotIn('CONFIG_SND_SOC_SPRD=m', text)
        self.assertFalse((UP / 'patches/0022-of-reserved-mem-add.patch').exists())
        self.assertFalse((TOP / 'kernel/patches/of-reserved-mem-add.patch').exists())
        self.assertNotIn('of-reserved-mem-add', (TOP / 'kernel/build-all.sh').read_text())
        # External USB sound cards and Bluetooth are independent of the removed DSP stack.
        cfg = (UP / 'mu300-mainline.config').read_text()
        self.assertIn('CONFIG_SND_USB_AUDIO=m', cfg)
        self.assertIn('CONFIG_BT_HCIBTUSB=m', cfg)


class NotHereAnyMore(unittest.TestCase):
    def test_no_driver_of_the_stack_is_built(self):
        self.assertFalse((UP / 'modules' / 'sprd_audio').exists())
        self.assertFalse((TOP / 'kernel' / 'build-audio.sh').exists())
        src = (UP / 'build-modules.sh').read_text()
        mods = re.search(r'mods=\$\{\*:-([^}]*)\}', src).group(1).split()
        self.assertNotIn('sprd_audio', mods)
        self.assertNotIn('virt-dma', src)
        for p in ('audio-*.patch', 'mcdt-enable-clock.patch', 'sprd-card-dummy-on-defer.patch'):
            self.assertEqual(list((TOP / 'kernel' / 'patches').glob(p)), [], p)

    def test_no_program_or_service_of_the_stack_is_in_the_images(self):
        for f in ('opt/mu300/bin/mu300-audio-dsp', 'opt/mu300/bin/mu300-voice', 'etc/systemd/system/mu300-audio.service',
                  'etc/systemd/system/mu300-voice.service', 'etc/modprobe.d/mu300-audio.conf'):
            self.assertFalse((OVERLAY / f).exists(), f)
        for f in ('etc/init.d/mu300-audio', 'etc/init.d/mu300-voice'):
            self.assertFalse((TOP / 'openwrt' / 'overlay' / f).exists(), f)
        for t in ('pcmhost', 'ctlset', 'hfp'):
            self.assertFalse((TOP / 'tools' / t).exists(), t)
        self.assertNotIn('mu300-voice', (OVERLAY / 'opt/mu300/lib/path-commands').read_text().split())
        self.assertNotIn('mu300-audio.service', (TOP / 'rootfs' / 'assemble.sh').read_text())
        self.assertNotIn('mu300-voice', (OVERLAY / 'etc/tmpfiles.d/mu300-run.conf').read_text().split('\n', 3)[-1])


if __name__ == '__main__':
    unittest.main()
