"""Render the screen UI pages with the host simulator and write PNGs.
usage: python tests/ui/shots.py <out-dir> [script-file] [--guide]
The default script shows every page and overlay; a custom script uses the simulator's command language
(tap/swipe/hold/wait/key/page/overlay/set/theme/shot/toast)."""
import subprocess
import sys
from pathlib import Path

from PIL import Image

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
args = [a for a in sys.argv[2:] if a != '--guide']
guide = '--guide' in sys.argv
script = Path(args[0]).read_text(encoding='utf-8') if args else ''.join(
    'page %d\nshot page%d\n' % (i, i) for i in range(4)) + ''.join('fn %d\nwait 200\nshot fn%d\n' % (i, i) for i in range(12)) + """page 3
fnscroll 160
shot func2
page 0
sheet 1
shot control
sheet 2
overlay 0 0
shot sms
overlay 2 0
shot sms_detail
sheet 0
page 0
fn 0
overlay 1 0
shot qr
page 0
set battery 12
toast 电量低 12%
shot lowbat
page 0
set nosig
set clock0
shot nosig
theme 1
page 1
shot theme_aurora
"""
drive = str(out).replace('\\', '/')
wsl_out = '/mnt/' + drive[0].lower() + drive[2:]
cmd = ['wsl.exe', '-u', 'root', '-e', '/opt/u30test/ui-host', 'sim', wsl_out] + (['--guide'] if guide else [])
r = subprocess.run(cmd, input=script.encode('utf-8'), capture_output=True)
sys.stdout.write(r.stdout.decode('utf-8', 'replace')[-400:])
sys.stderr.write(r.stderr.decode('utf-8', 'replace')[-400:])
names = []
for ppm in sorted(out.glob('*.ppm')):
    im = Image.open(ppm).convert('RGB')
    im.resize((480, 480), Image.LANCZOS).save(ppm.with_suffix('.png'))
    names.append(ppm.stem)
# contact sheet, 4 per row at 1x
imgs = [Image.open(out / (n + '.ppm')).convert('RGB') for n in names]
cols = 4
rows = (len(imgs) + cols - 1) // cols
sheet = Image.new('RGB', (cols * 244, rows * 244), (30, 30, 30))
for i, im in enumerate(imgs):
    sheet.paste(im, ((i % cols) * 244 + 2, (i // cols) * 244 + 2))
sheet.save(out / 'sheet.png')
print('rendered', len(names), 'images:', ' '.join(names))
