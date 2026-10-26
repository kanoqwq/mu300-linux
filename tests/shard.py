"""The test modules of one CI shard: `python3 shard.py N I` prints the test_*.py module names (no .py) that shard I
of N runs, round-robin over the sorted list so every module runs in exactly one shard. The heaviest modules are
spread by hand (HEAVY) so no shard carries two of them."""
import sys
from pathlib import Path

HEAVY = ['test_device_scripts', 'test_panel', 'test_power', 'test_update', 'test_sms_pool', 'test_luci_i18n',
         'test_installer', 'test_android_install', 'test_answers', 'test_gui']


def shards(n):
    mods = sorted(p.stem for p in Path(__file__).parent.glob('test_*.py'))
    heavy = [m for m in HEAVY if m in mods]
    rest = [m for m in mods if m not in heavy]
    out = [[] for _ in range(n)]
    for i, m in enumerate(heavy + rest):
        out[i % n].append(m)
    return out


if __name__ == '__main__':
    n, i = int(sys.argv[1]), int(sys.argv[2])
    print(' '.join(shards(n)[i]))
