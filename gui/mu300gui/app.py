"""The window. A sidebar (Install, Manage, Help; the device; the language), and beside it either the wizard of an
installation - Device, Storage, What to install, Backup, Install, Finish - or one of the two other pages.

Nothing here waits: the controller's threads do the slow things, and tick() takes in what they report ten times a
second. The pages are rebuilt from the controller's state whenever it changes in a way that concerns them, so there
is one place that says what a state looks like; only a run in progress is updated in place.
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import ISSUES, RELEASES, SITE, TELEGRAM, TOP, WIKI, desktop
from . import adb as adbmod
from .controller import Controller
from .i18n import LANGUAGES, N_, _, language, os_language, set_language
from .masking import short_serial
from .model import GIB, MIB, connect, download_plan, duration, layout, make_room, size, summary, work_need
from .theme import S1, S2, S3, S4, S5, S6, Theme, dpi_aware, system_dark
from .widgets import DiskBar, Scroll, StepList, Stepper, banner, card, facts, link, wrap_to

WIZARD = (('device', N_('Device')), ('storage', N_('Storage')), ('choose', N_('What to install')), ('backup', N_('Backup')),
          ('install', N_('Install')), ('done', N_('Finish')))
SECTIONS = (('install', N_('Install')), ('manage', N_('Manage')), ('help', N_('Help')))
MODELS = {'f50': 'ZTE F50 / MU300', 'u30air': 'ZTE U30 Air', 'v50': 'ZTE V50'}
PLATFORM_TOOLS = 'https://developer.android.com/tools/releases/platform-tools'


class App:
    def __init__(self, root, ctl, theme):
        self.root, self.ctl, self.theme = root, ctl, theme
        self.section = 'install'
        self.step = 0
        self.reached = 0
        self.show_serial = False
        self.advanced = False
        self.log_open = False
        self.log_seen = 0
        self.auto_checked = None
        self.dialog = None
        self._show_result = False       # the Install page shows the last run, not the choices
        self._pending = None
        self.status = ''                # one line under the page: what the last action did
        self.w = {}                     # widgets of the page that is shown, by name
        self.v = self._vars()
        ctl.listen(self.on_change)
        root.title(_('MU300 Linux installer'))
        root.protocol('WM_DELETE_WINDOW', self.quit)
        root.bind('<F5>', lambda e: self.refresh_devices())
        root.bind('<Control-q>', lambda e: self.quit())
        self.build()
        self.tick()

    # ---- the choices as variables of the widgets
    def _vars(self):
        c = self.ctl.choices
        v = {}
        spec = (('ubuntu', tk.BooleanVar, True), ('openwrt', tk.BooleanVar, True), ('ubuntu_version', tk.StringVar, True),
                ('openwrt_flavor', tk.StringVar, False), ('boot_os', tk.StringVar, False), ('kernel', tk.StringVar, False),
                ('release', tk.StringVar, False), ('release_url', tk.StringVar, False), ('default_linux', tk.BooleanVar, True),
                ('boot_attempts', tk.StringVar, False), ('hotspot', tk.BooleanVar, False), ('gpu', tk.BooleanVar, False),
                ('vpn', tk.BooleanVar, False), ('adb', tk.BooleanVar, False), ('panel', tk.BooleanVar, False),
                ('storage', tk.StringVar, True), ('existing', tk.StringVar, True), ('password', tk.StringVar, False),
                ('password2', tk.StringVar, False), ('keep_password', tk.BooleanVar, True),
                ('current_password', tk.StringVar, False), ('overwrite_word', tk.StringVar, False), ('erase_sd_word', tk.StringVar, False),
                ('internal_word', tk.StringVar, False), ('unknown_model', tk.BooleanVar, False), ('sync_bootchain', tk.BooleanVar, False),
                ('fetch_jobs', tk.StringVar, False), ('vpn_url', tk.StringVar, False), ('vpn_module', tk.StringVar, False))
        for name, kind, rebuild in spec:
            var = kind(self.root, value=getattr(c, name))
            var.trace_add('write', lambda *_a, n=name, r=rebuild: self._choice(n, r))
            v[name] = var
        # what is typed on a page but is no choice of an installation
        v['room_gib'] = tk.StringVar(self.root, value='4')
        v['room_word'] = tk.StringVar(self.root, value='')
        v['release_mode'] = tk.StringVar(self.root, value='latest')     # latest | beta | tag
        v['release_tag'] = tk.StringVar(self.root, value='')
        for name in ('release_mode', 'release_tag'):
            v[name].trace_add('write', lambda *_a: self._release())
        return v

    def _choice(self, name, rebuild):
        c = self.ctl.choices
        try:
            value = self.v[name].get()
        except tk.TclError:
            return
        if name in ('boot_attempts', 'fetch_jobs'):
            try:
                value = int(value)
            except ValueError:
                value = 0
        setattr(c, name, value)
        if rebuild:
            self.later(self.render)
        else:
            self.validate()

    def _sync(self):
        """The choices changed behind the widgets (a check fitted them to the device): the variables follow."""
        c = self.ctl.choices
        for name, var in self.v.items():
            if not hasattr(c, name):
                continue
            value = getattr(c, name)
            value = str(value) if isinstance(var, tk.StringVar) else value
            if var.get() != value:
                var.set(value)

    def _release(self):
        mode, rel = self.v['release_mode'].get(), self.ctl.releases
        tag = {'latest': '', 'beta': rel.get('beta') or '', 'tag': self.v['release_tag'].get().strip()}[mode]
        if self.v['release'].get() != tag:
            self.v['release'].set(tag)
        self.validate()

    def later(self, fn):
        """Once, when the event that asked for it is over (a variable's trace must not rebuild its own widget)."""
        if self._pending is None:
            def run():
                self._pending = None
                fn()
            self._pending = self.root.after_idle(run)

    # ---- the frame of the window
    def build(self):
        for child in self.root.winfo_children():
            child.destroy()
        self.dialog = None
        self.root.title(_('MU300 Linux installer'))
        c = self.theme.c
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        side = ttk.Frame(self.root, style='Side.TFrame', padding=(S3, S5, S3, S4), width=228)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        ttk.Label(side, text='MU300 Linux', style='Brand.TLabel').pack(anchor='w', padx=S2)
        ttk.Label(side, text=_('Ubuntu and OpenWrt for the ZTE F50 and U30 Air'), style='Side.Small.TLabel', wraplength=190,
                  justify='left').pack(anchor='w', padx=S2, pady=(S1, S5))
        self.nav = {}
        for sid, label in SECTIONS:
            b = ttk.Button(side, text=_(label), style='Nav.TButton', command=lambda s=sid: self.goto(s))
            b.pack(fill='x', pady=1)
            self.nav[sid] = b
        ttk.Frame(side, style='Side.TFrame').pack(fill='both', expand=True)
        # the device, always in sight
        chip = ttk.Frame(side, style='Card.TFrame', padding=(S3, S3))
        chip.pack(fill='x', pady=(0, S3))
        self.chip_dot = tk.Canvas(chip, width=10, height=10, highlightthickness=0, background=c['surface'])
        self.chip_dot.grid(row=0, column=0, padx=(0, S2), pady=(4, 0), sticky='n')
        self.chip_title = ttk.Label(chip, text='', style='Card.Bold.TLabel', wraplength=160, justify='left')
        self.chip_title.grid(row=0, column=1, sticky='w')
        self.chip_text = ttk.Label(chip, text='', style='Card.Small.TLabel', wraplength=160, justify='left')
        self.chip_text.grid(row=1, column=1, sticky='w')
        chip.columnconfigure(1, weight=1)
        ttk.Label(side, text=_('Language'), style='Side.Small.TLabel').pack(anchor='w', padx=S2)
        self.lang_box = ttk.Combobox(side, state='readonly', values=[name for _code, name in LANGUAGES])
        self.lang_box.set(dict(LANGUAGES)[language()])
        self.lang_box.bind('<<ComboboxSelected>>', self._language)
        self.lang_box.pack(fill='x', padx=S2, pady=(S1, 0))

        main = ttk.Frame(self.root, padding=(S6, S5, S6, S4))
        main.grid(row=0, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self.title = ttk.Label(main, text='', style='H1.TLabel')
        self.title.grid(row=0, column=0, sticky='w')
        self.head = ttk.Frame(main)
        self.head.grid(row=1, column=0, sticky='ew', pady=(S3, 0))
        self.head.columnconfigure(0, weight=1)
        self.scroll = Scroll(main, self.theme)
        self.scroll.grid(row=2, column=0, sticky='nsew', pady=(S3, 0))
        self.foot = ttk.Frame(main)
        self.foot.grid(row=3, column=0, sticky='ew', pady=(S3, 0))
        self.foot.columnconfigure(1, weight=1)
        self.render()

    def _language(self, _event=None):
        name = self.lang_box.get()
        for code, label in LANGUAGES:
            if label == name and code != language():
                set_language(code)
                self.build()

    def goto(self, section, step=None):
        self.section = section
        if step is not None:
            self.step = step
            self.reached = max(self.reached, step)
        self.status = ''
        self.render()
        self.scroll.top()

    def render(self):
        """Build the page that is shown from the controller's state."""
        keep = self.scroll.canvas.yview()[0] if self.scroll.winfo_exists() else 0
        for area in (self.scroll.body, self.head, self.foot):
            for child in area.winfo_children():
                child.destroy()
        self.w = {}
        self.log_seen = 0
        for sid, b in self.nav.items():
            b.configure(style='NavOn.TButton' if sid == self.section else 'Nav.TButton')
        body = self.scroll.body
        body.columnconfigure(0, weight=1)
        if self.section == 'install':
            self.head.grid()
        else:
            self.head.grid_remove()
        if self.section == 'install':
            sid = WIZARD[self.step][0]
            self.title.configure(text=_(WIZARD[self.step][1]))
            stepper = Stepper(self.head, self.theme, [(s, _(label)) for s, label in WIZARD], self._pick_step)
            stepper.grid(row=0, column=0, sticky='ew')
            stepper.set(self.step, self.reached)
            getattr(self, 'page_' + sid)(body)
            self._footer()
        elif self.section == 'manage':
            self.title.configure(text=_('Manage'))
            self.page_manage(body)
        else:
            self.title.configure(text=_('Help'))
            self.page_help(body)
        self._chip()
        self.validate()
        self.root.update_idletasks()
        self.scroll.canvas.yview_moveto(keep)

    def _pick_step(self, i):
        if i <= self.reached and not (self.ctl.busy() and self.ctl.run_kind in ('install', 'makeroom')):
            self.goto('install', i)

    def _footer(self):
        sid = WIZARD[self.step][0]
        running = self.ctl.busy() and self.ctl.run_kind in ('install', 'makeroom')
        if self.step > 0 and sid != 'done' and not running:
            self.w['back'] = ttk.Button(self.foot, text=_('Back'), command=lambda: self.goto('install', self.step - 1))
            self.w['back'].grid(row=0, column=0, sticky='w')
        self.w['status'] = ttk.Label(self.foot, text=self.status, style='Muted.TLabel')
        self.w['status'].grid(row=0, column=1, sticky='w', padx=S4)
        if sid in ('device', 'storage', 'choose', 'backup'):
            label = {'backup': _('Continue to the installation')}.get(sid, _('Continue'))
            self.w['next'] = ttk.Button(self.foot, text=label, style='Accent.TButton', command=lambda: self.goto('install', self.step + 1))
            self.w['next'].grid(row=0, column=2, sticky='e')

    def say(self, text):
        self.status = text
        if 'status' in self.w and self.w['status'].winfo_exists():
            self.w['status'].configure(text=text)

    def validate(self):
        """What the choices allow right now: the Continue button, and the list of what is still missing."""
        if self.section != 'install':
            return
        sid = WIZARD[self.step][0]
        probs = self.ctl.problems()
        if 'next' in self.w:
            if sid == 'device':
                ok = self.ctl.device_state() in ('android', 'other') and self.ctl.check_state == 'ok' and not [p for p in probs if p[0] == 'device']
            elif sid == 'storage':
                ok = not [p for p in probs if p[0] in ('device', 'storage')]
            elif sid == 'choose':
                ok = not probs
            else:
                ok = True
            self.w['next'].state(['!disabled'] if ok else ['disabled'])
        if 'problems' in self.w:
            wanted = {'storage': ('storage',), 'choose': ('systems', 'kernel', 'boot', 'password')}.get(sid, ())
            texts = [m for f, m in probs if f in wanted]
            self.w['problems'].configure(text='\n'.join('• ' + t for t in texts))
        if 'start' in self.w:
            self.w['start'].state(['disabled'] if (probs or self.ctl.busy()) else ['!disabled'])

    # ---- the sidebar's device chip
    def _chip(self):
        c = self.theme.c
        state = self.ctl.device_state()
        d = self.ctl.device
        tone, title, text = 'faint', _('No device'), _('Connect it by USB')
        if state == 'no-adb':
            tone, title, text = 'bad', _('adb not found'), _('Android platform-tools are needed')
        elif state == 'choose':
            tone, title, text = 'warn', _('{1} adb devices', len(self.ctl.devices)), _('Choose the F50 or U30 Air')
        elif state == 'unauthorized':
            tone, title, text = 'warn', _('Not authorised'), _('Allow USB debugging on the device')
        elif state == 'offline':
            tone, title, text = 'warn', _('Not ready'), d.state if d else ''
        elif state == 'linux':
            tone, title, text = 'accent', MODELS.get(self.ctl.kind(), _('MU300 Linux')), _('running Linux')
        elif state == 'linux-ssh':
            tone, title, text = 'accent', _('MU300 Linux'), _('running Linux at {1}', self.ctl.linux_ip)
        elif state == 'other':
            tone, title, text = 'warn', (d.attrs.get('model') or d.serial) if d else '', _('not an F50 or U30 Air')
        elif state == 'android':
            ok = self.ctl.check_state == 'ok'
            tone = 'good' if ok else ('bad' if self.ctl.check_state == 'failed' else 'accent')
            title = MODELS.get(self.ctl.kind(), d.attrs.get('model', '') if d else '')
            text = {'ok': _('in Android, checked'), 'failed': _('in Android, check failed'), 'running': _('checking…')}.get(
                self.ctl.check_state, _('in Android'))
        self.chip_dot.delete('all')
        self.chip_dot.create_oval(1, 1, 9, 9, fill=c[tone], outline=c[tone])
        self.chip_title.configure(text=title)
        self.chip_text.configure(text=text)

    # ---- taking in what changed
    def tick(self):
        try:
            self.ctl.pump()
            if self.ctl.busy() and self.ctl.run_kind in ('install', 'makeroom', 'uninstall', 'command', 'backup'):
                self._run_update(pulse=True)
        finally:
            self._after = self.root.after(120, self.tick)

    def on_change(self, topics):
        if 'check' in topics:
            self._sync()
        if 'devices' in topics:
            self._chip()
            self._auto_check()
        sid = WIZARD[self.step][0] if self.section == 'install' else self.section
        if 'confirm' in topics:
            self._confirm()
        if 'run' in topics or 'log' in topics:
            finished = self.ctl.progress is not None and self.ctl.progress.finished
            if finished and self.ctl.run is None:
                self._run_finished()
                return
            self._run_update()
        if ('devices' in topics and sid in ('device', 'manage')) or ('check' in topics and sid in ('device', 'storage', 'choose', 'backup', 'manage')) \
                or ('releases' in topics and sid in ('choose', 'backup', 'install')) or ('linux' in topics and sid == 'manage'):
            if not self._typing():
                self.render()

    def _typing(self):
        """Whether the user is in the middle of typing into a field of this page: a rebuild would take the field
        away under their hands. (The page is rebuilt when they leave it.)"""
        try:
            return isinstance(self.root.focus_get(), (ttk.Entry, tk.Entry)) and self.dialog is None
        except (KeyError, tk.TclError):
            return False

    def _auto_check(self):
        """A device that just appeared in Android is looked at once, without a click."""
        if self.ctl.device_state() == 'android' and self.ctl.check_state == 'idle' and self.auto_checked != self.ctl.serial \
                and not self.ctl.busy():
            self.auto_checked = self.ctl.serial
            self.ctl.check()
        if self.ctl.device is None:
            self.auto_checked = None

    def refresh_devices(self):
        self.auto_checked = None
        if self.ctl.adb_path == '':
            self.ctl.adb_path = None
            self.ctl.start()
        elif not self.ctl.busy() and (self.ctl.device_state() == 'android' or
                                      (self.ctl.device_state() == 'other' and self.ctl.choices.unknown_model)):
            self.auto_checked = self.ctl.serial
            self.ctl.check()
        self.render()

    def quit(self):
        if self.ctl.busy() and self.ctl.progress is not None and not self.ctl.progress.can_cancel():
            messagebox.showwarning(_('MU300 Linux installer'),
                                   _('The installer is writing to the device. Wait until it has finished: closing now could leave a write half done.'),
                                   parent=self.root)
            return
        if self.ctl.busy() and not messagebox.askyesno(_('MU300 Linux installer'), _('A run is in progress. Stop it and close?'), parent=self.root):
            return
        self.ctl.close()
        try:
            self.root.after_cancel(self._after)
        except (AttributeError, tk.TclError):
            pass
        self.root.destroy()

    # ================================================================ Device
    def page_device(self, body):
        ctl = self.ctl
        state = ctl.device_state()
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        # which device
        outer, inner = card(body, _('The device'), _('Plug the device into this computer with a USB cable, booted into Android with USB debugging on.'))
        add(outer)
        line = ttk.Frame(inner, style='Card.TFrame')
        line.pack(fill='x')
        names = [self._device_label(d) for d in ctl.devices]
        box = ttk.Combobox(line, state='readonly', values=names, width=40)
        if ctl.device is not None:
            box.set(self._device_label(ctl.device))
        elif names:
            box.set(_('Choose a device'))
        else:
            box.set(_('No adb device'))
        box.bind('<<ComboboxSelected>>', lambda e: ctl.select(ctl.devices[box.current()].serial) if box.current() >= 0 else None)
        box.pack(side='left')
        ttk.Button(line, text=_('Refresh'), command=self.refresh_devices).pack(side='left', padx=(S2, 0))
        ttk.Checkbutton(inner, text=_('Show serial numbers'), style='Card.TCheckbutton', variable=self._flag('show_serial')).pack(anchor='w', pady=(S2, 0))

        if state == 'no-adb':
            add(banner(body, _('adb was not found on this computer. It is part of Android\'s platform-tools: unzip them next to this project (a folder called platform-tools) or put them on PATH, then press Refresh.'), 'bad'))
            add(link(body, PLATFORM_TOOLS, lambda: desktop.open_url(PLATFORM_TOOLS)))
            return
        if state == 'none':
            add(banner(body, _('No device answers on adb yet.'), 'warn'))
            o, i = card(body, _('What to check'))
            add(o)
            for text in (_('The device is switched on and has finished starting Android.'),
                         _('USB debugging is switched on in Android\'s developer options.'),
                         _('The cable carries data (some only charge), and it is plugged into this computer directly.'),
                         _('On Windows the device needs its adb driver; on Linux your user may need a udev rule for it.')):
                ttk.Label(i, text='•  ' + text, style='Card.TLabel', wraplength=660, justify='left').pack(anchor='w', pady=1)
            return
        if state == 'choose':
            add(banner(body, _('More than one adb device is attached, or none of them says it is an F50 or U30 Air. Choose the device above: the installer never picks one by itself, so that it cannot write to a phone or tablet next to it.'), 'warn'))
            return
        if state == 'unauthorized':
            add(banner(body, _('The device has not authorised this computer for USB debugging. Confirm "Allow USB debugging?" on the device, then press Refresh. If no question appears there: unplug the cable and plug it in again, or use "Revoke USB debugging authorisations" in the developer options and try once more.'), 'warn'))
            return
        if state == 'offline':
            add(banner(body, _('adb sees the device but cannot talk to it ({1}). Unplug the cable, plug it in again and press Refresh.', ctl.device.state), 'warn'))
            return
        if state in ('linux', 'linux-ssh'):
            add(banner(body, _('This device is running MU300 Linux. Installing, updating from a computer and uninstalling happen from Android: send it back to Android on the Manage page, then come back here.'), 'accent'))
            add(ttk.Button(body, text=_('Open the Manage page'), style='Accent.TButton', command=lambda: self.goto('manage')), S4)
            return

        f, d = ctl.facts, ctl.device
        p = ctl.props
        model = f.get('device.model') or ' / '.join(x for x in (p.get('ro.product.model', d.attrs.get('model', '')), p.get('ro.product.device', d.attrs.get('device', ''))) if x)
        kind = ctl.kind()
        rows = [(_('Model'), MODELS.get(kind, _('not recognised')) + ('   (' + model + ')' if model else ''), '' if kind else 'warn'),
                (_('Android build'), f.get('android.build') or p.get('ro.build.display.id', '') or '—'),
                (_('Serial number'), d.serial if self.show_serial else short_serial(d.serial)),
                (_('Runs'), _('Android (slot {1}); Linux goes to slot {2}', f['slot.android'], f['slot.linux']) if 'slot.android' in f else _('Android')),
                (_('USB debugging'), _('authorised for this computer'), 'good')]
        o, i = card(body, _('What it is'))
        add(o)
        facts(i, rows).pack(fill='x')

        if state == 'other' and not self.v['unknown_model'].get():
            add(banner(body, _('This does not look like a ZTE F50/MU300 or U30 Air. The installer is made for those two; on anything else it can do damage. (Advanced, on the "What to install" page: continue anyway.)'), 'bad'))

        # root and the bootloader, each for itself
        checking = ctl.check_state == 'running'
        root = f.root
        o, i = card(body, _('Root'))
        add(o)
        if root is True:
            facts(i, [(_('Root (su)'), _('works'), 'good'), (_('Provided by'), f.get('root.su') or '—')]).pack(fill='x')
        elif root is False:
            facts(i, [(_('Root (su)'), _('does not work'), 'bad')]).pack(fill='x')
            banner(i, _('Without root nothing can be installed: the installer reads the partition table, copies the device\'s own Wi-Fi and modem files, and writes the Linux filesystem and the boot image of the unused slot - all of that through su. If the device is rooted, open its root manager and allow "shell" (adb), then press Refresh.'), 'bad').pack(fill='x', pady=(S2, 0))
        else:
            facts(i, [(_('Root (su)'), _('checking…') if checking else _('not checked yet'))]).pack(fill='x')

        boot = f.bootloader if (f or checking) else ''
        if not f and p:
            from .model import Facts
            boot = Facts({'bootloader.locked': p.get('ro.boot.flash.locked', ''),
                          'bootloader.device_state': p.get('ro.boot.vbmeta.device_state', '')}).bootloader
        o, i = card(body, _('Bootloader'))
        add(o)
        verified = f.get('bootloader.verified') or p.get('ro.boot.verifiedbootstate', '')
        # locked is a warning and never a refusal; unlocked is simply said; a device that does not report is no fault
        label = {'unlocked': (_('unlocked'), ''), 'locked': (_('locked'), 'warn'), 'unknown': (_('not reported'), '')}.get(boot, ('—', ''))
        facts(i, [(_('Bootloader'), label[0], label[1]), (_('Verified boot state'), verified or '—')]).pack(fill='x')
        if boot == 'locked':
            banner(i, _('The bootloader is locked. Did you patch TrustOS? With a patched TrustOS Linux boots on a locked bootloader; without it the device goes back to Android by itself after the install.'), 'warn').pack(fill='x', pady=(S2, 0))
        note = ttk.Label(body, text=_('This project does not root, unlock or patch devices and gives no help with it: the device has to come that way.'),
                         style='Muted.TLabel', wraplength=700, justify='left')
        add(note)

        if ctl.check_state == 'failed' and root is not False:
            add(banner(body, _('The check stopped: {1}', ctl.check_error or '?'), 'bad'))
        elif ctl.check_state == 'ok':
            add(banner(body, _('Checked: {1}', f.get('verdict', '')), 'good' if f.get('verdict.code') != 'dirty' else 'warn'))
        line = ttk.Frame(body)
        add(line)
        b = ttk.Button(line, text=_('Check again') if ctl.check_state in ('ok', 'failed') else _('Check the device'), command=self.refresh_devices)
        b.pack(side='left')
        if checking:
            b.state(['disabled'])
            bar = ttk.Progressbar(line, mode='indeterminate', length=160, style='Thin.Horizontal.TProgressbar')
            bar.pack(side='left', padx=S3)
            bar.start(40)
        ttk.Label(line, text=_('The check only reads: nothing is written to the device.'), style='Muted.TLabel').pack(side='left', padx=S3)

    def _device_label(self, d):
        serial = d.serial if self.show_serial else short_serial(d.serial)
        what = _('MU300 Linux') if d.linux else (d.attrs.get('model') or d.attrs.get('product') or '')
        state = '' if d.usable else '  [%s]' % d.state
        return '%s   %s%s' % (what or '?', serial, state)

    def _flag(self, name):
        """A BooleanVar for one of the window's own switches (not a choice of the installation); toggling rebuilds."""
        var = tk.BooleanVar(self.root, value=getattr(self, name))

        def changed(*_a):
            setattr(self, name, var.get())
            self.later(self.render)
        var.trace_add('write', changed)
        self.w['flag-' + name] = var        # keep it alive as long as the page
        return var

    # ================================================================ Storage
    def page_storage(self, body):
        ctl, f, c = self.ctl, self.ctl.facts, self.theme.c
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        if not f.checked:
            add(banner(body, _('The device has not been checked yet. Go back to "Device".'), 'warn'))
            return
        o, i = card(body, _('Internal storage (eMMC)'), _('Linux lives in space that belongs to no partition. Android, its data and the partition table stay as they are.'))
        add(o)
        bar = DiskBar(i, self.theme)
        bar.pack(fill='x')
        segs = layout(f)
        bar.set(segs)
        legend = ttk.Frame(i, style='Card.TFrame')
        legend.pack(fill='x', pady=(S2, S3))
        col = 0
        for s in segs:
            if not s['label']:
                continue
            sw = tk.Canvas(legend, width=12, height=12, highlightthickness=0, background=c['surface'])
            sw.create_rectangle(0, 0, 12, 12, fill=c[DiskBar.COLOURS[s['kind']]], outline=c['accent'] if s['kind'] == 'free' else c['line'])
            sw.grid(row=col, column=0, padx=(0, S2), pady=1, sticky='w')
            ttk.Label(legend, text=_(s['label']), style='Card.TLabel').grid(row=col, column=1, sticky='w', pady=1)
            ttk.Label(legend, text=size(s['bytes']), style='Card.Muted.TLabel').grid(row=col, column=2, sticky='e', padx=(S4, 0), pady=1)
            col += 1
        disk = f.num('emmc.sectors') * 512
        rows = [(_('eMMC'), '%s  (%s)' % (size(disk), _('{1} sectors', f.num('emmc.sectors')))),
                (_('Partitions end at'), '%s  (%s)' % (size(f.num('emmc.last_end') * 512), _('sector {1}', f.num('emmc.last_end')))),
                (_('Free after them'), size(f.num('region.size')) if not f.existing else _('{1}, used by MU300 Linux', size(f.num('region.size'))),
                 'good' if f.num('region.size') >= 700 * MIB else 'bad')]
        if 'region.dirty' in f:
            dirty = f.num('region.dirty')
            rows.append((_('Is it empty?'), _('yes: 16 of 16 samples are empty') if dirty == 0 else _('no: {1} of 16 samples hold data', dirty), 'good' if dirty == 0 else 'warn'))
        if f.get('bootchain'):
            rows.append((_('Boot chain'), f['bootchain'].split(':', 1)[-1].strip()))
        facts(i, rows).pack(fill='x')
        if f.get('verdict'):
            banner(i, f['verdict'], 'warn' if f.get('verdict.code') == 'dirty' else 'good').pack(fill='x', pady=(S3, 0))
        # a prepared installation (making room by a reset, tools/resume.sh): what its checks said
        if f.get('resume.transaction'):
            if f.get('resume.check') == 'RESUME-OK':
                banner(i, _('A prepared installation waits for this device (EXPERIMENTAL: room was made by resetting Android). Install continues it into the prepared region. Its record: {1}', f['resume.transaction']), 'accent').pack(fill='x', pady=(S3, 0))
            else:
                banner(i, _('A prepared installation waits for this device, but it cannot be continued: {1}. Nothing is written. Its record: {2}',
                            f.get('resume.check', '').replace('RESUME-REFUSED: ', ''), f['resume.transaction']), 'bad').pack(fill='x', pady=(S3, 0))

        sd = f.sd
        o, i = card(body, _('SD card'))
        add(o)
        if sd:
            what = {'yes': (_('holds a MU300 Linux installation (mu300sd)'), 'accent'), 'foreign': (_('holds another Linux (ext4) filesystem: never formatted'), 'bad'),
                    'no': (_('no Linux filesystem on it'), '')}[sd[2]]
            facts(i, [(_('Card'), '%s  (%s)' % (size(sd[1]), sd[0])), (_('On it'), what[0], what[1])]).pack(fill='x')
        elif f.get('sd.small') == '1':
            ttk.Label(i, text=_('A card is in the slot, but it is smaller than 700 MiB and is not used.'), style='Card.Muted.TLabel').pack(anchor='w')
        else:
            ttk.Label(i, text=_('No SD card in the device (the U30 Air has no slot). Put a card in while the device is switched off.') if f.kind != 'u30air'
                      else _('The U30 Air has no SD card slot.'), style='Card.Muted.TLabel', wraplength=660, justify='left').pack(anchor='w')

        room = make_room(f)
        if room is not None:
            self._make_room(body, add, room)
            return
        if f.get('verdict.code') == 'sd-only' and not sd:
            return

        o, i = card(body, _('Where should Linux go?'))
        add(o)
        internal_ok = f.num('region.size') >= 700 * MIB or f.existing
        r1 = ttk.Radiobutton(i, text=_('Internal storage: {1}', size(f.num('region.size'))), value='internal', variable=self.v['storage'], style='Card.TRadiobutton')
        r1.pack(anchor='w')
        if not internal_ok:
            r1.state(['disabled'])
        r2 = ttk.Radiobutton(i, text=_('SD card: {1}', size(sd[1])) if sd else _('SD card: none'), value='sd', variable=self.v['storage'], style='Card.TRadiobutton')
        r2.pack(anchor='w')
        if not sd or sd[2] == 'foreign':
            r2.state(['disabled'])
        ch = ctl.choices
        if ch.storage == 'sd' and sd:
            ttk.Label(i, text=_('A card with a MU300 Linux filesystem always starts first. The boot image starts any card labelled mu300sd, so whoever holds the device can start a system of their own from a card.'),
                      style='Card.Muted.TLabel', wraplength=660, justify='left').pack(anchor='w', pady=(S2, 0))
            if ch.formats(f):
                self._word(i, _('The card will be formatted and everything on it is erased. Type ERASE to allow that:'), 'erase_sd_word')
        elif ch.storage == 'internal':
            if sd and sd[2] == 'yes':
                self._word(i, _('The card holds an installation, which the device starts first: an installation to internal storage does not start while that card is in the slot. Type internal to install there anyway:'), 'internal_word')
            if f.num('region.dirty') > 0 and not f.existing:
                self._word(i, _('The free space is not empty: something may be using it. Installing overwrites it. Type overwrite to use it anyway:'), 'overwrite_word')
        self.w['problems'] = ttk.Label(body, text='', style='BadText.TLabel', wraplength=700, justify='left')
        add(self.w['problems'])

    def _word(self, parent, text, name):
        ttk.Label(parent, text=text, style='Card.TLabel', wraplength=660, justify='left').pack(anchor='w', pady=(S3, S1))
        ttk.Entry(parent, textvariable=self.v[name], width=14).pack(anchor='w')

    def _make_room(self, body, add, room):
        """userdata fills the disk: what making room would do, with its figures. It runs the installer's own
        make-room path, which wants ERASE typed."""
        o, i = card(body, _('No free space: making room (experimental)'), style='Warn')
        add(o)
        if not room['possible']:
            ttk.Label(i, text=_('userdata fills the disk, and there is not enough to share: {1} in all, of which Android needs at least {2}. Nothing can be changed here; an SD card is the other way (F50).',
                                size(room['total']), size(room['min_android'])), style='Warn.TLabel', wraplength=660, justify='left').pack(anchor='w')
            return
        ttk.Label(i, text=_('userdata fills the whole disk, so there is nowhere to put Linux. The installer can shrink userdata - only userdata, the last partition, so nothing else moves. This rewrites the partition table and ERASES everything in Android: apps, photos, accounts, settings. It is experimental.'),
                  style='Warn.TLabel', wraplength=660, justify='left').pack(anchor='w')
        facts(i, [(_('userdata now'), size(room['userdata'])), (_('To share'), size(room['total'])),
                  (_('Linux can get'), _('{1} to {2}', size(room['min']), size(room['max']))),
                  (_('Android keeps at least'), size(room['min_android']))], style='Warn').pack(fill='x', pady=S3)
        line = ttk.Frame(i, style='Warn.TFrame')
        line.pack(anchor='w')
        ttk.Label(line, text=_('GiB for Linux:'), style='Warn.TLabel').pack(side='left')
        if self.v['room_gib'].get() == '4' and room['suggest']:
            self.v['room_gib'].set(('%.1f' % (room['suggest'] / GIB)).rstrip('0').rstrip('.'))
        ttk.Entry(line, textvariable=self.v['room_gib'], width=8).pack(side='left', padx=S2)
        ttk.Label(line, text=_('Type ERASE:'), style='Warn.TLabel').pack(side='left', padx=(S4, 0))
        ttk.Entry(line, textvariable=self.v['room_word'], width=10).pack(side='left', padx=S2)
        ttk.Button(i, text=_('Make room now'), style='Danger.TButton', command=self._make_room_go).pack(anchor='w', pady=(S3, 0))
        ttk.Label(i, text=_('First the partition table, misc, boot and the boot chain are backed up to this computer, with a record of what is prepared. Then the device restarts into Android\'s own recovery, which resets Android - a factory reset, a few minutes and more than one restart. Set Android up again, turn USB debugging on and bring root back (install the Magisk app again, let Shell have root); then Refresh: the installation continues where it was prepared. EXPERIMENTAL (ported from kano\'s desktop installer).'),
                  style='Warn.Muted.TLabel', wraplength=660, justify='left').pack(anchor='w', pady=(S2, 0))

    def _make_room_go(self):
        try:
            gib = float(self.v['room_gib'].get().replace(',', '.'))
        except ValueError:
            messagebox.showerror(_('Making room'), _('Give a number of GiB, like 4 or 2.5.'), parent=self.root)
            return
        word = self.v['room_word'].get()
        self._show_result = True
        if word != 'ERASE':
            messagebox.showerror(_('Making room'), _('Type ERASE (in capitals) to allow it. Nothing was changed.'), parent=self.root)
            return
        if not messagebox.askyesno(_('Making room'), _('Shrink userdata to give Linux {1} GiB? Everything in Android is erased, and this cannot be undone.', self.v['room_gib'].get()),
                                   icon='warning', default='no', parent=self.root):
            return
        self.v['room_word'].set('')
        if self.ctl.make_room(gib, word):
            self.goto('install', [s for s, _l in WIZARD].index('install'))

    # ================================================================ What to install
    def page_choose(self, body):
        ctl, f, ch = self.ctl, self.ctl.facts, self.ctl.choices
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        def check(parent, text, name, note=None, enabled=True):
            b = ttk.Checkbutton(parent, text=text, variable=self.v[name], style='Card.TCheckbutton')
            b.pack(anchor='w')
            if not enabled:
                b.state(['disabled'])
            if note:
                ttk.Label(parent, text=note, style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', padx=(S5, 0), pady=(0, S2))
            return b

        def radio(parent, text, name, value, note=None, enabled=True, indent=0):
            b = ttk.Radiobutton(parent, text=text, variable=self.v[name], value=value, style='Card.TRadiobutton')
            b.pack(anchor='w', padx=(indent, 0))
            if not enabled:
                b.state(['disabled'])
            if note:
                ttk.Label(parent, text=note, style='Card.Small.TLabel', wraplength=620, justify='left').pack(anchor='w', padx=(indent + S5, 0), pady=(0, S2))
            return b

        room = ch.place_bytes(f)
        if ctl.package and ctl.package.get('error'):
            add(banner(body, _('{1} is not an unpacked Magisk zip of a release that this installer can read (its mu300/manifest is not valid). Unpack the zip again.', ctl.package['dir']), 'bad'))
        if ch.package:
            self._package_card(body, add, ch)
        else:
            self._choose_system_kernel_release(body, add, ctl, f, ch, room, check, radio)
        self._choose_rest(body, add, ctl, f, ch, check)

    def _package_card(self, body, add, ch):
        """An unpacked Magisk zip: what it installs, and nothing to choose about it."""
        pkg = ch.package
        names = {'openwrt': 'OpenWrt 25.12', 'openwrt-luci': _('OpenWrt with the MU300 control panel'),
                 'ubuntu-24.04': 'Ubuntu 24.04 LTS', 'ubuntu-26.04': 'Ubuntu 26.04 LTS'}
        o, i = card(body, _('This package'), _('This window was started from an unpacked Magisk zip: it installs the system and the kernel of that zip, from its own files, and downloads nothing. Another system or kernel: the zip of that combination.'))
        add(o)
        facts(i, [(_('System'), names.get(pkg['system'], pkg['system'])), (_('Kernel'), pkg['kernel']), (_('Release'), pkg['tag']),
                  (_('Folder'), pkg['dir']), (_('Files'), size(pkg.get('bytes', 0)))]).pack(fill='x')

    def _choose_system_kernel_release(self, body, add, ctl, f, ch, room, check, radio):
        o, i = card(body, _('Systems'), _('One or both. With both, mu300-os switches between them later.'))
        add(o)
        check(i, 'Ubuntu LTS', 'ubuntu', _('The full distribution with apt; about 500 MiB of RAM in use. Needs {1}.', size(f.need('ubuntu'))),
              enabled=not room or room >= f.need('ubuntu'))
        radio(i, _('24.04 LTS: the longest tested, supported until 2029'), 'ubuntu_version', '24.04', enabled=ch.ubuntu, indent=S5)
        radio(i, _('26.04 LTS: the newest, supported until 2031; needs kernel 6.18, 7.2 or 7.3'), 'ubuntu_version', '26.04', enabled=ch.ubuntu, indent=S5)
        ttk.Frame(i, height=S2, style='Card.TFrame').pack()
        check(i, 'OpenWrt 25.12', 'openwrt', _('A router system with the LuCI web interface; about 140 MiB of RAM in use. Needs {1}.', size(f.need('openwrt'))))
        radio(i, _('The standard LuCI web interface'), 'openwrt_flavor', 'plain', enabled=ch.openwrt, indent=S5)
        radio(i, _('With the MU300 control panel: dashboard, cellular locks, SMS, AT terminal, USB modes'), 'openwrt_flavor', 'luci', enabled=ch.openwrt, indent=S5)
        if ch.ubuntu and ch.openwrt:
            ttk.Label(i, text=_('Which one starts first?'), style='Card.Bold.TLabel').pack(anchor='w', pady=(S3, S1))
            line = ttk.Frame(i, style='Card.TFrame')
            line.pack(anchor='w')
            ttk.Radiobutton(line, text='Ubuntu', variable=self.v['boot_os'], value='ubuntu', style='Card.TRadiobutton').pack(side='left')
            ttk.Radiobutton(line, text='OpenWrt', variable=self.v['boot_os'], value='openwrt', style='Card.TRadiobutton').pack(side='left', padx=S4)
        if room and room < f.need('both'):
            ttk.Label(i, text=_('This place has {1}: both systems together need {2}.', size(room), size(f.need('both'))), style='Card.Small.TLabel').pack(anchor='w', pady=(S2, 0))

        o, i = card(body, _('Kernel'), _('It can be changed later on the device: sudo mu300-update kernel 5.4|6.18|7.2|7.3'))
        add(o)
        radio(i, _('5.4  —  the vendor kernel: the longest tested, everything this project supports'), 'kernel', '5.4', enabled=ch.kernel_ok('5.4'),
              note=None if ch.kernel_ok('5.4') else _('The ZTE V50 runs a mainline kernel only (6.18, 7.2 or 7.3).') if ch.device == 'v50'
              else _('Not with Ubuntu 26.04: its programs use system calls that 5.4 does not have.'))
        radio(i, _('6.18  —  mainline Linux, long-term release: newer drivers and security fixes; no USB-C video output yet'), 'kernel', '6.18')
        radio(i, _('7.2  —  mainline Linux, stable release; tested less than 6.18'), 'kernel', '7.2')
        radio(i, _('7.3  —  the newest mainline Linux; tested less than 7.2'), 'kernel', '7.3')

        rel = ctl.releases
        o, i = card(body, _('Release'))
        add(o)
        latest = rel.get('latest')
        radio(i, _('The newest release: {1}', latest) if latest else _('The newest release (the installer looks it up)'), 'release_mode', 'latest')
        if rel.get('beta'):
            radio(i, _('The beta: {1}', rel['beta']), 'release_mode', 'beta',
                  note=_('A prerelease for testing, newer than {1}. It has not been run on a device as long as a full release.', latest))
        if ctl.releases_state == 'failed':
            ttk.Label(i, text=_('GitHub did not answer (offline, or its rate limit): the installer will look the release up itself.'),
                      style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w')
        files = rel.get('files', {}).get(ch.release or latest or '', {})
        if files:
            plan, total = download_plan(ch, files)
            ttk.Label(i, text=_('To download: {1} in {2} files (checked against the release\'s SHA256 sums).', size(total), len(plan)),
                      style='Card.Small.TLabel').pack(anchor='w', pady=(S2, 0))

    def _choose_rest(self, body, add, ctl, f, ch, check):
        o, i = card(body, _('Starting'))
        add(o)
        check(i, _('Start Linux by default instead of Android'), 'default_linux',
              _('If Linux does not finish starting several times in a row, the device goes back to Android by itself. That is also your way back with no computer at hand: cut the power while it is starting, that many times.'))
        if ch.default_linux:
            line = ttk.Frame(i, style='Card.TFrame')
            line.pack(anchor='w', padx=(S5, 0))
            ttk.Label(line, text=_('Failed starts in a row before Android (1-6):'), style='Card.TLabel').pack(side='left')
            sp = ttk.Combobox(line, textvariable=self.v['boot_attempts'], values=['1', '2', '3', '4', '5', '6'], width=3, state='readonly')
            sp.pack(side='left', padx=S2)

        o, i = card(body, _('Extras'))
        add(o)
        check(i, _('Copy Android\'s hotspot name and password to Linux'), 'hotspot', _('Your Wi-Fi keeps its name and password. Kept as it is when updating.'))
        check(i, _('The Mali GPU (OpenCL) userspace, about 90 MiB'), 'gpu')
        check(i, _('The VPN module (about 40 MB more to download, 120 MB on the device)'), 'vpn',
              _('mu300-vpn with Xray and sing-box. A system being replaced whose VPN is on needs it: the installer stops otherwise. It can be added later: sudo mu300-extra install vpn'))
        if not ch.package:          # (a file of the release, not of a zip)
            check(i, _('The web panel for Ubuntu (about 24 MB more to download, 50 MB on the device)'), 'panel',
                  _('Cockpit with an MU300 page, on the LAN only. It can be added later: sudo mu300-extra install panel'), enabled=ch.ubuntu)
        check(i, _('Let only this computer in over adb to Linux'), 'adb',
              _('adb to Linux over USB is on and lets every computer in: a root shell without a password for anyone who plugs in a cable. With this, only this computer\'s adb key (~/.android/adbkey.pub) is let in.'))

        if ch.place_existing(f):
            o, i = card(body, _('Linux is already installed there'), style='Accent')
            add(o)
            ttk.Radiobutton(i, text=_('Update: install the new release and keep everything you added or changed'), variable=self.v['existing'], value='update', style='Accent.TRadiobutton').pack(anchor='w')
            ttk.Label(i, text=_('/etc/mu300, users and home directories, SSH host keys, OpenWrt\'s UCI configuration and the hotspot settings are kept.'),
                      style='Accent.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', padx=(S5, 0), pady=(0, S2))
            ttk.Radiobutton(i, text=_('Wipe: erase the Linux filesystem and install from scratch'), variable=self.v['existing'], value='wipe', style='Accent.TRadiobutton').pack(anchor='w')
            if ch.existing == 'update':
                ttk.Checkbutton(i, text=_('Keep the current password'), variable=self.v['keep_password'], style='Accent.TCheckbutton').pack(anchor='w', pady=(S2, 0))
                if ch.keep_password:
                    line = ttk.Frame(i, style='Accent.TFrame')
                    line.pack(anchor='w', padx=(S5, 0))
                    ttk.Label(line, text=_('Current password'), style='Accent.TLabel').pack(side='left')
                    ttk.Entry(line, textvariable=self.v['current_password'], show='•', width=28).pack(side='left', padx=S2)
                    ttk.Label(i, text=_('The installer checks it on the device before anything is written, and stops when it is not the current one. Leave it empty when there is none (OpenWrt\'s root as it ships). The new password below is then needed only for a system the update installs new.'),
                              style='Accent.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', padx=(S5, 0), pady=(0, S2))

        o, i = card(body, _('Password'), _('For the user "ubuntu" (Ubuntu) and for "root" (OpenWrt): the login over SSH and the web interface. At least 6 characters.'))
        add(o)
        grid = ttk.Frame(i, style='Card.TFrame')
        grid.pack(anchor='w')
        ttk.Label(grid, text=_('Password'), style='Card.TLabel').grid(row=0, column=0, sticky='w', padx=(0, S3), pady=2)
        ttk.Entry(grid, textvariable=self.v['password'], show='•', width=28).grid(row=0, column=1, sticky='w', pady=2)
        ttk.Label(grid, text=_('Repeat'), style='Card.TLabel').grid(row=1, column=0, sticky='w', padx=(0, S3), pady=2)
        ttk.Entry(grid, textvariable=self.v['password2'], show='•', width=28).grid(row=1, column=1, sticky='w', pady=2)
        ttk.Label(i, text=_('It goes to the installer in a private file that is deleted when the run ends; only its hash reaches the device. It is never written to a log.'),
                  style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', pady=(S2, 0))

        o, i = card(body)
        add(o)
        ttk.Checkbutton(i, text=_('Advanced'), variable=self._flag('advanced'), style='Card.TCheckbutton').pack(anchor='w')
        if self.advanced:
            g = ttk.Frame(i, style='Card.TFrame')
            g.pack(fill='x', pady=(S2, 0))
            if not ch.package:      # (a package is its own release)
                ttk.Radiobutton(g, text=_('Another release, by its tag:'), variable=self.v['release_mode'], value='tag', style='Card.TRadiobutton').grid(row=0, column=0, sticky='w')
                ttk.Entry(g, textvariable=self.v['release_tag'], width=18).grid(row=0, column=1, sticky='w', padx=S2)
                ttk.Label(g, text=_('A mirror with the release\'s files (URL):'), style='Card.TLabel').grid(row=1, column=0, sticky='w', pady=2)
                ttk.Entry(g, textvariable=self.v['release_url'], width=32).grid(row=1, column=1, sticky='w', padx=S2)
            ttk.Label(g, text=_('Parallel connections per download:'), style='Card.TLabel').grid(row=2, column=0, sticky='w', pady=2)
            ttk.Combobox(g, textvariable=self.v['fetch_jobs'], values=['1', '2', '4', '8', '16'], width=4, state='readonly').grid(row=2, column=1, sticky='w', padx=S2)
            ttk.Label(g, text=_('Another place for the VPN module (URL):'), style='Card.TLabel').grid(row=3, column=0, sticky='w', pady=2)
            ttk.Entry(g, textvariable=self.v['vpn_url'], width=32).grid(row=3, column=1, sticky='w', padx=S2)
            ttk.Label(g, text=_('A copy of the VPN module on this computer:'), style='Card.TLabel').grid(row=4, column=0, sticky='w', pady=2)
            ttk.Entry(g, textvariable=self.v['vpn_module'], width=32).grid(row=4, column=1, sticky='w', padx=S2)
            check(i, _('Copy the boot chain of Android\'s slot to the Linux slot where they differ'), 'sync_bootchain',
                  _('uboot, sml, teecfg, trustos and vbmeta, backed up first; needed after an Android update. Switch it off only if you know why.'))
            check(i, _('Continue although the device is not recognised as an F50 or U30 Air'), 'unknown_model',
                  _('The installer treats it as an F50 and leaves its boot chain alone. On another device this can do damage.'))
        self.w['problems'] = ttk.Label(body, text='', style='BadText.TLabel', wraplength=700, justify='left')
        add(self.w['problems'])

    # ================================================================ Backup
    def page_backup(self, body):
        ctl, f, ch = self.ctl, self.ctl.facts, self.ctl.choices
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        work = desktop.work_dir(ctl.top, ctl.environ)
        slot = f.get('slot.android', 'a')
        o, i = card(body, _('What is copied to this computer before anything is written'))
        add(o)
        for name, why in ((_('Android\'s boot image (boot_{1})', slot), _('The Linux boot image is built from it, and it is what the uninstaller puts back. Compared with the device\'s by its SHA256.')),
                          (_('The first block of misc'), _('The 32 bytes that say which slot starts are in it.')),
                          (_('The boot chain of the Linux slot'), _('Only when the installer has to replace it (after an Android update): uboot, sml, teecfg, trustos, vbmeta - into work/bootchain, and onto the Linux filesystem.')),
                          (_('The partition table'), _('Only on the make-room path, before it is rewritten: into work/gpt.'))):
            ttk.Label(i, text='•  ' + name, style='Card.Bold.TLabel').pack(anchor='w')
            ttk.Label(i, text=why, style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', padx=(S4, 0), pady=(0, S2))
        banner(i, _('Never copied by the installer or this window: the NV and calibration partitions (IMEI, radio calibration). The installer does not read or write them. A copy of those for safekeeping is a separate tool, tools/backup-device.sh, that you run yourself - keep its output private.'), 'accent').pack(fill='x', pady=(S2, 0))

        o, i = card(body, _('Where it goes'))
        add(o)
        free = desktop.free_bytes(work)
        files = ctl.releases.get('files', {}).get(ch.release or ctl.releases.get('latest') or '', {})
        need = work_need(ch, files) if files else 2 * GIB
        enough = free is None or free >= need
        facts(i, [(_('Folder'), work), (_('Free on that disk'), size(free) if free is not None else '—', 'good' if enough else 'bad'),
                  (_('Needed, about'), _('{1} (the downloads, the device\'s files, the images built from them)', size(need)))]).pack(fill='x')
        if not enough:
            banner(i, _('There is not enough free space for this installation in that folder. Free some, or set MU300_WORK to a folder on another disk before starting.'), 'bad').pack(fill='x', pady=(S2, 0))
        line = ttk.Frame(i, style='Card.TFrame')
        line.pack(anchor='w', pady=(S3, 0))
        b = ttk.Button(line, text=_('Back up now'), command=self._backup)
        b.pack(side='left')
        if ctl.busy() or ctl.device_state() != 'android':
            b.state(['disabled'])
        ttk.Button(line, text=_('Open the folder'), command=lambda: desktop.open_folder(work) or self.say(_('The folder does not exist yet.'))).pack(side='left', padx=S2)
        ttk.Label(i, text=_('The installation makes the same copies by itself. "Back up now" only makes them, and writes nothing to the device.'),
                  style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', pady=(S2, 0))
        if ctl.busy() and ctl.run_kind == 'backup':
            bar = ttk.Progressbar(i, mode='indeterminate', style='Thin.Horizontal.TProgressbar')
            bar.pack(fill='x', pady=(S3, 0))
            bar.start(40)
        last = ctl.last.get('backup')
        if ctl.backup and last is not None and last.ok:
            o, i = card(body, _('Backed up'), style='Good')
            add(o)
            facts(i, [(_('Boot image'), ctl.masker.mask(ctl.backup.get('backup.boot', ''))), ('SHA256', ctl.backup.get('backup.sha256', '')),
                      (_('misc'), ctl.masker.mask(ctl.backup.get('backup.misc', '')))], style='Good').pack(fill='x')
        elif last is not None and last.finished and not last.ok:
            add(banner(body, _('The backup stopped: {1}', last.error or '?'), 'bad'))

    def _backup(self):
        if self.ctl.backup_now():
            self.render()

    # ================================================================ Install
    def page_install(self, body):
        ctl = self.ctl
        p = ctl.progress if ctl.run_kind in ('install', 'makeroom') else None
        row = 0

        def add(widget, pad=S3, sticky='ew'):
            nonlocal row
            widget.grid(row=row, column=0, sticky=sticky, pady=(0, pad))
            row += 1

        if p is None or (p.finished and ctl.run is None and not self._show_result):
            # before the start: the choices once more
            o, i = card(body, _('Ready'), _('This is what you chose. The installer downloads and prepares everything first, then shows exactly what it will write and waits for your yes: nothing is written to the device before that.'))
            add(o)
            facts(i, summary(ctl.choices, ctl.facts, ctl.releases.get('latest') or ''), label_width=24).pack(fill='x')
            if ctl.facts.bootloader == 'locked':
                add(banner(body, _('The bootloader is locked. Did you patch TrustOS? With a patched TrustOS Linux boots on a locked bootloader; without it the device goes back to Android by itself after the install.'), 'warn'))
            probs = ctl.problems()
            if probs:
                add(banner(body, '\n'.join('• ' + m for _f, m in probs), 'bad'))
            line = ttk.Frame(body)
            add(line)
            self.w['start'] = ttk.Button(line, text=_('Start'), style='Accent.TButton', command=self._start)
            self.w['start'].pack(side='left')
            ttk.Label(line, text=_('It takes about 15 minutes, most of it the download.'), style='Muted.TLabel').pack(side='left', padx=S3)
            return

        self._run_panel(body, add)

    def _run_panel(self, body, add, style='Card'):
        """A run as it happens: its stages, how far it is, the file that is coming in, the log. Built once, then kept
        up to date by _run_update()."""
        self.w['run-banner'] = ttk.Frame(body)
        add(self.w['run-banner'], 0)
        self._run_result()
        o, i = card(body, style=style)
        add(o)
        cols = ttk.Frame(i, style=style + '.TFrame')
        cols.pack(fill='x')
        cols.columnconfigure(1, weight=1)
        self.w['steps'] = StepList(cols, self.theme, style=style)
        self.w['steps'].grid(row=0, column=0, sticky='nw', padx=(0, S5))
        right = ttk.Frame(cols, style=style + '.TFrame')
        right.grid(row=0, column=1, sticky='new')
        self.w['run-title'] = ttk.Label(right, text='', style=style + '.H2.TLabel', wraplength=380, justify='left')
        self.w['run-title'].pack(anchor='w', fill='x')
        self.w['run-percent'] = ttk.Label(right, text='', style=style + '.Muted.TLabel')
        self.w['run-percent'].pack(anchor='w', pady=(S2, S1))
        self.w['run-bar'] = ttk.Progressbar(right, maximum=100, style='Horizontal.TProgressbar')
        self.w['run-bar'].pack(fill='x')
        self.w['file-name'] = ttk.Label(right, text='', style=style + '.TLabel')
        self.w['file-name'].pack(anchor='w', pady=(S4, S1))
        self.w['file-bar'] = ttk.Progressbar(right, maximum=1000, style='Thin.Horizontal.TProgressbar')
        self.w['file-bar'].pack(fill='x')
        self.w['file-text'] = ttk.Label(right, text='', style=style + '.Small.TLabel')
        self.w['file-text'].pack(anchor='w', pady=(S1, 0))
        self.w['verified'] = ttk.Label(right, text='', style=style + '.Small.TLabel', wraplength=380, justify='left')
        self.w['verified'].pack(anchor='w', pady=(S2, 0))
        line = ttk.Frame(body)
        add(line)
        self.w['cancel'] = ttk.Button(line, text=_('Stop'), command=self._cancel)
        self.w['cancel'].pack(side='left')
        self.w['cancel-note'] = ttk.Label(line, text='', style='Muted.TLabel', wraplength=520, justify='left')
        self.w['cancel-note'].pack(side='left', padx=S3)
        self._log_panel(body, add)
        self._run_update()

    def _log_panel(self, body, add):
        line = ttk.Frame(body)
        add(line, S2)
        self.w['log-toggle'] = ttk.Checkbutton(line, text=_('Show the installer\'s own output'), variable=self._flag_log())
        self.w['log-toggle'].pack(side='left')
        ttk.Button(line, text=_('Copy the log'), command=self._copy_log).pack(side='left', padx=(S4, 0))
        ttk.Button(line, text=_('Save the log…'), command=self._save_log).pack(side='left', padx=S2)
        note = ttk.Label(body, text=_('Serial numbers, IMEI, IMSI, ICCID and passwords are masked in the log, on the screen and in what is copied or saved.'),
                         style='Small.TLabel', wraplength=700, justify='left')
        add(note, S2)
        if self.log_open:
            c = self.theme.c
            frame = ttk.Frame(body)
            add(frame)
            text = tk.Text(frame, height=16, wrap='none', background=c['log_bg'], foreground=c['log_text'], insertbackground=c['log_text'],
                           font=self.theme.f['mono'], borderwidth=0, highlightthickness=0, padx=S3, pady=S2, state='disabled')
            bar = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
            text.configure(yscrollcommand=bar.set)
            text.grid(row=0, column=0, sticky='nsew')
            bar.grid(row=0, column=1, sticky='ns')
            frame.columnconfigure(0, weight=1)
            self.w['log'] = text
            self.log_seen = 0
            self._log_update()

    def _flag_log(self):
        var = tk.BooleanVar(self.root, value=self.log_open)

        def changed(*_a):
            self.log_open = var.get()
            self.later(self.render)
        var.trace_add('write', changed)
        self.w['flag-log'] = var
        return var

    def _log_update(self):
        text = self.w.get('log')
        if text is None or not text.winfo_exists():
            return
        lines = self.ctl.log
        if self.log_seen > len(lines):
            self.log_seen = 0
            text.configure(state='normal')
            text.delete('1.0', 'end')
            text.configure(state='disabled')
        if self.log_seen < len(lines):
            at_end = text.yview()[1] > 0.999
            text.configure(state='normal')
            text.insert('end', '\n'.join(lines[self.log_seen:]) + '\n')
            text.configure(state='disabled')
            self.log_seen = len(lines)
            if at_end:
                text.see('end')

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.ctl.log_text())
        self.say(_('The log is on the clipboard (masked).'))

    def _save_log(self):
        path = filedialog.asksaveasfilename(parent=self.root, title=_('Save the log'), defaultextension='.txt', initialfile='mu300-install-log.txt',
                                            filetypes=[(_('Text'), '*.txt')])
        if path:
            try:
                self.ctl.save_log(path)
                self.say(_('Saved: {1}', path))
            except OSError as e:
                messagebox.showerror(_('Save the log'), str(e), parent=self.root)

    def _start(self):
        self._show_result = True
        if self.ctl.install():
            self.render()

    def _cancel(self):
        p = self.ctl.progress
        if p is None or not p.can_cancel():
            return
        if messagebox.askyesno(_('Stop'), _('Stop the run? Nothing has been written to the device yet; what was downloaded is kept for the next time.'), parent=self.root):
            self.ctl.cancel()

    def _run_update(self, pulse=False):
        """Bring the run's display up to date (only what exists on the page that is shown)."""
        p = self.ctl.progress
        w = self.w
        if p is None or 'steps' not in w or not w['steps'].winfo_exists():
            self._log_update()
            return
        w['steps'].set(p.steps, _)
        overall = p.overall()
        w['run-bar'].configure(value=overall)
        w['run-title'].configure(text=p.title or _('Starting…'))
        w['run-percent'].configure(text=_('{1} % of the whole run', overall) if p.steps else '')
        name = p.file
        f = p.files.get(name) if name else None
        if f:
            w['file-name'].configure(text=name)
            w['file-bar'].configure(value=(1000 * f['bytes'] / f['total']) if f['total'] else 0)
            bits = ['%s / %s' % (size(f['bytes']), size(f['total']))]
            if f['speed'] > 0:
                bits.append('%s/s' % size(f['speed']))
            if f['eta'] is not None:
                bits.append(_('about {1} left', duration(f['eta'])))
            w['file-text'].configure(text='   ·   '.join(bits))
        else:
            w['file-name'].configure(text='')
            w['file-bar'].configure(value=0)
            w['file-text'].configure(text='')
        done = [n for n, info in p.files.items() if info['verify'] in ('ok', 'cached')]
        bad = [n for n, info in p.files.items() if info['verify'] == 'mismatch']
        text = ''
        if done:
            text = _('SHA256 verified: {1}', ', '.join(done))
        if bad:
            text += ('\n' if text else '') + _('SHA256 mismatch: {1}', ', '.join(bad))
        w['verified'].configure(text=text)
        can = p.can_cancel()
        w['cancel'].state(['!disabled'] if can else ['disabled'])
        if p.finished:
            note = ''
        elif can:
            note = _('Stopping is safe now: nothing has been written to the device.')
        else:
            note = _('The installer is writing to the device. This cannot be stopped: wait until it has finished.')
        w['cancel-note'].configure(text=note)
        self._log_update()

    def _run_finished(self):
        """A run ended: show what it ended with."""
        ctl = self.ctl
        kind = ctl.run_kind
        p = ctl.last.get(kind)
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
        if kind in ('install', 'makeroom', 'uninstall', 'command'):
            self.auto_checked = None
        if kind == 'install' and p is not None and p.ok:
            # the password has done its work: it is not kept in the window
            self.v['password'].set('')
            self.v['password2'].set('')
            self.v['current_password'].set('')
            self._show_result = False
            self.goto('install', [s for s, _l in WIZARD].index('done'))
            return
        self.render()

    def _run_result(self):
        """How the run that is shown ended, above its stages (nothing while it still runs)."""
        ctl = self.ctl
        kind, p = ctl.run_kind, ctl.progress
        if p is None or not p.finished or ctl.run is not None:
            return
        box = self.w['run-banner']
        if kind == 'makeroom' and p.ok:
            banner(box, _('Room was made. The device restarts into Android\'s recovery, which resets Android (a few minutes, more than one restart). Set Android up again, turn USB debugging on and bring root back (the Magisk app, Shell allowed); then press Refresh on the Device page: the installation continues where it was prepared.'), 'good').pack(fill='x', pady=(0, S3))
        elif kind in ('install', 'makeroom') and not p.ok:
            banner(box, _('It stopped: {1}', p.error or '?'), 'bad').pack(fill='x', pady=(0, S2))
            reached = [s_[0] for s_ in p.steps if s_[2] in ('done', 'failed', 'running')]
            wrote = 'device' in reached or 'repartition' in reached
            if kind == 'install':
                if 'device' not in reached:
                    note = _('Nothing was written to the device.')
                elif 'boot' not in reached:
                    note = _('The Linux filesystem may be partly written, but the boot image and the 32 bytes that arm Linux were not: the device still starts Android.')
                else:
                    note = _('The boot image step had begun; the log says what failed. If Linux does not start, the device goes back to Android by itself.')
                ttk.Label(box, text=note, style='Muted.TLabel', wraplength=700, justify='left').pack(anchor='w', pady=(0, S2))
            line = ttk.Frame(box)
            line.pack(anchor='w', pady=(0, S3))
            ttk.Button(line, text=_('Back to the choices'), command=self._again).pack(side='left')
            if kind == 'install' and not wrote:
                ttk.Button(line, text=_('Try again'), style='Accent.TButton', command=self._start).pack(side='left', padx=S2)

    def _again(self):
        self._show_result = False
        self.goto('install', [s for s, _l in WIZARD].index('choose'))

    # ---- the installer's own summary, and the yes it waits for
    def _confirm(self):
        p = self.ctl.progress
        if p is None or p.waiting is None or self.dialog is not None:
            return
        qid = p.waiting.get('id', '')
        word = {'install': 'INSTALL', 'uninstall': 'UNINSTALL'}.get(self.ctl.run_kind)
        if qid != 'MU300_ANS_CONFIRM' or word is None:
            # a question nobody planned for: answer nothing rather than something
            self.ctl.answer('')
            return
        c = self.theme.c
        d = tk.Toplevel(self.root)
        self.dialog = d
        d.title(_('The installer\'s summary'))
        d.configure(background=c['bg'])
        d.transient(self.root)
        frame = ttk.Frame(d, padding=(S5, S5))
        frame.pack(fill='both', expand=True)
        uninstall = self.ctl.run_kind == 'uninstall'
        ttk.Label(frame, text=_('This is what will be done') if uninstall else _('This is what will be written'), style='H2.TLabel').pack(anchor='w')
        ttk.Label(frame, text=_('The uninstaller has looked at the device. It waits for your yes; nothing has been changed yet.') if uninstall
                  else _('Everything is downloaded, verified and built. The installer shows its own summary and waits for your yes; nothing has been written to the device yet.'),
                  style='Muted.TLabel', wraplength=600, justify='left').pack(anchor='w', pady=(S1, S3))
        lines = [l for l in p.heading_lines if l.strip() and not l.strip().startswith('==>')]
        text = tk.Text(frame, height=min(16, max(6, len(lines) + 1)), width=96, wrap='word', background=c['log_bg'], foreground=c['log_text'],
                       font=self.theme.f['mono'], borderwidth=0, highlightthickness=0, padx=S3, pady=S2)
        text.insert('1.0', '\n'.join(l for l in lines if not l.rstrip().endswith(']:')))
        text.configure(state='disabled')
        text.pack(fill='both', expand=True)
        ttk.Label(frame, text=_('If Linux does not start, the device goes back to Android by itself.') if not uninstall else '',
                  style='Muted.TLabel').pack(anchor='w', pady=(S2, 0))
        line = ttk.Frame(frame)
        line.pack(fill='x', pady=(S4, 0))

        def answer(text_):
            self.dialog = None
            d.destroy()
            self.ctl.answer(text_)

        ttk.Button(line, text=_('Uninstall now') if uninstall else _('Install now'), style='Accent.TButton', command=lambda: answer(word)).pack(side='right')
        cancel = ttk.Button(line, text=_('Cancel: write nothing'), command=lambda: answer('no'))
        cancel.pack(side='right', padx=S2)
        d.protocol('WM_DELETE_WINDOW', lambda: answer('no'))
        d.bind('<Escape>', lambda e: answer('no'))
        cancel.focus_set()
        d.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - d.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - d.winfo_height()) // 3)
        d.geometry('+%d+%d' % (x, y))
        try:
            d.grab_set()
        except tk.TclError:
            pass

    # ================================================================ Finish
    def page_done(self, body):
        ctl = self.ctl
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        inst = ctl.installed
        if not inst:
            add(banner(body, _('Nothing has been installed in this session yet.'), 'warn'))
            return
        done = inst['done']
        info = connect(inst['kind'], inst['boot'], inst['systems'], inst['panel'])
        names = {'ubuntu': 'Ubuntu', 'openwrt': 'OpenWrt', 'openwrt-luci': 'OpenWrt'}
        add(banner(body, _('Linux is installed. The device is restarting into {1}; give it about a minute.', names.get(inst['boot'], inst['boot'])), 'good'))
        o, i = card(body, _('How to reach it'), _('On the computer the device is plugged into, over its USB network:'))
        add(o)
        rows = [(_('Address'), info['ip']), ('SSH', 'ssh ' + info['ssh']), (_('User and password'), _('{1}, with the password you chose', info['user']))]
        for name, url in info['web']:
            rows.append((name, url))
        if inst['kind'] == 'u30air':
            rows.append((_('Note'), _('The U30 Air uses 192.168.78.1; the F50 uses 192.168.77.1.')))
        facts(i, rows).pack(fill='x')
        line = ttk.Frame(i, style='Card.TFrame')
        line.pack(anchor='w', pady=(S3, 0))
        ttk.Button(line, text=_('Open an SSH session'), style='Accent.TButton', command=lambda: self._ssh(info['user'], info['ip'])).pack(side='left')
        for name, url in info['web']:
            ttk.Button(line, text=_('Open {1}', name), command=lambda u=url: desktop.open_url(u)).pack(side='left', padx=(S2, 0))
        ttk.Label(i, text=_('The Wi-Fi network the device broadcasts is its hotspot. Then try: sudo mu300-toolkit'), style='Card.Small.TLabel').pack(anchor='w', pady=(S3, 0))
        if sys.platform == 'darwin':
            ttk.Label(i, text=_('macOS does not set up a network interface it has never seen while the screen is locked: keep the Mac unlocked the first time.'),
                      style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w')

        o, i = card(body, _('If it does not start'))
        add(o)
        texts = [_('Nothing to do: after {1} failed starts in a row the device goes back to Android by itself.', inst['attempts']) if inst['default_linux']
                 else _('Android is still the default: Linux starts once, and a start that fails returns to Android.'),
                 _('To go back yourself with no computer at hand: cut the power while it is starting, that many times in a row.'),
                 _('From Linux: sudo mu300-next-boot android && sudo reboot. From Android (with Magisk): su -c mu300-linux starts Linux again.'),
                 _('The Manage page does both over the cable, and uninstalls.')]
        for t in texts:
            ttk.Label(i, text='•  ' + t, style='Card.TLabel', wraplength=660, justify='left').pack(anchor='w', pady=1)

        o, i = card(body, _('News'), style='Accent')
        add(o)
        ttk.Label(i, text=_('News and new releases are on the project\'s Telegram channel; you can follow it.'), style='Accent.TLabel').pack(anchor='w')
        ttk.Button(i, text=_('Follow on Telegram'), command=lambda: desktop.open_url(TELEGRAM)).pack(anchor='w', pady=(S2, 0))
        self._log_panel(body, add)

    def _ssh(self, user, ip):
        cmd = desktop.ssh_command(user, ip)
        if cmd and desktop.open_terminal(cmd):
            self.say(_('A terminal was opened: {1}', ' '.join(cmd)))
        elif cmd:
            self.root.clipboard_clear()
            self.root.clipboard_append(' '.join(cmd))
            messagebox.showinfo('SSH', _('No terminal could be opened from here. The command is on the clipboard: {1}', ' '.join(cmd)), parent=self.root)

    # ================================================================ Manage
    def page_manage(self, body):
        ctl = self.ctl
        state = ctl.device_state()
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        busy = ctl.busy()

        def action(parent, text, command, style='TButton', enabled=True):
            b = ttk.Button(parent, text=text, command=command, style=style)
            if busy or not enabled:
                b.state(['disabled'])
            return b

        if state in ('none', 'no-adb', 'choose', 'unauthorized', 'offline', 'other'):
            add(banner(body, _('No device of this project answers. Connect it and look at the Device page of "Install" for what is missing.'), 'warn'))
        elif state == 'android':
            f = ctl.facts
            o, i = card(body, _('The device is in Android'))
            add(o)
            rows = [(_('Model'), MODELS.get(ctl.kind(), '—')),
                    (_('MU300 Linux'), (_('installed') if (f.existing or (f.sd and f.sd[2] == 'yes')) else _('not found')) if f.checked else _('not checked yet'))]
            facts(i, rows).pack(fill='x')
            line = ttk.Frame(i, style='Card.TFrame')
            line.pack(anchor='w', pady=(S3, 0))
            action(line, _('Start Linux'), lambda: self._command('to-linux', _('Start Linux now? The device restarts. This uses the on-device switch the installer put there (a Magisk module).'))).pack(side='left')
            action(line, _('Check the device'), self.refresh_devices).pack(side='left', padx=S2)
            ttk.Label(i, text=_('To update or to install another system or kernel from this computer, use "Install": with Linux already there it offers to update and keeps your settings.'),
                      style='Card.Small.TLabel', wraplength=640, justify='left').pack(anchor='w', pady=(S3, 0))
            o, i = card(body, _('Remove Linux'), _('Android becomes the only thing that starts again, the Linux slot gets Android\'s own boot image back, and the Linux filesystem is erased if you say so. Android, its data and the other partitions are not touched.'))
            add(o)
            action(i, _('Uninstall…'), self._uninstall_dialog, style='Danger.TButton').pack(anchor='w')
        else:
            info = ctl.linux
            kind = ctl.kind()
            ip = ctl.linux_ip or adbmod.USB_IPS.get(kind, '192.168.77.1')
            ubuntu = info.get('system', '') == 'ubuntu' or not info.get('system', '').startswith('openwrt')
            user = 'ubuntu' if (ubuntu and info) else ('root' if info else 'ubuntu')
            o, i = card(body, _('The device is running MU300 Linux'))
            add(o)
            if state == 'linux' and info:
                rows = [(_('Model'), MODELS.get(kind, '—')), (_('System'), '%s %s' % (info.get('system', '—'), info.get('version_id', ''))),
                        (_('Release'), info.get('release', '—')), (_('Kernel'), info.get('kernel', '—'))]
                if 'adb' in info:
                    rows.append((_('adb to Linux'), {'open': _('open to every computer'), 'keys': _('authorised keys only')}.get(info['adb'], info['adb'])))
                facts(i, rows).pack(fill='x')
            elif state == 'linux':
                ttk.Label(i, text=_('adb lists it, but it does not answer this computer (its adb takes authorised keys only, or it is still starting). Use SSH below.'),
                          style='Card.TLabel', wraplength=660, justify='left').pack(anchor='w')
            else:
                ttk.Label(i, text=_('It answers on SSH at {1}, without adb. The commands below are run by you, in a terminal, with the device\'s password.', ip),
                          style='Card.TLabel', wraplength=660, justify='left').pack(anchor='w')
            line = ttk.Frame(i, style='Card.TFrame')
            line.pack(anchor='w', pady=(S3, 0))
            ttk.Button(line, text=_('Open an SSH session'), command=lambda: self._ssh(user, ip)).pack(side='left')
            ttk.Button(line, text=_('Open the web interface'), command=lambda: desktop.open_url('http://%s' % ip if not ubuntu else 'https://%s:9090' % ip)).pack(side='left', padx=S2)
            over_adb = state == 'linux' and bool(info)
            o, i = card(body, _('Do something on it'), _('Over adb these run at once, as root, with the project\'s own commands.') if over_adb
                        else _('adb does not reach this Linux: type these on the device (SSH).'))
            add(o)
            if over_adb:
                line = ttk.Frame(i, style='Card.TFrame')
                line.pack(anchor='w')
                action(line, _('Back to Android'), lambda: self._command('to-android', _('Restart the device into Android?'))).pack(side='left')
                action(line, _('Restart'), lambda: self._command('reboot', _('Restart the device?'))).pack(side='left', padx=S2)
                line = ttk.Frame(i, style='Card.TFrame')
                line.pack(anchor='w', pady=(S3, 0))
                action(line, _('Look for an update'), lambda: self._command('update-check', None)).pack(side='left')
                action(line, _('Update now'), lambda: self._command('update', _('Download and install the newest release on the device? Settings and data are kept; it takes a few minutes and needs the device online.'))).pack(side='left', padx=S2)
                line = ttk.Frame(i, style='Card.TFrame')
                line.pack(anchor='w', pady=(S3, 0))
                ttk.Label(line, text=_('Kernel:'), style='Card.TLabel').pack(side='left')
                kbox = ttk.Combobox(line, values=['5.4', '6.18', '7.2', '7.3'], width=6, state='readonly')
                # the kernel it runs, not a fixed 6.18 (seen on F50-B running 7.2.10: the box said 6.18)
                running = info.get('kernel', '')
                kbox.set(next((k for k in ('5.4', '6.18', '7.2', '7.3') if running == k or running.startswith(k + '.')), '6.18'))
                kbox.pack(side='left', padx=S2)
                action(line, _('Switch to it'), lambda: self._command('kernel-' + kbox.get(), _('Install kernel {1} on the device? It restarts into it afterwards only when you restart it.', kbox.get()))).pack(side='left')
            else:
                for cmd, what in (('sudo mu300-next-boot android && sudo reboot', _('back to Android')), ('sudo mu300-update check', _('look for an update')),
                                  ('sudo mu300-update apply', _('update')), ('sudo mu300-update kernel 6.18', _('another kernel')),
                                  ('sudo mu300-os ubuntu|openwrt', _('the other system'))):
                    r = ttk.Frame(i, style='Card.TFrame')
                    r.pack(fill='x', pady=1)
                    ttk.Label(r, text=cmd, style='Card.Mono.TLabel', width=46, anchor='w').pack(side='left')
                    ttk.Label(r, text=what, style='Card.Muted.TLabel').pack(side='left')
        if ctl.progress is not None and ctl.run_kind in ('uninstall', 'command'):
            p = ctl.progress
            if p.finished and p.error:
                add(banner(body, _('It stopped: {1}', p.error), 'bad'))
            elif p.finished and ctl.run_kind == 'uninstall' and p.ok:
                add(banner(body, _('Linux was removed. The device starts stock Android; restart it once to check.'), 'good'))
            elif p.finished:
                add(banner(body, _('Done.'), 'good'))
            if ctl.run_kind == 'uninstall':
                self._run_panel(body, add)
                return
        self._log_panel(body, add)

    def _command(self, name, question):
        if question and not messagebox.askyesno(_('Manage'), question, parent=self.root):
            return
        self.log_open = True
        if self.ctl.command(name):
            self.render()

    def _uninstall_dialog(self):
        ctl, c = self.ctl, self.theme.c
        d = tk.Toplevel(self.root)
        d.title(_('Uninstall MU300 Linux'))
        d.configure(background=c['bg'])
        d.transient(self.root)
        frame = ttk.Frame(d, padding=(S5, S5))
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=_('Uninstall MU300 Linux'), style='H2.TLabel').pack(anchor='w')
        ttk.Label(frame, text=_('The uninstaller looks at the device first, shows what it will do and waits for your yes.'), style='Muted.TLabel',
                  wraplength=560, justify='left').pack(anchor='w', pady=(S1, S3))
        wipe = tk.StringVar(d, value='secure')
        sdw = tk.StringVar(d, value='keep')
        grow = tk.BooleanVar(d, value=False)
        word = tk.StringVar(d, value='')
        ttk.Label(frame, text=_('The Linux filesystem on the internal storage'), style='Bold.TLabel').pack(anchor='w')
        for value, text in (('secure', _('Secure: overwrite all of it and verify (a few minutes; your files are really gone)')),
                            ('quick', _('Quick: erase only the filesystem headers (the files stay readable on the flash)')),
                            ('keep', _('Keep: leave it in place (it just never starts again)'))):
            ttk.Radiobutton(frame, text=text, variable=wipe, value=value).pack(anchor='w')
        ttk.Label(frame, text=_('The Linux filesystem on the SD card (asked only when a card holds one)'), style='Bold.TLabel').pack(anchor='w', pady=(S3, 0))
        ttk.Radiobutton(frame, text=_('Erase it (its first 64 MiB are overwritten; no other card is ever touched)'), variable=sdw, value='erase').pack(anchor='w')
        ttk.Radiobutton(frame, text=_('Keep the card as it is'), variable=sdw, value='keep').pack(anchor='w')
        if sys.platform != 'win32':
            ttk.Label(frame, text=_('The partition table'), style='Bold.TLabel').pack(anchor='w', pady=(S3, 0))
            ttk.Checkbutton(frame, text=_('Give the space behind userdata back to it (experimental; ERASES Android\'s data)'), variable=grow).pack(anchor='w')
            ttk.Label(frame, text=_('Only for a device where this installer shrank userdata to make room. On the usual 64 GB device the space behind userdata is free from the factory: leave this off.'),
                      style='Small.TLabel', wraplength=560, justify='left').pack(anchor='w', padx=(S5, 0))
            # (FINDINGS 153: after a yes here a reinstall has to make room again, "it asks to repartition again")
            ttk.Label(frame, text=_('Leave it off too if Linux may be installed again: the installer then uses this space without touching Android. With it on, a new installation has to make room again, which resets Android once more.'),
                      style='Small.TLabel', wraplength=560, justify='left').pack(anchor='w', padx=(S5, 0))
            wl = ttk.Frame(frame)
            wl.pack(anchor='w', padx=(S5, 0), pady=(S1, 0))
            ttk.Label(wl, text=_('Type ERASE to allow that:')).pack(side='left')
            ttk.Entry(wl, textvariable=word, width=10).pack(side='left', padx=S2)
        line = ttk.Frame(frame)
        line.pack(fill='x', pady=(S5, 0))

        def go():
            if grow.get() and word.get() != 'ERASE':
                messagebox.showerror(_('Uninstall MU300 Linux'), _('Type ERASE (in capitals) to allow growing userdata, or switch that off.'), parent=d)
                return
            d.destroy()
            self.log_open = False
            if ctl.uninstall(wipe.get(), sdw.get(), grow.get(), word.get()):
                self.render()

        ttk.Button(line, text=_('Continue'), style='Accent.TButton', command=go).pack(side='right')
        ttk.Button(line, text=_('Cancel'), command=d.destroy).pack(side='right', padx=S2)
        d.bind('<Escape>', lambda e: d.destroy())
        d.update_idletasks()
        d.geometry('+%d+%d' % (self.root.winfo_rootx() + 80, self.root.winfo_rooty() + 60))
        try:
            d.grab_set()
        except tk.TclError:
            pass

    # ================================================================ Help
    def page_help(self, body):
        row = 0

        def add(widget, pad=S3):
            nonlocal row
            widget.grid(row=row, column=0, sticky='ew', pady=(0, pad))
            row += 1

        o, i = card(body, _('How an installation goes'))
        add(o)
        steps = (_('You need a ZTE F50 / MU300 or U30 Air that is already rooted and able to boot a modified boot image (an unlocked bootloader, or a locked one with a patched TrustOS), USB debugging on, a USB cable, and adb on this computer.'),
                 _('Device: plug it in. The window looks at it without writing anything: the model, root, the bootloader, the storage.'),
                 _('Storage: Linux goes into free space behind Android\'s partitions (about 32 GiB on the 64 GB device) or onto an SD card (F50). Android and its data stay.'),
                 _('What to install: Ubuntu, OpenWrt or both, the kernel, a password.'),
                 _('Install: the release is downloaded and verified, the device\'s own Wi-Fi and modem files are added, and the installer shows exactly what it will write. Only after your yes is anything written.'),
                 _('The device restarts into Linux. If Linux ever fails to start, it goes back to Android by itself.'))
        for n, t in enumerate(steps):
            r = ttk.Frame(i, style='Card.TFrame')
            r.pack(fill='x', pady=2)
            ttk.Label(r, text=str(n) if n else '•', style='Card.AccentText.TLabel', width=2).pack(side='left', anchor='n')
            ttk.Label(r, text=t, style='Card.TLabel', wraplength=640, justify='left').pack(side='left', anchor='w')
        banner(i, _('A hobby project, with no warranty. A wrong step, a power cut or a bad cable can still leave a device unusable. This project does not root, unlock or patch devices and gives no help with it.'), 'warn').pack(fill='x', pady=(S3, 0))

        o, i = card(body, _('Read more'))
        add(o)
        for text, url in ((_('The wiki: installation, choosing a system, kernels, troubleshooting'), WIKI), (_('Releases'), RELEASES),
                          (_('Report a problem (issues)'), ISSUES), (_('The illustrated guide'), SITE + 'install/')):
            link(i, text, lambda u=url: desktop.open_url(u), style='CardLink.TButton').pack(anchor='w', pady=(S1, 0))
            ttk.Label(i, text=url, style='Card.Small.TLabel').pack(anchor='w', pady=(0, S1))

        o, i = card(body, _('News'), style='Accent')
        add(o)
        ttk.Label(i, text=_('News and new releases are on the project\'s Telegram channel; you can follow it.'), style='Accent.TLabel').pack(anchor='w')
        r = ttk.Frame(i, style='Accent.TFrame')
        r.pack(anchor='w', pady=(S2, 0))
        ttk.Button(r, text=_('Follow on Telegram'), command=lambda: desktop.open_url(TELEGRAM)).pack(side='left')
        ttk.Label(r, text=TELEGRAM, style='Accent.Small.TLabel').pack(side='left', padx=S3)

        o, i = card(body, _('Something went wrong?'), _('A diagnostics report holds what an issue needs: this computer\'s system and tools, the devices adb lists, what the last check found and the log. Serial numbers, IMEI, IMSI, ICCID, passwords and your home folder\'s name are masked in it. Read it before you post it.'))
        add(o)
        r = ttk.Frame(i, style='Card.TFrame')
        r.pack(anchor='w')
        ttk.Button(r, text=_('Save a diagnostics report…'), command=self._diagnostics).pack(side='left')
        ttk.Button(r, text=_('Copy it'), command=self._diagnostics_copy).pack(side='left', padx=S2)

        o, i = card(body, _('About'))
        add(o)
        facts(i, [(_('This window'), _('drives install.sh / install.ps1 in their answers mode; it installs nothing by itself')),
                  ('Python', '%s, Tk %s' % (sys.version.split()[0], tk.TkVersion)),
                  (_('Project folder'), self.ctl.masker.mask(self.ctl.top)), ('adb', self.ctl.masker.mask(self.ctl.adb_path or '—'))]).pack(fill='x')
        self.w['status'] = ttk.Label(self.foot, text=self.status, style='Muted.TLabel')
        self.w['status'].grid(row=0, column=1, sticky='w')

    def _diagnostics(self):
        path = filedialog.asksaveasfilename(parent=self.root, title=_('Save a diagnostics report'), defaultextension='.txt',
                                            initialfile='mu300-diagnostics.txt', filetypes=[(_('Text'), '*.txt')])
        if path:
            try:
                with open(path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(self.ctl.diagnostics())
                self.say(_('Saved: {1}', path))
            except OSError as e:
                messagebox.showerror(_('Save a diagnostics report'), str(e), parent=self.root)

    def _diagnostics_copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.ctl.diagnostics())
        self.say(_('The report is on the clipboard (masked).'))


def can_start():
    """'' when a window can be opened here, else why not."""
    try:
        import tkinter as _tk
    except ImportError as e:
        return 'tkinter is missing: %s' % e
    if _tk.TkVersion < 8.6:
        # the Tk 8.5 of macOS's own Python opens a window that stays black on current macOS
        return 'Tk %s is too old for the window (8.6 or newer is needed)' % _tk.TkVersion
    try:
        r = _tk.Tk()
        r.destroy()
    except _tk.TclError as e:
        return 'no display for a window: %s' % e
    return ''


def main(argv=()):
    if '--check' in argv:
        why = can_start()
        print(why or 'ok')
        return 3 if why else 0
    if tk.TkVersion < 8.6:
        sys.stderr.write('The graphical installer needs Tk 8.6 or newer (this Python has %s).\n' % tk.TkVersion)
        return 3
    dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        sys.stderr.write('The graphical installer cannot open a window here: %s\n' % e)
        return 3
    root.withdraw()
    set_language(os_language())
    theme = Theme(root, dark=system_dark())
    ctl = Controller(top=TOP)
    w, h = 1120, 760
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = min(w, sw - 80), min(h, sh - 120)
    root.geometry('%dx%d+%d+%d' % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
    root.minsize(min(940, w), min(600, h))
    App(root, ctl, theme)
    ctl.start()
    root.deiconify()
    if sys.platform == 'darwin':
        # a window started from a terminal opens behind it
        root.lift()
        root.attributes('-topmost', True)
        root.after(300, lambda: root.attributes('-topmost', False))
    root.mainloop()
    return 0
