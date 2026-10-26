"""How the window looks: one palette for light and one for dark, one accent colour, a spacing scale, the fonts of
each system, and the ttk styles made from them. Nothing of the system's own theme is used for drawing (ttk's "clam"
is the base, every colour is set here), so the window is the same on Windows, macOS and Linux, and on a Tk as old as
8.5 (the one macOS's Command Line Tools ship).
"""
import os
import subprocess
import sys

# the spacing scale: every gap in the window is one of these
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32

LIGHT = {
    'bg': '#F4F3EF',            # the window
    'surface': '#FFFFFF',       # cards
    'sunken': '#ECEAE4',        # the sidebar, wells, the log
    'line': '#DAD7CE',          # borders and rules
    'text': '#1E211F',
    'muted': '#666A66',
    'faint': '#9A9D98',
    'accent': '#0B6E63',        # the one accent: actions, the step that runs, the place Linux goes
    'accent_text': '#FFFFFF',
    'accent_soft': '#D8ECE8',
    'good': '#1F7A3D',
    'good_soft': '#DDF0E2',
    'warn': '#9A5B00',
    'warn_soft': '#FBEBCB',
    'bad': '#B3261E',
    'bad_soft': '#F8DDDA',
    'seg_android': '#8E99A8',   # the picture of the eMMC
    'seg_userdata': '#B9C1CC',
    'seg_other': '#DAD7CE',
    'log_bg': '#1F2321',
    'log_text': '#D9DDD8',
}
DARK = {
    'bg': '#171A19',
    'surface': '#212524',
    'sunken': '#131615',
    'line': '#343A38',
    'text': '#E8EAE6',
    'muted': '#A0A6A1',
    'faint': '#6F7672',
    'accent': '#3CC8B4',
    'accent_text': '#06211D',
    'accent_soft': '#173A35',
    'good': '#6FD08C',
    'good_soft': '#173323',
    'warn': '#F0B757',
    'warn_soft': '#3A2C10',
    'bad': '#FF8A80',
    'bad_soft': '#40201D',
    'seg_android': '#5E6A7A',
    'seg_userdata': '#3F4854',
    'seg_other': '#2A2F2D',
    'log_bg': '#0F1211',
    'log_text': '#C9CEC8',
}


def system_dark(environ=None):
    """Whether the system is set to a dark appearance, as far as it can be asked; MU300_GUI_THEME=light|dark says."""
    environ = os.environ if environ is None else environ
    forced = environ.get('MU300_GUI_THEME', '').lower()
    if forced in ('light', 'dark'):
        return forced == 'dark'
    try:
        if sys.platform == 'darwin':
            r = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'], capture_output=True, text=True, timeout=5)
            return 'dark' in r.stdout.lower()
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
                return winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 0
        if environ.get('GTK_THEME', '').lower().endswith(':dark'):
            return True
        r = subprocess.run(['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'], capture_output=True, text=True, timeout=5)
        return 'dark' in r.stdout.lower()
    except Exception:       # no such setting, no such tool: light
        return False


def dpi_aware():
    """Windows draws a window that does not say it knows about the screen's scaling at 96 dpi and stretches it,
    which blurs every letter. Say so before the first window exists. (macOS and Wayland/X11 scale by themselves.)"""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # Windows 8.1 and newer: aware of the system's scaling
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()           # older
    except Exception:
        pass


def fonts(root):
    """The font families of this system that are there, for text and for the log: (family, mono family)."""
    import tkinter.font as tkfont
    have = set(tkfont.families(root))
    if sys.platform == 'darwin':
        ui = ['SF Pro Text', 'Helvetica Neue', 'Lucida Grande']
        mono = ['SF Mono', 'Menlo', 'Monaco']
    elif sys.platform == 'win32':
        ui = ['Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI', 'Tahoma']
        mono = ['Cascadia Mono', 'Consolas', 'Courier New']
    else:
        ui = ['Inter', 'Noto Sans', 'Cantarell', 'Ubuntu', 'DejaVu Sans', 'Liberation Sans']
        mono = ['JetBrains Mono', 'Noto Sans Mono', 'DejaVu Sans Mono', 'Liberation Mono']
    default = tkfont.nametofont('TkDefaultFont').actual('family')
    fixed = tkfont.nametofont('TkFixedFont').actual('family')
    return next((f for f in ui if f in have), default), next((f for f in mono if f in have), fixed)


class Theme:
    """The palette in use, the named fonts, and the ttk styles. apply() can be called again with another palette."""

    def __init__(self, root, dark=False):
        import tkinter.font as tkfont
        self.root = root
        self.dark = dark
        self.c = dict(DARK if dark else LIGHT)
        family, mono = fonts(root)
        base = 13 if sys.platform == 'darwin' else 10
        self.f = {
            'body': tkfont.Font(root, family=family, size=base),
            'small': tkfont.Font(root, family=family, size=base - 1),
            'bold': tkfont.Font(root, family=family, size=base, weight='bold'),
            'h2': tkfont.Font(root, family=family, size=base + 2, weight='bold'),
            'h1': tkfont.Font(root, family=family, size=base + 9, weight='bold'),
            'brand': tkfont.Font(root, family=family, size=base + 3, weight='bold'),
            'mono': tkfont.Font(root, family=mono, size=base - 1),
        }
        self.apply()

    def apply(self, dark=None):
        from tkinter import ttk
        if dark is not None:
            self.dark = dark
            self.c = dict(DARK if dark else LIGHT)
        c, f = self.c, self.f
        root = self.root
        root.configure(background=c['bg'])
        # the classic widgets that are used (Canvas, Text, Listbox of a combobox) take their colours from here
        for opt, val in (('*background', c['bg']), ('*foreground', c['text']), ('*selectBackground', c['accent']),
                         ('*selectForeground', c['accent_text']), ('*TCombobox*Listbox.background', c['surface']),
                         ('*TCombobox*Listbox.foreground', c['text']), ('*TCombobox*Listbox.font', f['body']),
                         ('*Menu.background', c['surface']), ('*Menu.foreground', c['text']),
                         ('*Menu.activeBackground', c['accent']), ('*Menu.activeForeground', c['accent_text'])):
            root.option_add(opt, val)
        s = ttk.Style(root)
        s.theme_use('clam')
        s.configure('.', background=c['bg'], foreground=c['text'], font=f['body'], bordercolor=c['line'],
                    lightcolor=c['line'], darkcolor=c['line'], troughcolor=c['sunken'], focuscolor=c['accent'],
                    selectbackground=c['accent'], selectforeground=c['accent_text'], insertcolor=c['text'])
        for name, bg in (('TFrame', 'bg'), ('Card.TFrame', 'surface'), ('Side.TFrame', 'sunken'), ('Well.TFrame', 'sunken'),
                         ('Good.TFrame', 'good_soft'), ('Warn.TFrame', 'warn_soft'), ('Bad.TFrame', 'bad_soft'),
                         ('Accent.TFrame', 'accent_soft')):
            s.configure(name, background=c[bg], borderwidth=0)
        s.configure('Line.TFrame', background=c['line'])
        for surface, bg in (('', 'bg'), ('Card.', 'surface'), ('Side.', 'sunken'), ('Good.', 'good_soft'), ('Warn.', 'warn_soft'),
                            ('Bad.', 'bad_soft'), ('Accent.', 'accent_soft')):
            s.configure(surface + 'TLabel', background=c[bg], foreground=c['text'], font=f['body'])
            s.configure(surface + 'Muted.TLabel', background=c[bg], foreground=c['muted'], font=f['body'])
            s.configure(surface + 'Small.TLabel', background=c[bg], foreground=c['muted'], font=f['small'])
            s.configure(surface + 'Bold.TLabel', background=c[bg], foreground=c['text'], font=f['bold'])
            s.configure(surface + 'H2.TLabel', background=c[bg], foreground=c['text'], font=f['h2'])
            s.configure(surface + 'H1.TLabel', background=c[bg], foreground=c['text'], font=f['h1'])
            s.configure(surface + 'Mono.TLabel', background=c[bg], foreground=c['text'], font=f['mono'])
            for tone in ('good', 'warn', 'bad', 'accent'):
                s.configure(surface + tone.capitalize() + 'Text.TLabel', background=c[bg], foreground=c[tone], font=f['bold'])
            s.configure(surface + 'Link.TLabel', background=c[bg], foreground=c['accent'], font=f['body'])
        s.configure('Brand.TLabel', background=c['sunken'], foreground=c['text'], font=f['brand'])
        # buttons: flat, one filled kind for the action of a page
        s.configure('TButton', background=c['surface'], foreground=c['text'], bordercolor=c['line'], lightcolor=c['surface'],
                    darkcolor=c['surface'], padding=(S4, S2), relief='raised', borderwidth=1, focusthickness=1, font=f['body'])
        s.map('TButton', background=[('disabled', c['bg']), ('pressed', c['sunken']), ('active', c['sunken'])],
              foreground=[('disabled', c['faint'])], bordercolor=[('focus', c['accent'])],
              lightcolor=[('pressed', c['sunken']), ('active', c['sunken'])], darkcolor=[('pressed', c['sunken']), ('active', c['sunken'])])
        s.configure('Accent.TButton', background=c['accent'], foreground=c['accent_text'], bordercolor=c['accent'],
                    lightcolor=c['accent'], darkcolor=c['accent'], font=f['bold'], padding=(S5, S2))
        s.map('Accent.TButton', background=[('disabled', c['line']), ('pressed', c['text']), ('active', c['text'])],
              foreground=[('disabled', c['faint']), ('pressed', c['bg']), ('active', c['bg'])],
              bordercolor=[('disabled', c['line']), ('focus', c['text'])],
              lightcolor=[('disabled', c['line']), ('active', c['text'])], darkcolor=[('disabled', c['line']), ('active', c['text'])])
        s.configure('Danger.TButton', background=c['surface'], foreground=c['bad'], bordercolor=c['bad'], lightcolor=c['surface'],
                    darkcolor=c['surface'], padding=(S4, S2), relief='raised')
        s.map('Danger.TButton', background=[('disabled', c['bg']), ('active', c['bad_soft'])], foreground=[('disabled', c['faint'])],
              bordercolor=[('disabled', c['line'])], lightcolor=[('active', c['bad_soft'])], darkcolor=[('active', c['bad_soft'])])
        s.configure('Nav.TButton', background=c['sunken'], foreground=c['muted'], bordercolor=c['sunken'], lightcolor=c['sunken'],
                    darkcolor=c['sunken'], anchor='w', padding=(S4, S3), font=f['body'], relief='flat')
        s.map('Nav.TButton', background=[('active', c['line'])], foreground=[('active', c['text'])],
              lightcolor=[('active', c['line'])], darkcolor=[('active', c['line'])], bordercolor=[('focus', c['accent'])])
        s.configure('NavOn.TButton', background=c['surface'], foreground=c['text'], bordercolor=c['surface'], lightcolor=c['surface'],
                    darkcolor=c['surface'], anchor='w', padding=(S4, S3), font=f['bold'], relief='flat')
        s.map('NavOn.TButton', background=[('active', c['surface'])], bordercolor=[('focus', c['accent'])])
        s.configure('Link.TButton', background=c['bg'], foreground=c['accent'], bordercolor=c['bg'], lightcolor=c['bg'],
                    darkcolor=c['bg'], padding=(0, 0), font=f['body'], relief='flat')
        s.map('Link.TButton', foreground=[('active', c['text'])], background=[('active', c['bg'])])
        s.configure('CardLink.TButton', background=c['surface'], foreground=c['accent'], bordercolor=c['surface'],
                    lightcolor=c['surface'], darkcolor=c['surface'], padding=(0, 0), font=f['body'], relief='flat')
        s.map('CardLink.TButton', foreground=[('active', c['text'])], background=[('active', c['surface'])])
        # choices
        for kind in ('TCheckbutton', 'TRadiobutton'):
            for surface, bg in (('', 'bg'), ('Card.', 'surface'), ('Accent.', 'accent_soft'), ('Warn.', 'warn_soft')):
                s.configure(surface + kind, background=c[bg], foreground=c['text'], indicatorcolor=c['surface'],
                            indicatorbackground=c['surface'], upperbordercolor=c['faint'], lowerbordercolor=c['faint'],
                            padding=(0, S1), font=f['body'])
                s.map(surface + kind, background=[('active', c[bg])], foreground=[('disabled', c['faint'])],
                      indicatorcolor=[('disabled', c['sunken']), ('selected', c['accent'])],
                      indicatorbackground=[('disabled', c['sunken']), ('selected', c['accent'])],
                      upperbordercolor=[('selected', c['accent'])], lowerbordercolor=[('selected', c['accent'])])
        s.configure('TEntry', fieldbackground=c['surface'], foreground=c['text'], bordercolor=c['line'], lightcolor=c['surface'],
                    darkcolor=c['surface'], padding=(S2, S1 + 2), insertcolor=c['text'])
        s.map('TEntry', bordercolor=[('focus', c['accent'])], lightcolor=[('focus', c['accent'])],
              fieldbackground=[('disabled', c['bg'])], foreground=[('disabled', c['faint'])])
        s.configure('TCombobox', fieldbackground=c['surface'], background=c['surface'], foreground=c['text'], arrowcolor=c['muted'],
                    bordercolor=c['line'], lightcolor=c['surface'], darkcolor=c['surface'], padding=(S2, S1 + 2))
        s.map('TCombobox', fieldbackground=[('readonly', c['surface']), ('disabled', c['bg'])], foreground=[('disabled', c['faint'])],
              bordercolor=[('focus', c['accent'])], selectbackground=[('readonly', c['surface'])],
              selectforeground=[('readonly', c['text'])], background=[('active', c['sunken'])])
        s.configure('TSpinbox', fieldbackground=c['surface'], background=c['surface'], foreground=c['text'], arrowcolor=c['muted'],
                    bordercolor=c['line'], lightcolor=c['surface'], darkcolor=c['surface'], padding=(S2, S1 + 2))
        s.configure('Horizontal.TProgressbar', background=c['accent'], troughcolor=c['sunken'], bordercolor=c['sunken'],
                    lightcolor=c['accent'], darkcolor=c['accent'], thickness=8)
        s.configure('Thin.Horizontal.TProgressbar', background=c['accent'], troughcolor=c['sunken'], bordercolor=c['sunken'],
                    lightcolor=c['accent'], darkcolor=c['accent'], thickness=4)
        # a slim bar without arrows: the thumb alone
        s.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        s.configure('Vertical.TScrollbar', background=c['line'], troughcolor=c['bg'], bordercolor=c['bg'], lightcolor=c['line'],
                    darkcolor=c['line'], gripcount=0, width=10, arrowsize=10)
        s.map('Vertical.TScrollbar', background=[('disabled', c['bg']), ('active', c['faint'])],
              lightcolor=[('disabled', c['bg']), ('active', c['faint'])], darkcolor=[('disabled', c['bg']), ('active', c['faint'])])
        s.configure('TSeparator', background=c['line'])
        s.configure('Horizontal.TScale', background=c['surface'], troughcolor=c['sunken'], bordercolor=c['line'],
                    lightcolor=c['accent'], darkcolor=c['accent'])
