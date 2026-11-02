#!/bin/bash
# usage (WSL root): bash tests/ui/fuzz.sh [seed] -- random taps/swipes/holds/keys against an ASAN+UBSAN build of the UI
cd "$(dirname "$0")/../.."
seed=${1:-1}
mkdir -p /opt/u30test /tmp/u30ui-run
gcc -DUI_HOST '-DUI_RUN="/tmp/u30ui-run"' "-DUI_PLUGIN_DIR=\"$PWD/tests/ui/plugins\"" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Wall -Wextra -o /opt/u30test/ui-host-asan tools/u30pro_ui.c -lm || exit 1
python3 - "$seed" > /tmp/u30ui-fuzz.txt <<'EOF'
import random, sys
r = random.Random(int(sys.argv[1]))
out = []
for i in range(4000):
    k = r.random()
    x, y = r.randint(0, 239), r.randint(0, 239)
    if k < .35: out.append('tap %d %d' % (x, y))
    elif k < .6: out.append('swipe %d %d %d %d %d' % (x, y, r.randint(0, 239), r.randint(0, 239), r.choice([80, 160, 400])))
    elif k < .68: out.append('hold %d %d %d' % (x, y, r.choice([300, 900, 1500, 2500])))
    elif k < .78: out.append('key %d' % r.choice([102, 114, 115, 116]))
    elif k < .84: out.append('wait %d' % r.choice([50, 500, 5000, 70000, 200000]))
    elif k < .88: out.append('page %d' % r.randint(-3, 12))
    elif k < .90: out.append('overlay %d %d' % (r.randint(0, 3), r.randint(0, 5)))
    elif k < .93: out.append('down %d %d' % (x, y))
    elif k < .96: out.append('move %d %d' % (x, y))
    elif k < .98: out.append('up')
    elif k < .93: out.append('sheet %d' % r.randint(0, 2))
    elif k < .95: out.append('fn %d' % r.randint(0, 14))
    elif k < .94: out.append('set battery %d' % r.choice([0, 5, 15, 50, 100]))
    elif k < .95: out.append('set nosig')
    elif k < .96: out.append('toast ' + 'x' * r.choice([1, 40, 120, 300]))
    elif k < .98: out.append('theme %d' % r.randint(0, 3))
    else: out.append('shot fuzz%d' % (i % 5))
print('\n'.join(out))
EOF
ASAN_OPTIONS=detect_leaks=0 /opt/u30test/ui-host-asan sim /tmp/u30ui-run < /tmp/u30ui-fuzz.txt > /tmp/u30ui-fuzz.out 2>&1
rc=$?
grep -E "ERROR: AddressSanitizer|runtime error" /tmp/u30ui-fuzz.out | sort | uniq -c | head -20
echo "fuzz seed $seed exit=$rc"
exit $rc
