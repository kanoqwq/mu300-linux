"""The window's own building blocks, on top of ttk: a page that scrolls, a card, a banner, the row of steps of the
wizard, the picture of the eMMC, the list of a run's stages. They draw with the palette of theme.py and know
nothing about installing.
"""
import sys
import time
import tkinter as tk
from tkinter import ttk

from .theme import S1, S2, S3, S4, S5


class Scroll(ttk.Frame):
    """A frame whose content scrolls vertically when it is taller than the window; .body takes the content."""

    def __init__(self, parent, theme, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=theme.c['bg'])
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._set)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.columnconfigure(0, weight=1)
        # the bar is always there: a page that just fits would otherwise grow wider without it, need it again,
        # shrink, and never come to rest
        self.bar.grid(row=0, column=1, sticky='ns', padx=(4, 0))
        self.rowconfigure(0, weight=1)
        self.body.bind('<Configure>', self._body)
        self.canvas.bind('<Configure>', self._canvas)
        # the wheel scrolls the page wherever the pointer is on it (a Text or a list scrolls itself)
        top = self.winfo_toplevel()
        top.bind_all('<MouseWheel>', self._on_wheel)
        top.bind_all('<Button-4>', lambda e: self._step(-1, e))
        top.bind_all('<Button-5>', lambda e: self._step(1, e))
        self._shown = False

    def _set(self, lo, hi):
        # whether there is anything to scroll (the wheel asks); the bar itself stays where it is
        self._shown = float(lo) > 0.0 or float(hi) < 1.0
        self.bar.set(lo, hi)

    def _body(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        delta = event.delta if sys.platform == 'darwin' else event.delta / 120
        self._step(-1 if delta > 0 else 1, event)

    def _step(self, n, event=None):
        w = getattr(event, 'widget', None)
        if isinstance(w, (tk.Text, tk.Listbox)) or not self.winfo_exists():
            return
        try:
            if w is not None and w.winfo_toplevel() is not self.winfo_toplevel():
                return          # a dialog
        except (AttributeError, tk.TclError):
            return
        if self._shown:
            self.canvas.yview_scroll(n * 2, 'units')

    def top(self):
        self.canvas.yview_moveto(0)


def card(parent, title=None, subtitle=None, style='Card'):
    """A surface with a title; returns (outer frame, inner frame for the content). STYLE: Card, Good, Warn, Bad,
    Accent - the styles of labels and choices inside are '<STYLE>.TLabel' and so on."""
    outer = ttk.Frame(parent, style=style + '.TFrame', padding=(S4, S4, S4, S4))
    if title:
        ttk.Label(outer, text=title, style=style + '.H2.TLabel').pack(anchor='w')
    if subtitle:
        ttk.Label(outer, text=subtitle, style=style + '.Muted.TLabel', wraplength=640, justify='left').pack(anchor='w', pady=(S1, 0))
    inner = ttk.Frame(outer, style=style + '.TFrame')
    inner.pack(fill='x', expand=True, pady=(S3 if (title or subtitle) else 0, 0))
    return outer, inner


def banner(parent, text, tone='warn', wrap=680):
    """A coloured strip with one message: good, warn, bad or accent."""
    style = tone.capitalize()
    f = ttk.Frame(parent, style=style + '.TFrame', padding=(S3, S2))
    ttk.Label(f, text=text, style=style + '.TLabel', wraplength=wrap, justify='left').pack(anchor='w', fill='x')
    return f


def facts(parent, rows, style='Card', label_width=22):
    """Label and value in two columns. ROWS: (label, value) or (label, value, tone) with tone good/warn/bad."""
    f = ttk.Frame(parent, style=style + '.TFrame')
    for i, row in enumerate(rows):
        label, value = row[0], row[1]
        tone = row[2] if len(row) > 2 else ''
        ttk.Label(f, text=label, style=style + '.Muted.TLabel', width=label_width, anchor='w').grid(row=i, column=0, sticky='nw', pady=1)
        vstyle = style + ('.' + tone.capitalize() + 'Text' if tone else '') + '.TLabel'
        ttk.Label(f, text=value, style=vstyle, wraplength=460, justify='left').grid(row=i, column=1, sticky='w', pady=1)
    f.columnconfigure(1, weight=1)
    return f


def wrap_to(label, frame, pad=0):
    """Keep a label's text wrapped to the width of FRAME as the window is resized."""
    def fit(event):
        w = max(200, event.width - pad)
        if int(float(label.cget('wraplength') or 0)) != w:
            label.configure(wraplength=w)
    frame.bind('<Configure>', fit, add='+')


class Stepper(tk.Canvas):
    """The steps of the wizard in a row: a numbered dot and a name each, joined by a line; the ones done are filled,
    the current one is ringed. A step that has been reached can be clicked."""

    def __init__(self, parent, theme, steps, on_pick):
        super().__init__(parent, height=56, highlightthickness=0, borderwidth=0, background=theme.c['bg'])
        self.theme = theme
        self.steps = steps          # [(id, label)]
        self.on_pick = on_pick
        self.current = 0
        self.reached = 0
        self.bind('<Configure>', lambda e: self.draw())

    def set(self, current, reached):
        self.current, self.reached = current, max(reached, current)
        self.draw()

    def draw(self):
        self.delete('all')
        c, f = self.theme.c, self.theme.f
        n = len(self.steps)
        w = max(self.winfo_width(), 300)
        cell = w / n
        r = 11
        y = 16
        for i, (sid, label) in enumerate(self.steps):
            x = cell * i + cell / 2
            if i < n - 1:
                self.create_line(x + r + 4, y, x + cell - r - 4, y, fill=c['accent'] if i < self.current else c['line'], width=2)
            done, now = i < self.current, i == self.current
            fill = c['accent'] if (done or now) else c['bg']
            outline = c['accent'] if (done or now) else c['line']
            tag = 'step%d' % i
            self.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=2, tags=(tag,))
            self.create_text(x, y, text='✓' if done else str(i + 1), fill=c['accent_text'] if (done or now) else c['muted'],
                             font=f['small'], tags=(tag,))
            self.create_text(x, y + r + 14, text=label, fill=c['text'] if now else c['muted'],
                             font=f['bold'] if now else f['small'], width=cell - 8, tags=(tag,))
            if i <= self.reached:
                self.tag_bind(tag, '<Button-1>', lambda e, i=i: self.on_pick(i))
                self.tag_bind(tag, '<Enter>', lambda e: self.configure(cursor='hand2'))
                self.tag_bind(tag, '<Leave>', lambda e: self.configure(cursor=''))


class DiskBar(tk.Canvas):
    """The eMMC as one bar: each segment as wide as its share of the disk (but wide enough to be seen), coloured by
    its kind, with a legend below."""
    COLOURS = {'android': 'seg_android', 'userdata': 'seg_userdata', 'free': 'accent_soft', 'linux': 'accent', 'other': 'seg_other'}

    def __init__(self, parent, theme, surface='surface'):
        super().__init__(parent, height=46, highlightthickness=0, borderwidth=0, background=theme.c[surface])
        self.theme = theme
        self.segments = []
        self.bind('<Configure>', lambda e: self.draw())

    def set(self, segments):
        self.segments = [s for s in segments if s['bytes'] > 0]
        self.draw()

    def draw(self):
        self.delete('all')
        c = self.theme.c
        w = max(self.winfo_width(), 200)
        total = sum(s['bytes'] for s in self.segments)
        if total <= 0:
            self.create_rectangle(0, 4, w, 40, fill=c['sunken'], outline=c['line'])
            return
        # no segment thinner than 3 pixels: a 4 MiB gap is still a place on the disk
        widths = [max(3.0, w * s['bytes'] / total) for s in self.segments]
        scale = w / sum(widths)
        x = 0.0
        for s, sw in zip(self.segments, widths):
            sw *= scale
            fill = c[self.COLOURS.get(s['kind'], 'seg_other')]
            self.create_rectangle(x, 4, x + sw, 40, fill=fill, outline=c['surface'] if 'surface' in c else '', width=1)
            if s['kind'] == 'free':
                # hatch: this is where Linux goes
                step = 8
                i = int(x)
                while i < x + sw:
                    self.create_line(i, 40, min(i + 36, x + sw), max(4, 40 - (min(i + 36, x + sw) - i)), fill=c['accent'], width=1)
                    i += step
                self.create_rectangle(x, 4, x + sw, 40, outline=c['accent'], width=1)
            x += sw


class StepList(ttk.Frame):
    """The stages of a run, one line each, with their state: a ring (to come), a filled dot that pulses (running), a
    tick (done), a cross (failed), a dash (not needed)."""
    GLYPH = {'pending': '○', 'running': '●', 'done': '✓', 'failed': '✕', 'skipped': '–'}

    def __init__(self, parent, theme, style='Card'):
        super().__init__(parent, style=style + '.TFrame')
        self.theme = theme
        self.style_name = style
        self.rows = {}
        self._pulse = False

    def set(self, steps, translate):
        """STEPS: [[id, label, state]]"""
        ids = [s[0] for s in steps]
        if ids != list(self.rows):
            for child in self.winfo_children():
                child.destroy()
            self.rows = {}
            for i, (sid, label, _state) in enumerate(steps):
                mark = ttk.Label(self, text='', style=self.style_name + '.TLabel', width=2, anchor='center')
                name = ttk.Label(self, text=translate(label), style=self.style_name + '.TLabel')
                mark.grid(row=i, column=0, sticky='w', pady=1)
                name.grid(row=i, column=1, sticky='w', padx=(S2, 0), pady=1)
                self.rows[sid] = (mark, name)
        c, f = self.theme.c, self.theme.f
        self._pulse = time.monotonic() % 1.4 < 0.8        # the running stage's dot breathes
        for sid, _label, state in steps:
            mark, name = self.rows[sid]
            colour = {'pending': c['faint'], 'running': c['accent'] if self._pulse else c['muted'], 'done': c['good'],
                      'failed': c['bad'], 'skipped': c['faint']}[state]
            mark.configure(text=self.GLYPH[state], foreground=colour)
            name.configure(foreground=c['text'] if state in ('running', 'done', 'failed') else c['muted'],
                           font=f['bold'] if state == 'running' else f['body'])


def link(parent, text, command, style='Link.TButton'):
    b = ttk.Button(parent, text=text, command=command, style=style, cursor='hand2', takefocus=True)
    return b


def spacer(parent, height=S4, style='TFrame'):
    return ttk.Frame(parent, height=height, style=style)


__all__ = ['Scroll', 'Stepper', 'DiskBar', 'StepList', 'card', 'banner', 'facts', 'wrap_to', 'link', 'spacer', 'S1', 'S2', 'S3', 'S4', 'S5']
