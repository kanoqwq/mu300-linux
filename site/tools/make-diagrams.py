#!/usr/bin/env python3
"""Draw the website's diagrams as SVG files, in English, Turkish and Chinese.

    python3 site/tools/make-diagrams.py

writes site/assets/img/<name>.svg (English) and <name>.tr.svg, <name>.zh.svg. The files are committed, so the
site itself has no build step; run this again after changing a label or a shape here. Standard library only.

Every picture carries its own light and dark colours. As a file (an <img>, the wiki) it follows the system's
prefers-color-scheme; written into a page, it follows the site's theme switch too (data-theme on <html>). Its
classes all hang under .mu-dg and its ids carry the picture's name, so several of them can share one page.
"""
import os
import re
from xml.sax.saxutils import escape

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "img")

FONT = ('"Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", '
        '"Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif')

LIGHT = """
text { font-family: %s; fill: #142133; }
.t-muted { fill: #526276; }
.t-white { fill: #ffffff; }
.t-mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
.ink { fill: #142133; } .stroke-ink { stroke: #142133; }
.paper { fill: #ffffff; } .soft { fill: #eef2f8; } .line { stroke: #c9d4e2; } .fill-line { fill: #c9d4e2; }
.lx { fill: #f2b23a; } .lx-soft { fill: #fbf0d8; } .lx-stroke { stroke: #b97700; } .t-lx { fill: #8a5900; }
.an { fill: #3fae6a; } .an-soft { fill: #def2e6; } .an-stroke { stroke: #217a47; } .t-an { fill: #17603a; }
.bl { fill: #2259d6; } .bl-soft { fill: #e3ebfb; } .bl-stroke { stroke: #2259d6; } .t-bl { fill: #1b47ad; }
.rd { fill: #c4302f; } .rd-soft { fill: #fbe3e2; } .rd-stroke { stroke: #c4302f; } .t-rd { fill: #a12625; }
.body-top { fill: #fdfdfe; } .body-side { fill: #dde4ee; } .body-edge { stroke: #b6c2d2; }
.led-white { fill: #ffffff; stroke: #b6c2d2; }
""" % FONT

DARK = """
text { fill: #e3ebf5; }
.t-muted { fill: #9cabbe; }
.ink { fill: #e3ebf5; } .stroke-ink { stroke: #e3ebf5; }
.paper { fill: #142031; } .soft { fill: #1a283b; } .line { stroke: #34465f; } .fill-line { fill: #34465f; }
.lx-soft { fill: #33290f; } .lx-stroke { stroke: #f2b740; } .t-lx { fill: #f2c060; }
.an-soft { fill: #12301f; } .an-stroke { stroke: #5ccf8a; } .t-an { fill: #7ddba3; }
.bl { fill: #5b88f0; } .bl-soft { fill: #1a2a48; } .bl-stroke { stroke: #7ea6ff; } .t-bl { fill: #9dbbff; }
.rd-soft { fill: #3a1a1a; } .rd-stroke { stroke: #ff7b72; } .t-rd { fill: #ff9b94; }
.body-top { fill: #2a3a52; } .body-side { fill: #1c2a3d; } .body-edge { stroke: #44587a; }
.led-white { fill: #f4f7fb; stroke: #44587a; }
"""


def rules(css):
    return re.findall(r"([^{}]+?)\s*\{([^{}]*)\}", css)


def style():
    """The colours, scoped under .mu-dg. Dark applies when the system asks for it and the page has not been set to
    light (:root is <html> in a page, the <svg> itself as a file), or when the page has been set to dark."""
    out = []
    for sel, decl in rules(LIGHT):
        out.append(".mu-dg %s {%s}" % (sel.strip(), decl))
    auto, forced = [], []
    for sel, decl in rules(DARK):
        sel = sel.strip()
        auto.append('  :root:not([data-theme="light"]) .mu-dg %s, .mu-dg:root %s {%s}' % (sel, sel, decl))
        forced.append(':root[data-theme="dark"] .mu-dg %s {%s}' % (sel, decl))
    return "\n".join(out + ["@media (prefers-color-scheme: dark) {"] + auto + ["}"] + forced)


STYLE = style()


def svg(w, h, title, desc, body, lang, uid):
    body = body.replace('id="ah"', 'id="%s-ah"' % uid).replace("url(#ah)", "url(#%s-ah)" % uid)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" class="mu-dg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
        'aria-labelledby="%s-t %s-d" xml:lang="%s">\n'
        '<title id="%s-t">%s</title>\n<desc id="%s-d">%s</desc>\n<style>\n%s\n</style>\n%s\n</svg>\n'
        % (w, h, w, h, uid, uid, lang, uid, escape(title), uid, escape(desc), STYLE, body)
    )


def text(x, y, s, size=18, cls="", anchor="start", weight=None):
    w = ' font-weight="%s"' % weight if weight else ""
    c = ' class="%s"' % cls if cls else ""
    return '<text x="%g" y="%g" font-size="%g" text-anchor="%s"%s%s>%s</text>' % (
        x, y, size, anchor, w, c, escape(s))


def lines(x, y, rows, size=18, gap=1.3, **kw):
    return "\n".join(text(x, y + i * size * gap, r, size, **kw) for i, r in enumerate(rows))


def arrow_defs():
    return ('<defs><marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" class="ink"/></marker></defs>')


# ---------------------------------------------------------------------------------------------------- labels

L = {
    "en": {
        "two_title": "Android and Linux on one device",
        "two_desc": "The internal storage: Android's partitions stay as they are, and Linux goes into the free space "
                    "after them. The device has two boot slots; Android keeps its own, and Linux's boot image goes "
                    "into the other one.",
        "storage": "Internal storage (the usual 64 GB device)",
        "and_parts": ["Android's partitions"],
        "and_sub": "system, apps, your data",
        "and_note": "left as they are",
        "lx_space": ["Free space, about 32 GiB"],
        "lx_sub": "Ubuntu and/or OpenWrt",
        "lx_note": "Linux lives here",
        "slots": "Two boot slots: two front doors into the same house",
        "slot_a": "Android's slot",
        "slot_a_sub": "never written",
        "slot_b": "The other slot",
        "slot_b_sub": "Linux's boot image",
        "sd": ["F50: Linux can live", "on an SD card instead"],
        "boot_title": "What happens when the device starts",
        "boot_desc": "The device starts Linux as a trial. If Linux comes up, the boot is confirmed and the count of "
                     "tries is full again. If it does not, one try is used up; when the tries run out, the device "
                     "starts Android by itself.",
        "b_power": ["Power on"],
        "b_try": ["The bootloader starts Linux", "and uses up one try"],
        "b_q": ["Is Linux up within", "about a minute?"],
        "b_q_sub": "USB network and SSH",
        "b_ok": ["Boot confirmed:", "all tries are back"],
        "b_use": ["You use Linux"],
        "b_fail": ["Not confirmed:", "one try fewer"],
        "b_left": ["Tries left?"],
        "b_android": ["Android starts", "by itself"],
        "yes": "yes", "no": "no",
        "b_next": "next start",
        "b_note": ["Tries: 1 to 6 (5 unless you chose otherwise).", "Locked Linux never goes back on its own."],
        "net_title": "How the device connects everything",
        "net_desc": "Phones and laptops join the device's Wi-Fi hotspot or plug into its USB port. The device "
                    "takes them to the internet over 5G or LTE with its SIM card, or over another Wi-Fi network.",
        "n_phone": "Phone", "n_laptop": "Laptop",
        "n_wifi": "Wi-Fi hotspot", "n_usb": "USB cable",
        "n_dev": "F50 / U30 Air",
        "n_addr": ["192.168.77.1 (F50)", "192.168.78.1 (U30 Air)"],
        "n_cell": ["5G / LTE", "with your SIM"],
        "n_wclient": ["or another", "Wi-Fi network"],
        "n_net": "Internet",
    },
    "tr": {
        "two_title": "Tek cihazda Android ve Linux",
        "two_desc": "Dahili depolama: Android'in bölümleri olduğu gibi kalır, Linux onlardan sonraki boş alana "
                    "kurulur. Cihazın iki açılış yuvası vardır; Android kendi yuvasında kalır, Linux'un açılış "
                    "imajı diğerine yazılır.",
        "storage": "Dahili depolama (yaygın 64 GB cihaz)",
        "and_parts": ["Android'in bölümleri"],
        "and_sub": "sistem, uygulamalar, verileriniz",
        "and_note": "olduğu gibi kalır",
        "lx_space": ["Boş alan, yaklaşık 32 GiB"],
        "lx_sub": "Ubuntu ve/veya OpenWrt",
        "lx_note": "Linux burada yaşar",
        "slots": "İki açılış yuvası: aynı evin iki ön kapısı",
        "slot_a": "Android'in yuvası",
        "slot_a_sub": "hiç yazılmaz",
        "slot_b": "Diğer yuva",
        "slot_b_sub": "Linux'un açılış imajı",
        "sd": ["F50: Linux bunun yerine", "SD kartta da durabilir"],
        "boot_title": "Cihaz açılırken ne olur",
        "boot_desc": "Cihaz Linux'u deneme olarak başlatır. Linux açılırsa açılış onaylanır ve deneme hakları "
                     "yeniden dolar. Açılmazsa bir hak gider; haklar bitince cihaz Android'i kendiliğinden başlatır.",
        "b_power": ["Cihaz açılır"],
        "b_try": ["Önyükleyici Linux'u başlatır", "ve bir deneme hakkı harcar"],
        "b_q": ["Linux yaklaşık bir dakika", "içinde açıldı mı?"],
        "b_q_sub": "USB ağı ve SSH",
        "b_ok": ["Açılış onaylandı:", "bütün haklar geri geldi"],
        "b_use": ["Linux'u kullanırsınız"],
        "b_fail": ["Onaylanmadı:", "bir hak eksildi"],
        "b_left": ["Hak kaldı mı?"],
        "b_android": ["Android kendiliğinden", "açılır"],
        "yes": "evet", "no": "hayır",
        "b_next": "sonraki açılış",
        "b_note": ["Deneme hakkı: 1 ile 6 arası (seçmezseniz 5).", "Kilitli Linux kendiliğinden Android'e dönmez."],
        "net_title": "Cihaz her şeyi nasıl bağlar",
        "net_desc": "Telefonlar ve dizüstü bilgisayarlar cihazın Wi-Fi hotspot'una katılır ya da USB'den bağlanır. "
                    "Cihaz onları SIM kartıyla 5G veya LTE üzerinden ya da başka bir Wi-Fi ağı üzerinden internete "
                    "çıkarır.",
        "n_phone": "Telefon", "n_laptop": "Dizüstü",
        "n_wifi": "Wi-Fi hotspot", "n_usb": "USB kablosu",
        "n_dev": "F50 / U30 Air",
        "n_addr": ["192.168.77.1 (F50)", "192.168.78.1 (U30 Air)"],
        "n_cell": ["5G / LTE", "SIM kartınızla"],
        "n_wclient": ["ya da başka", "bir Wi-Fi ağı"],
        "n_net": "İnternet",
    },
    "zh": {
        "two_title": "一台设备上的 Android 和 Linux",
        "two_desc": "内部存储：Android 的分区保持原样，Linux 安装在它们后面的空闲空间里。设备有两个启动槽位；"
                    "Android 保留自己的槽位，Linux 的启动镜像写入另一个。",
        "storage": "内部存储（常见的 64 GB 设备）",
        "and_parts": ["Android 的分区"],
        "and_sub": "系统、应用、你的数据",
        "and_note": "保持原样",
        "lx_space": ["空闲空间，约 32 GiB"],
        "lx_sub": "Ubuntu 和/或 OpenWrt",
        "lx_note": "Linux 住在这里",
        "slots": "两个启动槽位：同一栋房子的两扇前门",
        "slot_a": "Android 的槽位",
        "slot_a_sub": "从不写入",
        "slot_b": "另一个槽位",
        "slot_b_sub": "Linux 的启动镜像",
        "sd": ["F50：Linux 也可以", "放在 SD 卡上"],
        "boot_title": "设备启动时发生什么",
        "boot_desc": "设备以试用方式启动 Linux。Linux 启动成功后，这次启动被确认，尝试次数重新补满。"
                     "如果没有成功，就用掉一次；次数用完时，设备会自动启动 Android。",
        "b_power": ["开机"],
        "b_try": ["引导程序启动 Linux，", "并用掉一次尝试"],
        "b_q": ["Linux 大约一分钟内", "启动成功了吗？"],
        "b_q_sub": "USB 网络和 SSH",
        "b_ok": ["启动已确认：", "尝试次数全部恢复"],
        "b_use": ["你在使用 Linux"],
        "b_fail": ["未确认：", "少一次尝试"],
        "b_left": ["还有次数吗？"],
        "b_android": ["自动启动", "Android"],
        "yes": "是", "no": "否",
        "b_next": "下次启动",
        "b_note": ["尝试次数：1 到 6（默认 5）。", "锁定的 Linux 不会自动回到 Android。"],
        "net_title": "设备如何把一切连起来",
        "net_desc": "手机和笔记本连接设备的 Wi-Fi 热点，或插在它的 USB 口上。设备通过 SIM 卡的 5G 或 LTE，"
                    "或者另一个 Wi-Fi 网络，把它们带到互联网。",
        "n_phone": "手机", "n_laptop": "笔记本",
        "n_wifi": "Wi-Fi 热点", "n_usb": "USB 数据线",
        "n_dev": "F50 / U30 Air",
        "n_addr": ["192.168.77.1（F50）", "192.168.78.1（U30 Air）"],
        "n_cell": ["5G / LTE", "使用你的 SIM 卡"],
        "n_wclient": ["或者另一个", "Wi-Fi 网络"],
        "n_net": "互联网",
    },
}


# ---------------------------------------------------------------------------------------------------- pictures

def device_body(x, y, s=1.0, leds=True, led_class=True):
    """A pocket hotspot seen from the front: a rounded box, three status LEDs, a USB-C port, two little rooms."""
    def P(v):
        return v * s
    out = ['<g transform="translate(%g %g)">' % (x, y)]
    # radio waves above it
    out.append('<g fill="none" stroke-linecap="round" class="bl-stroke" stroke-width="%g">' % P(7))
    for r in (52, 86, 120):
        out.append('<path d="M %g %g A %g %g 0 0 1 %g %g"/>' % (P(150 - r * .7), P(40 - r * .55), P(r), P(r),
                                                                P(150 + r * .7), P(40 - r * .55)))
    out.append('</g>')
    # body: the side, then the face
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="body-side"/>' % (P(8), P(58), P(284), P(300), P(46)))
    out.append('<rect x="0" y="%g" width="%g" height="%g" rx="%g" class="body-top body-edge" stroke-width="%g"/>'
               % (P(46), P(284), P(296), P(44), P(2)))
    # LEDs
    if leds:
        specs = [("bl", 92), ("led-white", 142), ("lx", 192)]
        for i, (c, cx) in enumerate(specs, 1):
            lc = ' led led-%d' % i if led_class else ''
            out.append('<circle cx="%g" cy="%g" r="%g" class="%s%s" stroke-width="%g"/>' % (P(cx), P(92), P(10), c, lc, P(2)))
    # the two rooms: Android (green, a phone) and Linux (amber, a terminal prompt)
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="an-soft"/>' % (P(36), P(136), P(100), P(150), P(18)))
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="lx-soft"/>' % (P(148), P(136), P(100), P(150), P(18)))
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="none" class="an-stroke" stroke-width="%g"/>'
               % (P(64), P(170), P(44), P(78), P(9), P(6)))
    out.append('<circle cx="%g" cy="%g" r="%g" class="an"/>' % (P(86), P(234), P(4.5)))
    out.append('<path d="M %g %g l %g %g l %g %g" fill="none" class="lx-stroke" stroke-width="%g" '
               'stroke-linecap="round" stroke-linejoin="round"/>' % (P(170), P(186), P(24), P(22), P(-24), P(22), P(8)))
    out.append('<path d="M %g %g h %g" class="lx-stroke" stroke-width="%g" stroke-linecap="round"/>'
               % (P(204), P(238), P(26), P(8)))
    # USB-C port on the bottom edge
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="ink" opacity=".75"/>' % (P(118), P(334), P(48), P(12), P(6)))
    out.append('</g>')
    return "\n".join(out)


LED_MOTION = """<style>
@media (prefers-reduced-motion: no-preference) {
  .mu-dg .led { animation: led-on .5s ease-out both; }
  .mu-dg .led-2 { animation-delay: .5s; } .mu-dg .led-3 { animation-delay: 1s; }
  @keyframes led-on { from { opacity: .25; } to { opacity: 1; } }
}
</style>"""


def make_device():
    # the one bit of motion on the site: the LEDs light up once, one after the other
    body = LED_MOTION + device_body(78, 64)
    return svg(440, 440, "A 5G pocket hotspot running Linux",
               "A drawing of a small 5G hotspot with three status LEDs. Its face shows two rooms: a green one "
               "for Android and an amber one for Linux.", body, "en", "device")


def make_two(lang):
    t = L[lang]
    W, H = 960, 440
    b = [arrow_defs()]
    b.append(text(24, 40, t["storage"], 20, weight=700))
    # storage bar
    bx, by, bw, bh = 24, 62, 912, 96
    split = bx + int(bw * 0.45)
    b.append('<rect x="%d" y="%d" width="%d" height="%d" rx="14" class="an-soft"/>' % (bx, by, split - bx + 14, bh))
    b.append('<rect x="%d" y="%d" width="%d" height="%d" rx="14" class="lx-soft"/>' % (split, by, bx + bw - split, bh))
    b.append('<rect x="%d" y="%d" width="%d" height="%d" class="an"/>' % (split - 3, by, 6, bh))
    # partition ticks inside Android's part
    for i in range(1, 9):
        x = bx + i * (split - bx) / 9
        b.append('<line x1="%g" y1="%d" x2="%g" y2="%d" class="an-stroke" stroke-width="1.5" opacity=".35"/>' % (x, by + bh - 20, x, by + bh - 6))
    b.append(text(bx + 20, by + 42, t["and_parts"][0], 19, cls="t-an", weight=700))
    b.append(text(bx + 20, by + 70, t["and_sub"], 16, cls="t-muted"))
    b.append(text(split + 24, by + 42, t["lx_space"][0], 19, cls="t-lx", weight=700))
    b.append(text(split + 24, by + 70, t["lx_sub"], 16, cls="t-muted"))
    # notes under the bar
    b.append('<path d="M %d %d v 18" class="an-stroke" stroke-width="2"/>' % ((bx + split) / 2, by + bh + 4))
    b.append(text((bx + split) / 2, by + bh + 44, t["and_note"], 16, cls="t-an", anchor="middle", weight=700))
    b.append('<path d="M %d %d v 18" class="lx-stroke" stroke-width="2"/>' % ((split + bx + bw) / 2, by + bh + 4))
    b.append(text((split + bx + bw) / 2, by + bh + 44, t["lx_note"], 16, cls="t-lx", anchor="middle", weight=700))
    # slots
    sy = 262
    b.append(text(24, sy, t["slots"], 20, weight=700))
    def slot(x, cls, title, sub, icon):
        g = ['<rect x="%d" y="%d" width="300" height="118" rx="16" class="paper %s" stroke-width="3" fill-opacity="1"/>' % (x, sy + 22, cls)]
        g.append(icon)
        g.append(text(x + 110, sy + 72, title, 19, weight=700))
        g.append(text(x + 110, sy + 100, sub, 16, cls="t-muted"))
        return "\n".join(g)
    door_a = ('<rect x="%d" y="%d" width="56" height="78" rx="8" class="an"/><circle cx="%d" cy="%d" r="4" class="paper"/>'
              % (54, sy + 42, 98, sy + 84))
    door_b = ('<rect x="%d" y="%d" width="56" height="78" rx="8" class="lx"/><circle cx="%d" cy="%d" r="4" class="paper"/>'
              % (374, sy + 42, 418, sy + 84))
    b.append(slot(24, "an-stroke", t["slot_a"], t["slot_a_sub"], door_a))
    b.append(slot(344, "lx-stroke", t["slot_b"], t["slot_b_sub"], door_b))
    # SD card
    sx = 696
    b.append('<path d="M %d %d h 46 l 18 18 v 68 a 6 6 0 0 1 -6 6 h -58 a 6 6 0 0 1 -6 -6 v -80 a 6 6 0 0 1 6 -6 z" '
             'class="bl-soft bl-stroke" stroke-width="3"/>' % (sx + 6, sy + 30))
    for i in range(4):
        b.append('<rect x="%d" y="%d" width="7" height="16" rx="2" class="bl"/>' % (sx + 14 + i * 12, sy + 40))
    b.append(lines(sx + 84, sy + 64, t["sd"], 16, cls="t-bl"))
    return svg(W, H, t["two_title"], t["two_desc"], "\n".join(b), lang, "two-" + lang)


def box(x, y, w, h, rows, cls="paper line", tcls="", size=17, rx=14, weight=None):
    g = ['<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="%s" stroke-width="2.5"/>' % (x, y, w, h, rx, cls)]
    n = len(rows)
    top = y + h / 2 - (n - 1) * size * 1.3 / 2 + size * .35
    g.append(lines(x + w / 2, top, rows, size, anchor="middle", cls=tcls, weight=weight))
    return "\n".join(g)


def diamond(cx, cy, w, h, rows, sub=None, size=17):
    pts = "%g,%g %g,%g %g,%g %g,%g" % (cx, cy - h / 2, cx + w / 2, cy, cx, cy + h / 2, cx - w / 2, cy)
    g = ['<polygon points="%s" class="bl-soft bl-stroke" stroke-width="2.5"/>' % pts]
    n = len(rows) + (1 if sub else 0)
    top = cy - (n - 1) * size * 1.3 / 2 + size * .35
    g.append(lines(cx, top, rows, size, anchor="middle", weight=700))
    if sub:
        g.append(text(cx, top + len(rows) * size * 1.3, sub, size - 3, cls="t-muted", anchor="middle"))
    return "\n".join(g)


def line(x1, y1, x2, y2, arrow=True, dash=False):
    return '<path d="M %g %g L %g %g" class="stroke-ink" stroke-width="2.5" fill="none"%s%s/>' % (
        x1, y1, x2, y2, ' marker-end="url(#ah)"' if arrow else "", ' stroke-dasharray="7 6"' if dash else "")


def path(d, dash=False):
    return '<path d="%s" class="stroke-ink" stroke-width="2.5" fill="none" marker-end="url(#ah)"%s/>' % (
        d, ' stroke-dasharray="7 6"' if dash else "")


def make_boot(lang):
    t = L[lang]
    W, H = 960, 860
    b = [arrow_defs()]
    cx = 280
    b.append(box(cx - 120, 24, 240, 60, t["b_power"], "soft line", size=20, weight=700, rx=30))
    b.append(line(cx, 84, cx, 120))
    b.append(box(cx - 180, 122, 360, 92, t["b_try"], "lx-soft lx-stroke", size=20))
    b.append(line(cx, 214, cx, 250))
    b.append(diamond(cx, 356, 400, 210, t["b_q"], t["b_q_sub"], size=20))
    # yes: to the right
    b.append(line(cx + 200, 356, 600, 356))
    b.append(text(cx + 224, 342, t["yes"], 18, cls="t-muted", weight=700))
    b.append(box(602, 310, 330, 92, t["b_ok"], "lx-soft lx-stroke", size=20))
    b.append(line(767, 402, 767, 450))
    b.append(box(637, 452, 260, 60, t["b_use"], "lx", size=20, weight=700, rx=30))
    # no: down
    b.append(line(cx, 461, cx, 510))
    b.append(text(cx + 14, 492, t["no"], 18, cls="t-muted", weight=700))
    b.append(box(cx - 170, 512, 340, 86, t["b_fail"], "rd-soft rd-stroke", size=20))
    b.append(line(cx, 598, cx, 634))
    b.append(diamond(cx, 700, 270, 130, t["b_left"], size=20))
    # tries left: back to the start, along the left edge
    b.append(path("M %g 700 H 40 V 54 H %g" % (cx - 135, cx - 122)))
    b.append(text(cx - 150, 688, t["yes"], 18, cls="t-muted", anchor="end", weight=700))
    b.append('<g transform="translate(28 380) rotate(-90)">%s</g>' % text(0, 0, t["b_next"], 17, cls="t-muted", anchor="middle"))
    # no tries left: Android
    b.append(line(cx + 135, 700, 600, 700))
    b.append(text(cx + 160, 688, t["no"], 18, cls="t-muted", weight=700))
    b.append(box(602, 660, 330, 80, t["b_android"], "an-soft an-stroke", size=20, weight=700))
    b.append(lines(40, 806, t["b_note"], 17, cls="t-muted"))
    return svg(W, H, t["boot_title"], t["boot_desc"], "\n".join(b), lang, "boot-" + lang)


def person_icon(x, y, kind):
    if kind == "phone":
        return ('<rect x="%g" y="%g" width="54" height="94" rx="10" class="paper stroke-ink" stroke-width="3"/>'
                '<rect x="%g" y="%g" width="20" height="4" rx="2" class="ink"/>' % (x, y, x + 17, y + 82))
    return ('<rect x="%g" y="%g" width="104" height="66" rx="8" class="paper stroke-ink" stroke-width="3"/>'
            '<path d="M %g %g h 136 l -10 12 h -116 z" class="ink"/>' % (x, y, x - 16, y + 72))


def make_net(lang):
    t = L[lang]
    W, H = 980, 410
    b = [arrow_defs()]
    # clients
    b.append(person_icon(40, 40, "phone"))
    b.append(text(67, 160, t["n_phone"], 16, anchor="middle", weight=700))
    b.append(person_icon(30, 236, "laptop"))
    b.append(text(82, 346, t["n_laptop"], 16, anchor="middle", weight=700))
    # links to the device
    b.append('<path d="M 110 88 C 200 88 230 150 300 170" class="bl-stroke" stroke-width="3" fill="none" stroke-dasharray="2 8" stroke-linecap="round"/>')
    b.append(text(196, 82, t["n_wifi"], 16, cls="t-bl", anchor="middle", weight=700))
    b.append('<path d="M 150 270 C 220 270 240 240 300 232" class="stroke-ink" stroke-width="5" fill="none" stroke-linecap="round"/>')
    b.append(text(212, 300, t["n_usb"], 16, anchor="middle", weight=700))
    # the device, small
    b.append(device_body(300, 76, s=0.5, led_class=False))
    b.append(text(371, 286, t["n_dev"], 17, anchor="middle", weight=700))
    b.append(lines(371, 312, t["n_addr"], 14, cls="t-muted t-mono", anchor="middle"))
    # uplinks: a tower, and another Wi-Fi
    tx = 600
    b.append('<path d="M %g 210 L %g 70 L %g 210 M %g 120 H %g M %g 165 H %g" class="stroke-ink" stroke-width="4" '
             'fill="none" stroke-linejoin="round"/>' % (tx - 32, tx, tx + 32, tx - 16, tx + 16, tx - 24, tx + 24))
    b.append('<circle cx="%g" cy="62" r="8" class="bl"/>' % tx)
    b.append('<g fill="none" class="bl-stroke" stroke-width="4" stroke-linecap="round">'
             '<path d="M %g 44 a 26 26 0 0 0 0 36"/><path d="M %g 44 a 26 26 0 0 1 0 36"/></g>' % (tx - 20, tx + 20))
    b.append(lines(tx, 238, t["n_cell"], 16, anchor="middle", weight=700))
    b.append(path("M 446 150 C 500 140 530 140 560 140"))
    # other Wi-Fi
    b.append('<g transform="translate(%g 300)" fill="none" class="stroke-ink" stroke-width="4" stroke-linecap="round">'
             '<path d="M -26 0 a 37 37 0 0 1 52 0"/><path d="M -15 11 a 21 21 0 0 1 30 0"/></g>'
             '<circle cx="%g" cy="322" r="5" class="ink"/>' % (tx, tx))
    b.append(lines(tx, 356, t["n_wclient"], 15, cls="t-muted", anchor="middle"))
    b.append(path("M 446 230 C 500 280 530 300 560 304", dash=True))
    # internet
    ix = 860
    b.append('<path d="M %g 230 a 40 40 0 0 1 8 -79 a 54 54 0 0 1 102 -6 a 36 36 0 0 1 4 85 z" class="bl-soft bl-stroke" '
             'stroke-width="3"/>' % (ix - 74))
    b.append(text(ix, 204, t["n_net"], 19, cls="t-bl", anchor="middle", weight=700))
    b.append(path("M 650 140 C 700 140 730 160 772 175"))
    b.append(path("M 650 312 C 720 312 770 280 800 238", dash=True))
    return svg(W, H, t["net_title"], t["net_desc"], "\n".join(b), lang, "net-" + lang)


def main():
    os.makedirs(OUT, exist_ok=True)
    files = {"device.svg": make_device()}
    for lang in ("en", "tr", "zh"):
        suffix = "" if lang == "en" else "." + lang
        files["two-systems%s.svg" % suffix] = make_two(lang)
        files["boot-fallback%s.svg" % suffix] = make_boot(lang)
        files["network%s.svg" % suffix] = make_net(lang)
    for name, data in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(data)
        print(name)


if __name__ == "__main__":
    main()
