"""Soundboard: press a hotkey, the sound file goes out through your game mic."""
import json
import os
import sys

if len(sys.argv) > 1 and sys.argv[1] == '--yt-worker':
    # child process of the YouTube clipper: do one job and exit, never load the UI
    import ytclip
    sys.exit(ytclip.worker_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == '--micfx-selftest':
    # diagnostics: load every voice-processing part the way the app would, write a report
    import micfx
    sys.exit(micfx.selftest(sys.argv[2] if len(sys.argv) > 2 else 'micfx-selftest.json'))


import queue
import tempfile
import threading
import tkinter as tk
from functools import partial
from tkinter import filedialog, messagebox


class _NullIO:
    """A --windowed exe has no stdout/stderr; without this, any print or traceback
    from inside a Tk callback raises and can wedge the event loop."""

    def write(self, *_args):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stderr is None:
    sys.stderr = _NullIO()
if sys.stdout is None:
    sys.stdout = _NullIO()

import customtkinter as ctk  # noqa: E402

import audio_engine as ae  # noqa: E402
import sources as src
import hotkeys as hk
import theme as T
import tray as tray_mod
import autostart
import random
import re
import time
import webbrowser
import updater
import version as ver

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__))
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')
SOUNDS_DIR = os.path.join(APP_DIR, 'sounds')
PREVIEW_DIR = os.path.join(tempfile.gettempdir(), 'SoundSaoTer_preview')
AUDIO_EXT = ('.wav', '.mp3', '.ogg', '.flac', '.aiff', '.aif', '.w64')
NONE_LABEL = '— ไม่ใช้ —'
MAX_ROWS = 250
DEFAULT_SLOTS = 8
PICKER_ROWS = 40



def default_config():
    return {
        'game_device': '', 'monitor_device': '', 'mic_device': '',
        'game_volume': 100, 'monitor_volume': 60, 'mic_volume': 100,
        'exclusive': True, 'hotkeys_enabled': True, 'tray': True, 'tray_notified': False,
        'stop_hotkey': 'ctrl+alt+s', 'sounds': [],
        'hear_self': False, 'auto_update': True, 'color': T.DEFAULT, 'mode': 'dark',
        'trim_silence': True, 'normalize': True, 'cooldown': 2.0, 'random_hotkey': '',
        'cache_mb': 40, 'slots': [], 'view': 'slots',
        'mic_denoise': False, 'mic_gate': False, 'mic_gate_sens': 50, 'mic_agc': False,
        'mic_aec': False, 'mic_aec_device': '',
    }


EDIT_KEYS = {86: '<<Paste>>', 67: '<<Copy>>', 88: '<<Cut>>', 65: '<<SelectAll>>'}   # physical V C X A


def install_edit_keys(root):
    """Ctrl+V/C/X/A and a right-click menu for every text box.

    Tk matches Ctrl+V by the typed character. On the Thai keyboard layout that key
    types "อ", so Ctrl+V did nothing — pasting a link was impossible unless the user
    first switched to English. Match the physical key instead.
    """
    def on_ctrl_key(event):
        if not event.state & 0x4 or event.keysym.lower() in ('v', 'c', 'x', 'a'):
            return None                     # English layout: Tk's own bindings handle it
        action = EDIT_KEYS.get(event.keycode)
        if action is None:
            return None
        event.widget.event_generate(action)
        return 'break'

    def do(widget, action):
        widget.focus_set()
        # CTkEntry clears its placeholder on <FocusIn>; let that run before pasting
        widget.after(30, lambda: widget.winfo_exists() and widget.event_generate(action))

    def on_right_click(event):
        widget = event.widget
        menu = tk.Menu(widget, tearoff=0, font=(T.FONT, 11), bg=T.SURFACE_2, fg=T.TEXT,
                       activebackground=T.PURPLE, activeforeground=T.ON_ACCENT, bd=0)
        for label, action in (('วาง', '<<Paste>>'), ('คัดลอก', '<<Copy>>'),
                              ('ตัด', '<<Cut>>'), ('เลือกทั้งหมด', '<<SelectAll>>')):
            menu.add_command(label=label, command=lambda a=action: do(widget, a))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return 'break'

    root.bind_class('Entry', '<Control-KeyPress>', on_ctrl_key, add='+')
    root.bind_class('Entry', '<Button-3>', on_right_click, add='+')


def watch_text(entry, callback):
    """Call back on every change to the text — typing, pasting with the mouse, anything.

    <KeyRelease> alone misses a right-click paste. The variable is seeded with what the
    entry shows now so CustomTkinter's placeholder stays visible; entry.get() still
    returns '' while the placeholder is up, so callers never filter by the hint text.
    """
    var = tk.StringVar(master=entry, value=entry._entry.get())
    entry._entry.configure(textvariable=var)
    var.trace_add('write', lambda *_: callback())
    entry._watch_var = var          # keep a reference or Tk drops the trace
    return var


def peek_theme():
    """(colour, mode) from config.json — needed before any widget exists.
    Configs from v1.1-v1.3 only have a single 'theme' name; translate that."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return T.DEFAULT, 'dark'
    if data.get('color'):
        return data['color'], data.get('mode', 'dark')
    return T.from_legacy(data.get('theme'))


# --------------------------------------------------------------------------- widgets
class ToggleGroup(ctk.CTkFrame):
    """Row of buttons where one is selected. Unlike CTkSegmentedButton each state gets
    its own text colour, so a selected neon-green segment can carry dark text."""

    def __init__(self, master, values, variable, command=None):
        super().__init__(master, fg_color='transparent')
        self.variable, self.command, self.buttons = variable, command, {}
        for i, value in enumerate(values):
            btn = ctk.CTkButton(self, text=value, height=36, corner_radius=9,
                                font=T.font(14, 'bold'), width=110,
                                command=lambda v=value: self.select(v))
            btn.pack(side='left', padx=(0 if i == 0 else 6, 0))
            self.buttons[value] = btn
        self._paint()

    def select(self, value):
        self.variable.set(value)
        self._paint()
        if self.command:
            self.command(value)

    def _paint(self):
        for value, btn in self.buttons.items():
            on = value == self.variable.get()
            btn.configure(fg_color=T.PURPLE if on else T.INPUT,
                          hover_color=T.PURPLE_DARK if on else T.SURFACE_3,
                          text_color=T.ON_ACCENT if on else T.TEXT_DIM)


class Card(ctk.CTkFrame):
    """A titled panel."""

    def __init__(self, master, title=None, **kw):
        super().__init__(master, fg_color=T.SURFACE, corner_radius=14,
                         border_width=1, border_color=T.BORDER, **kw)
        if title:
            ctk.CTkLabel(self, text=title, font=T.font(14, 'bold'),
                         text_color=T.TEXT_FAINT).pack(anchor='w', padx=18, pady=(14, 0))


class SoundRow(ctk.CTkFrame):
    """One sound: play button, name, hotkey badge, remove."""

    def __init__(self, master, app, index, data):
        super().__init__(master, fg_color=T.SURFACE_2, corner_radius=10, height=58)
        self.app, self.index, self.data = app, index, data
        self.pack_propagate(False)

        self.play = ctk.CTkButton(
            self, text='▶', width=38, height=38, corner_radius=19,
            font=T.font(15), fg_color=T.PURPLE, hover_color=T.PURPLE_DARK,
            text_color=T.ON_ACCENT, command=lambda: app.play_path(data['path']))
        self.play.pack(side='left', padx=(8, 12), pady=7)

        self.name = ctk.CTkLabel(self, text=data['name'], font=T.font(15), anchor='w',
                                 text_color=T.TEXT, justify='left')
        self.name.pack(side='left', fill='x', expand=True)

        ctk.CTkButton(self, text='✕', width=34, height=34, corner_radius=8,
                      font=T.font(14), fg_color='transparent', hover_color=T.DANGER,
                      text_color=T.TEXT_FAINT,
                      command=lambda: app.remove_sound(index)).pack(side='right', padx=(4, 8))

        vol = int(data.get('volume', 100))
        self.vol_chip = ctk.CTkButton(
            self, text=f'🔊 {vol}%', width=74, height=32, corner_radius=8, font=T.font(13),
            fg_color='transparent', hover_color=T.SURFACE_3, border_width=1,
            border_color=T.BORDER, text_color=T.TEXT if vol != 100 else T.TEXT_FAINT,
            command=lambda: app.edit_volume(index))
        self.vol_chip.pack(side='right', padx=(6, 0))
        # เลื่อนล้อเมาส์บนปุ่ม = ปรับทีละ 5%  (ไม่สร้าง slider ค้างไว้ทุกแถว ประหยัดแรม)
        self.vol_chip.bind('<MouseWheel>', lambda e: app.nudge_volume(index, 5 if e.delta > 0 else -5))

        slots = app.slots_using(data['path'])
        if slots:
            ctk.CTkLabel(self, text='slot ' + ', '.join(str(n) for n in slots), font=T.font(12),
                         text_color=T.PURPLE, width=70).pack(side='right', padx=4)
        else:
            ctk.CTkButton(self, text='＋ slot', width=70, height=32, corner_radius=8,
                          font=T.font(13), fg_color='transparent', hover_color=T.SURFACE_3,
                          border_width=1, border_color=T.BORDER, text_color=T.TEXT_DIM,
                          command=lambda: app.add_to_slot(data['path'])).pack(side='right', padx=4)

        for widget in (self, self.name):
            widget.bind('<Enter>', self._on_enter)
            widget.bind('<Leave>', self._on_leave)
        self.name.bind('<Double-1>', lambda e: app.rename_sound(index))

    def _on_enter(self, _=None):
        self.configure(fg_color=T.SURFACE_3)

    def _on_leave(self, _=None):
        self.configure(fg_color=T.SURFACE_2)

    def flash(self):
        self.play.configure(fg_color=T.PINK)
        self.after(400, lambda: self.play.configure(fg_color=T.PURPLE))


class SlotRow(ctk.CTkFrame):
    """One slot: number, hotkey, the sound it plays. Slot 1 is always random."""

    def __init__(self, master, app, number, slot=None):
        super().__init__(master, fg_color=T.SURFACE_2, corner_radius=10, height=58)
        self.pack_propagate(False)
        self.app, self.number, self.slot = app, number, slot
        is_random = slot is None
        spec = app.config_data.get('random_hotkey') if is_random else slot.get('hotkey')

        ctk.CTkLabel(self, text='🎲' if is_random else str(number), width=38, height=38,
                     corner_radius=19, font=T.font(15, 'bold'),
                     fg_color=T.PINK if is_random else T.INPUT,
                     text_color=T.ON_ACCENT if is_random else T.TEXT_DIM).pack(side='left', padx=(8, 10))

        self.badge = ctk.CTkButton(
            self, text=spec or '+ ตั้งปุ่ม', width=126, height=32, corner_radius=8,
            font=T.font(14, 'bold' if spec else 'normal'),
            fg_color=T.BLUE if spec else 'transparent',
            hover_color=T.BLUE_DARK if spec else T.SURFACE_3,
            border_width=0 if spec else 1, border_color=T.BORDER,
            text_color=T.ON_ACCENT if spec else T.TEXT_DIM,
            command=lambda: app.set_slot_hotkey(number))
        self.badge.pack(side='left', padx=(0, 10))
        self.badge.bind('<Button-3>', lambda e: app.clear_slot_hotkey(number))

        if is_random:
            n = len(app._rows())
            ctk.CTkLabel(self, text=f'สุ่มจากคลังเสียงทั้งหมด ({n} เสียง)', anchor='w',
                         font=T.font(15), text_color=T.TEXT).pack(side='left', fill='x', expand=True)
        else:
            sound = app.sound_by_path(slot.get('path'))
            self.pick = ctk.CTkButton(
                self, text=(sound['name'] if sound else '—  กดเพื่อเลือกเสียง  —'), anchor='w',
                height=36, corner_radius=8, font=T.font(15),
                fg_color='transparent', hover_color=T.SURFACE_3,
                text_color=T.TEXT if sound else T.TEXT_FAINT,
                command=lambda: app.pick_sound(number))
            self.pick.pack(side='left', fill='x', expand=True)

        ctk.CTkButton(self, text='✕', width=34, height=34, corner_radius=8, font=T.font(14),
                      fg_color='transparent', hover_color=T.DANGER, text_color=T.TEXT_FAINT,
                      state='disabled' if is_random else 'normal',
                      command=lambda: app.remove_slot(number)).pack(side='right', padx=(4, 8))
        self.play = ctk.CTkButton(
            self, text='▶', width=38, height=38, corner_radius=19, font=T.font(15),
            fg_color=T.PURPLE, hover_color=T.PURPLE_DARK, text_color=T.ON_ACCENT,
            command=lambda: app.play_slot(number))
        self.play.pack(side='right', padx=4)

    def flash(self):
        self.play.configure(fg_color=T.PINK)
        self.after(400, lambda: self.play.configure(fg_color=T.PURPLE))


class DeviceRow:
    """Label + dropdown + volume slider, laid out on one grid row."""

    def __init__(self, parent, row, icon, label, on_pick, on_volume, start=100, vmax=150):
        self.map = {}
        ctk.CTkLabel(parent, text=icon, font=T.font(19)).grid(row=row, column=0, padx=(18, 6), pady=7)
        ctk.CTkLabel(parent, text=label, font=T.font(14), text_color=T.TEXT_DIM,
                     anchor='w', width=148).grid(row=row, column=1, sticky='w', pady=7)

        self.var = ctk.StringVar(value=NONE_LABEL)
        self.menu = ctk.CTkOptionMenu(
            parent, variable=self.var, values=[NONE_LABEL], width=360, height=38,
            corner_radius=9, font=T.font(14), dropdown_font=T.font(14),
            fg_color=T.INPUT, button_color=T.INPUT, button_hover_color=T.SURFACE_3,
            dropdown_fg_color=T.SURFACE_2, dropdown_hover_color=T.SURFACE_3,
            text_color=T.TEXT, dropdown_text_color=T.TEXT, anchor='w',
            dynamic_resizing=False, command=lambda _v: on_pick())
        self.menu.grid(row=row, column=2, sticky='ew', padx=10, pady=7)

        self.vol = ctk.IntVar(value=start)
        self.slider = ctk.CTkSlider(
            parent, from_=0, to=vmax, variable=self.vol, width=142, height=18,
            corner_radius=8, button_corner_radius=8, button_length=0,
            fg_color=T.INPUT, progress_color=T.PINK,
            button_color=T.TEXT, button_hover_color=T.PURPLE, command=lambda _v: on_volume())
        self.slider.grid(row=row, column=3, padx=(4, 8), pady=7)
        self.pct = ctk.CTkLabel(parent, text=f'{start}%', font=T.font(13),
                                text_color=T.TEXT_FAINT, width=54)
        self.pct.grid(row=row, column=4, padx=(0, 14), pady=7)

    def set_options(self, pairs, include_none, keep):
        """pairs: [(index, full_label)]"""
        self.map = {T.short_device(lbl): idx for idx, lbl in pairs}
        self.full = {T.short_device(lbl): lbl for idx, lbl in pairs}
        values = ([NONE_LABEL] if include_none else []) + list(self.map)
        self.menu.configure(values=values or [NONE_LABEL])
        short_keep = T.short_device(keep) if keep else ''
        if short_keep in self.map:
            self.var.set(short_keep)
        elif not include_none and values:
            self.var.set(values[0])
        else:
            self.var.set(NONE_LABEL)

    def selected_index(self):
        return self.map.get(self.var.get())

    def selected_label(self):
        return self.full.get(self.var.get(), '')

    def set_by_index(self, idx):
        for short, i in self.map.items():
            if i == idx:
                self.var.set(short)
                return

    def refresh_pct(self):
        self.pct.configure(text=f'{int(self.vol.get())}%')


# --------------------------------------------------------------------------- app
class App(ctk.CTk):
    def __init__(self):
        T.apply(*peek_theme())
        ctk.set_appearance_mode(T.MODE)
        super().__init__(fg_color=T.BG)
        install_edit_keys(self)
        self.engine = ae.Engine()
        self.devices, self.inputs = [], []
        self.config_data = default_config()
        self.events = queue.Queue()
        self.capturing = False
        self.rows = []
        self.hotkey_error = ''
        try:
            self.hotkeys = hk.HotkeyManager()
        except Exception as exc:
            self.hotkeys, self.hotkey_error = None, str(exc)

        self.title('SoundSaoTer')
        self.geometry('1060x850')
        self.minsize(990, 730)
        for icon in (os.path.join(getattr(sys, '_MEIPASS', APP_DIR), 'icon.ico'),
                     os.path.join(APP_DIR, 'icon.ico')):
            if os.path.isfile(icon):
                try:
                    self.iconbitmap(icon)
                    self.after(250, lambda p=icon: self.iconbitmap(p))  # CTk resets it late
                except Exception:
                    pass
                break

        self._build()
        self.load_config()
        self.refresh_devices(initial=True)
        self.apply_hotkeys()
        if getattr(self, '_scanned', 0):
            self.say(f'พบไฟล์ใหม่ในโฟลเดอร์ sounds {self._scanned} ไฟล์ — เพิ่มเข้ารายการให้แล้ว', T.OK)
        self._start_tray()
        try:
            autostart.refresh()           # exe ถูกย้ายที่ -> ชี้ Run key ไปที่ใหม่
        except Exception:
            pass
        if autostart.started_hidden() and self.tray.available:
            self.withdraw()               # เปิดพร้อม Windows: ไปรอใน tray เงียบ ๆ
        updater.cleanup_old()
        self.after(2500, lambda: self.check_update(quiet=True))
        self.after(80, self._tick)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    # ------------------------------------------------------------ background
    def _start_tray(self):
        """pystray runs on its own thread, so every callback hops back to Tk via after()."""
        icon = next((p for p in (os.path.join(getattr(sys, '_MEIPASS', APP_DIR), 'icon.ico'),
                                 os.path.join(APP_DIR, 'icon.ico')) if os.path.isfile(p)), None)
        self.tray = tray_mod.Tray(
            icon,
            on_show=lambda: self.after(0, self.show_window),
            on_toggle=lambda: self.after(0, self.toggle_hotkeys),
            on_stop=lambda: self.after(0, self.stop_all),
            on_quit=lambda: self.after(0, self.quit_app),
            hotkeys_on=lambda: bool(self.config_data.get('hotkeys_enabled')),
        )
        if not self.tray.start():
            self.sw_tray.configure(state='disabled')
            self.sw_tray.var.set(False)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_window(self):
        self.withdraw()
        if not self.config_data.get('tray_notified'):
            self.config_data['tray_notified'] = True
            self.tray.notify('ยังทำงานอยู่เบื้องหลัง ฮอตคีย์ใช้ได้ตามปกติ\n'
                             'ดับเบิลคลิกไอคอนนี้เพื่อเปิดหน้าต่างกลับมา')
            self.save_config()

    def toggle_hotkeys(self):
        self.sw_hk.var.set(not self.sw_hk.var.get())
        self.apply_hotkeys()

    def apply_tray_option(self):
        self.config_data['tray'] = bool(self.sw_tray.var.get())
        self.save_config()

    def quit_app(self):
        self.on_close(force=True)

    # ---------------------------------------------------------------- layout
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---- header
        head = ctk.CTkFrame(self, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=22, pady=(20, 10))
        badge = ctk.CTkLabel(head, text='♪', font=T.font(26, 'bold'), text_color=T.ON_ACCENT,
                             fg_color=T.PURPLE, corner_radius=12, width=52, height=52)
        badge.pack(side='left')
        titles = ctk.CTkFrame(head, fg_color='transparent')
        titles.pack(side='left', padx=14)
        ctk.CTkLabel(titles, text='SoundSaoTer', font=T.font(23, 'bold'),
                     text_color=T.TEXT).pack(anchor='w')
        ctk.CTkLabel(titles, text=f'ส่งไฟล์เสียงเข้าไมค์ในเกม  ·  v{ver.VERSION}',
                     font=T.font(13), text_color=T.TEXT_FAINT).pack(anchor='w')

        light = T.MODE == 'light'
        ctk.CTkButton(head, text='🌙' if light else '☀', width=46, height=46, corner_radius=23,
                      font=T.font(20), fg_color=T.INPUT, hover_color=T.SURFACE_3,
                      text_color=T.TEXT, command=self.toggle_mode).pack(side='right')
        ctk.CTkLabel(head, text='โหมดมืด' if light else 'โหมดสว่าง', font=T.font(12),
                     text_color=T.TEXT_FAINT).pack(side='right', padx=(0, 8))

        # ---- devices card
        card = Card(self, 'อุปกรณ์เสียง')
        card.grid(row=1, column=0, sticky='ew', padx=22, pady=(0, 12))
        grid = ctk.CTkFrame(card, fg_color='transparent')
        grid.pack(fill='x', padx=0, pady=(8, 14))
        grid.grid_columnconfigure(2, weight=1)

        self.dev_game = DeviceRow(grid, 0, '🎮', 'เสียงเข้าเกม', self.apply_devices,
                                  self.apply_volumes, 100)
        self.dev_mon = DeviceRow(grid, 1, '🎧', 'ฟังเองที่หูฟัง', self.apply_devices,
                                 self.apply_volumes, 60)
        self.dev_mic = DeviceRow(grid, 2, '🎤', 'ไมค์จริง', self.apply_mic_pick,
                                 self.apply_volumes, 100, vmax=200)

        meter_row = ctk.CTkFrame(card, fg_color='transparent')
        meter_row.pack(fill='x', padx=18, pady=(0, 14))
        ctk.CTkLabel(meter_row, text='ระดับเสียงไมค์', font=T.font(13),
                     text_color=T.TEXT_FAINT).pack(side='left')
        self.meter = ctk.CTkProgressBar(meter_row, height=10, corner_radius=4,
                                        fg_color=T.INPUT, progress_color=T.OK)
        self.meter.pack(side='left', fill='x', expand=True, padx=12)
        self.meter.set(0)
        ctk.CTkButton(meter_row, text='รีเฟรชอุปกรณ์', width=128, height=34, corner_radius=8,
                      font=T.font(13), fg_color=T.INPUT, hover_color=T.SURFACE_3,
                      text_color=T.TEXT_DIM, command=self.refresh_devices).pack(side='left')
        ctk.CTkButton(meter_row, text='ทดสอบเสียง', width=118, height=34, corner_radius=8,
                      font=T.font(13), fg_color=T.INPUT, hover_color=T.SURFACE_3,
                      text_color=T.TEXT_DIM, command=self.test_tone).pack(side='left', padx=6)
        self.btn_micfx = ctk.CTkButton(meter_row, text=self._mic_fx_label(), width=150, height=34,
                                       corner_radius=8, font=T.font(13), fg_color=T.INPUT,
                                       hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
                                       command=self.open_mic_fx)
        self.btn_micfx.pack(side='left')

        self.var_hearself = ctk.BooleanVar(value=False)
        sw = ctk.CTkSwitch(meter_row, text='ฟังเสียงตัวเอง', variable=self.var_hearself,
                           command=self.apply_hear_self, font=T.font(13),
                           text_color=T.TEXT_DIM, progress_color=T.OK, fg_color=T.INPUT,
                           button_color=T.TEXT, button_hover_color=T.PINK,
                           width=40, switch_width=38, switch_height=18)
        sw.pack(side='left', padx=(14, 0))
        sw.var = self.var_hearself
        self.sw_hearself = sw

        # ---- toolbar
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.grid(row=2, column=0, sticky='nsew', padx=22)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(bar, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.sw_excl = self._switch(top, 'เล่นทีละเสียง', self.apply_options, True)
        self.sw_excl.pack_configure(padx=(0, 0))
        self.sw_hk = self._switch(top, 'ฮอตคีย์', self.apply_hotkeys, True)
        self.sw_mute = self._switch(top, 'ปิดไมค์', self.apply_mic_mute, False)
        self.sw_tray = self._switch(top, 'ย่อลง tray', self.apply_tray_option, True)

        ctk.CTkButton(top, text='■  หยุดเสียง', width=128, height=36, corner_radius=9,
                      font=T.font(14), fg_color=T.INPUT, hover_color=T.DANGER,
                      text_color=T.TEXT_DIM, command=self.stop_all).pack(side='right')
        self.stop_badge = ctk.CTkButton(
            top, text=self.config_data['stop_hotkey'], width=122, height=36, corner_radius=9,
            font=T.font(13, 'bold'), fg_color='transparent', border_width=1,
            border_color=T.BORDER, hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
            command=self.set_stop_hotkey)
        self.stop_badge.pack(side='right', padx=8)

        tabs = ctk.CTkFrame(bar, fg_color='transparent')
        tabs.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        self.view_var = ctk.StringVar(value='slots')
        self.view_tabs = ToggleGroup(tabs, ['🎯  ช่องคีย์ลัด', '📚  คลังเสียง'], self.view_var,
                                     command=lambda _v: self.switch_view())
        for b in self.view_tabs.buttons.values():
            b.configure(width=150)
        self.view_tabs.pack(side='left')
        self.search = ctk.CTkEntry(tabs, placeholder_text='ค้นหาเสียง...', width=270, height=38,
                                   corner_radius=9, font=T.font(14), fg_color=T.INPUT,
                                   border_color=T.BORDER, text_color=T.TEXT)
        self.search.pack(side='right')
        watch_text(self.search, self._schedule_redraw)

        # ---- list
        self.list = ctk.CTkScrollableFrame(
            bar, fg_color=T.SURFACE, corner_radius=14, border_width=1, border_color=T.BORDER,
            scrollbar_button_color=T.SURFACE_3, scrollbar_button_hover_color=T.PURPLE)
        self.list.grid(row=2, column=0, sticky='nsew')

        self.empty = ctk.CTkLabel(
            self.list, justify='center', font=T.font(15), text_color=T.TEXT_FAINT,
            text='ยังไม่มีเสียงในรายการ\n\nกด "เพิ่มไฟล์" หรือ "โหลดจากเว็บ" ด้านล่าง')

        # ---- footer
        foot = ctk.CTkFrame(self, fg_color='transparent')
        foot.grid(row=3, column=0, sticky='ew', padx=22, pady=(12, 6))
        for text, cmd, fill, hover in (
            ('+  เพิ่มไฟล์', self.add_files, T.PURPLE, T.PURPLE_DARK),
            ('+  เพิ่มทั้งโฟลเดอร์', self.add_folder, T.INPUT, T.SURFACE_3),
            ('⭳  โหลดเสียงจากเว็บ', self.open_downloader, T.PINK, T.PINK_DARK),
            ('✂  ตัดจาก YouTube', self.open_yt_clipper, T.BLUE, T.BLUE_DARK),
        ):
            ctk.CTkButton(foot, text=text, height=44, corner_radius=10, font=T.font(14, 'bold'),
                          fg_color=fill, hover_color=hover,
                          text_color=T.ON_ACCENT if fill != T.INPUT else T.TEXT,
                          command=cmd).pack(side='left', padx=(0, 10))

        bottom = ctk.CTkFrame(self, fg_color='transparent')
        bottom.grid(row=4, column=0, sticky='ew', padx=22, pady=(2, 12))
        self.dot = ctk.CTkLabel(bottom, text='●', font=T.font(15), text_color=T.WARN)
        self.dot.pack(side='right', padx=(6, 2))
        self.head_status = ctk.CTkLabel(bottom, text='', font=T.font(13), text_color=T.TEXT_DIM)
        self.head_status.pack(side='right')
        self.btn_update = ctk.CTkButton(
            bottom, text='ตรวจอัปเดต', width=108, height=30, corner_radius=8, font=T.font(12),
            fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
            command=lambda: self.check_update(quiet=False))
        self.btn_update.pack(side='right', padx=(0, 14))
        ctk.CTkButton(bottom, text='⚙  ตั้งค่า', width=96, height=30, corner_radius=8, font=T.font(12),
                      fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
                      command=self.open_settings).pack(side='right', padx=(0, 8))
        self.status = ctk.CTkLabel(bottom, text='', font=T.font(13), text_color=T.TEXT_FAINT,
                                   anchor='w')
        self.status.pack(side='left', fill='x', expand=True, padx=(4, 12))

    def _switch(self, parent, text, cmd, default):
        var = ctk.BooleanVar(value=default)
        sw = ctk.CTkSwitch(parent, text=text, variable=var, command=cmd, font=T.font(14),
                           text_color=T.TEXT_DIM, progress_color=T.PURPLE,
                           fg_color=T.INPUT, button_color=T.TEXT, button_hover_color=T.PINK,
                           width=40, switch_width=38, switch_height=18)
        sw.pack(side='left', padx=(16, 0))
        sw.var = var
        return sw

    def say(self, text, tone=None):
        # the status line shares its row with the buttons on the right; keep it one line
        short = text if len(text) <= 95 else text[:92].rstrip(' ,|') + '…'
        self.status.configure(text=short, text_color=tone or T.TEXT_FAINT)

    # ---------------------------------------------------------------- devices
    def refresh_devices(self, initial=False):
        try:
            import sounddevice as sd
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self.devices = ae.list_output_devices()
        self.inputs = ae.list_input_devices()

        self.dev_game.set_options(self.devices, False, self.config_data.get('game_device'))
        self.dev_mon.set_options(self.devices, True, self.config_data.get('monitor_device'))
        self.dev_mic.set_options(self.inputs, True, self.config_data.get('mic_device'))

        if initial and not self.config_data.get('game_device'):
            guess = ae.guess_cable_device(self.devices) or ae.default_output_device()
            if guess is not None:
                self.dev_game.set_by_index(guess)
        self.apply_devices()

    def apply_devices(self):
        problems = []
        for role, row in (('game', self.dev_game), ('monitor', self.dev_mon)):
            try:
                self.engine.set_device(role, row.selected_index())
            except Exception as exc:
                problems.append(f'{row.selected_label() or "?"}: {exc}')
                try:
                    self.engine.set_device(role, None)
                except Exception:
                    pass
        self.config_data['game_device'] = self.dev_game.selected_label()
        self.config_data['monitor_device'] = self.dev_mon.selected_label()
        problems += self._apply_mic()
        self.apply_volumes()
        self.save_config()
        self._update_banner(problems)

    def apply_mic_pick(self):
        problems = self._apply_mic()
        self.save_config()
        self._update_banner(problems)

    def _apply_mic(self):
        try:
            self.engine.set_mic(self.dev_mic.selected_index())
        except Exception as exc:
            try:
                self.engine.set_mic(None)
            except Exception:
                pass
            self.config_data['mic_device'] = self.dev_mic.selected_label()
            return [f'ไมค์: {exc}']
        self.apply_mic_mute()
        self.config_data['mic_device'] = self.dev_mic.selected_label()
        return []

    def _update_banner(self, problems):
        self._last_problems = list(problems)
        if problems:
            self.dot.configure(text_color=T.DANGER)
            self.head_status.configure(text='เปิดอุปกรณ์ไม่ได้', text_color=T.DANGER)
            self.say(' | '.join(problems), T.DANGER)
        elif ae.guess_cable_device(self.devices, verify=False) is None:
            self.dot.configure(text_color=T.WARN)
            self.head_status.configure(text='ยังไม่มี Virtual Cable', text_color=T.WARN)
            self.say('ต้องติดตั้ง VB-CABLE ก่อน เสียงถึงจะเข้าไมค์ในเกมได้ — ดูวิธีใน README', T.WARN)
        else:
            self.dot.configure(text_color=T.OK)
            self.head_status.configure(text='พร้อมใช้งาน', text_color=T.OK)
            self.say('อุปกรณ์เสียงเชื่อมต่อแล้ว')

    def apply_volumes(self):
        for row in (self.dev_game, self.dev_mon, self.dev_mic):
            row.refresh_pct()
        g, m, mic = (int(self.dev_game.vol.get()), int(self.dev_mon.vol.get()),
                     int(self.dev_mic.vol.get()))
        self.engine.gains['game'] = g / 100.0
        self.engine.gains['monitor'] = m / 100.0
        self.engine.set_mic_gain(mic / 100.0)
        self.config_data.update(game_volume=g, monitor_volume=m, mic_volume=mic)

    def apply_hear_self(self, save=True):
        on = self.engine.set_monitor_self(bool(self.var_hearself.get()))
        self.config_data['hear_self'] = on
        if on and not self.engine.can_monitor_self():
            self.say('ต้องตั้งช่อง "ฟังเองที่หูฟัง" และ "ไมค์จริง" ก่อน ถึงจะได้ยินตัวเอง', T.WARN)
        elif on:
            self.say('เปิดฟังเสียงตัวเองแล้ว — ใช้หูฟัง ถ้าใช้ลำโพงจะเกิดเสียงหอน', T.OK)
        else:
            self.say('ปิดฟังเสียงตัวเองแล้ว')
        if save:
            self.save_config()

    def apply_options(self):
        self.engine.exclusive = bool(self.sw_excl.var.get())
        self.config_data['exclusive'] = self.engine.exclusive
        self.save_config()

    def apply_mic_mute(self):
        muted = bool(self.sw_mute.var.get())
        if self.engine.mic is not None:
            self.engine.mic.muted = muted
            if muted:
                self.engine.mic.sink.clear_live()

    def test_tone(self):
        import numpy as np
        sr = 48000
        t = np.linspace(0, 0.8, int(sr * 0.8), endpoint=False, dtype=np.float32)
        env = np.minimum(1.0, np.minimum(t * 25, (0.8 - t) * 25)).astype(np.float32)
        tone = (0.25 * np.sin(2 * np.pi * 440 * t) * env).reshape(-1, 1)
        played = False
        for role in ('game', 'monitor'):
            player = self.engine.players.get(role)
            if player is None:
                continue
            data = ae._to_channels(ae._resample(tone, sr, player.samplerate), player.channels)
            player.play(data, gain=self.engine.gains.get(role, 1.0), exclusive=True)
            played = True
        self.say('ส่งเสียงทดสอบ 440Hz แล้ว' if played else 'ยังไม่ได้เลือกอุปกรณ์', T.WARN)

    # ---------------------------------------------------------------- sounds
    def _rows(self):
        return self.config_data['sounds']

    def _schedule_redraw(self):
        job = getattr(self, '_redraw_job', None)
        if job:
            self.after_cancel(job)
        self._redraw_job = self.after(150, self.redraw)

    def switch_view(self):
        self.config_data['view'] = 'library' if 'คลัง' in self.view_var.get() else 'slots'
        self.save_config()
        self.redraw()

    def _add_btn(self, text, cmd):
        btn = ctk.CTkButton(self.list, text=text, height=40, corner_radius=10, font=T.font(14),
                            fg_color='transparent', hover_color=T.SURFACE_3, border_width=1,
                            border_color=T.BORDER, text_color=T.TEXT_DIM, command=cmd)
        btn.pack(fill='x', padx=10, pady=(6, 10))
        self.rows.append(btn)

    def redraw(self):
        for row in self.rows:
            row.destroy()
        self.rows = []
        self.empty.pack_forget()
        needle = self.search.get().strip().lower()

        if self.config_data.get('view', 'slots') == 'slots':
            if not needle or 'สุ่ม' in needle:
                row = SlotRow(self.list, self, 1)
                row.pack(fill='x', padx=10, pady=4)
                self.rows.append(row)
            for n, slot in enumerate(self.config_data['slots'], start=2):
                sound = self.sound_by_path(slot.get('path'))
                if needle and needle not in (sound['name'].lower() if sound else '') \
                        and needle != (slot.get('hotkey') or '').lower():
                    continue
                row = SlotRow(self.list, self, n, slot)
                row.pack(fill='x', padx=10, pady=4)
                self.rows.append(row)
            if not needle:
                self._add_btn('＋  เพิ่ม slot', self.add_slot)
            return

        shown = 0
        for i, data in enumerate(self._rows()):
            if needle and needle not in data['name'].lower():
                continue
            if shown >= MAX_ROWS:
                break
            row = SoundRow(self.list, self, i, data)
            row.pack(fill='x', padx=10, pady=4)
            self.rows.append(row)
            shown += 1

        if not self._rows():
            self.empty.configure(text='ยังไม่มีเสียงในคลัง\n\nกด "เพิ่มไฟล์" หรือ "โหลดเสียงจากเว็บ" ด้านล่าง')
            self.empty.pack(pady=60)
        elif shown == 0:
            self.empty.configure(text=f'ไม่พบเสียงที่ตรงกับ "{self.search.get()}"')
            self.empty.pack(pady=60)

    # ---------------------------------------------------------------- slots
    def sound_by_path(self, path):
        if not path:
            return None
        key = self._key(path)
        return next((s for s in self._rows() if self._key(s['path']) == key), None)

    def slots_using(self, path):
        key = self._key(path)
        return [n for n, s in enumerate(self.config_data['slots'], start=2)
                if s.get('path') and self._key(s['path']) == key]

    def _slot(self, number):
        """number 2.. -> the slot dict (slot 1 is the random slot and has none)"""
        return self.config_data['slots'][number - 2]

    def _slots_changed(self, message=None, tone=None):
        self.save_config()
        self.apply_hotkeys()
        self.redraw()
        if message:
            self.say(message, tone or T.OK)

    def add_slot(self):
        self.config_data['slots'].append({'hotkey': '', 'path': ''})
        self._slots_changed()

    def remove_slot(self, number):
        slot = self._slot(number)
        del self.config_data['slots'][number - 2]
        self._slots_changed(f"ลบ slot {number}" + (f" ({slot['hotkey']})" if slot.get('hotkey') else ''))

    def add_to_slot(self, path):
        """From the library: drop the sound into the first empty slot, making one if needed."""
        slots = self.config_data['slots']
        free = next((i for i, s in enumerate(slots) if not s.get('path')), None)
        if free is None:
            slots.append({'hotkey': '', 'path': ''})
            free = len(slots) - 1
        slots[free]['path'] = path
        self._slots_changed(f'ใส่ลง slot {free + 2} แล้ว — ไปตั้งปุ่มได้ที่แท็บ "ช่องคีย์ลัด"')

    def play_slot(self, number):
        if number == 1:
            return self.play_random()
        sound = self.sound_by_path(self._slot(number).get('path'))
        if sound is None:
            return self.pick_sound(number)
        self.play_path(sound['path'])

    def _hotkey_owner(self, spec):
        if spec and self.config_data.get('random_hotkey') == spec:
            return 1
        for n, s in enumerate(self.config_data['slots'], start=2):
            if spec and s.get('hotkey') == spec:
                return n
        return None

    def set_slot_hotkey(self, number):
        def done(spec):
            if spec == self.config_data.get('stop_hotkey'):
                return self.say(f'{spec} ใช้เป็นปุ่มหยุดเสียงอยู่แล้ว', T.WARN)
            owner = self._hotkey_owner(spec)
            if owner and owner != number:          # ปุ่มเดียวกันใช้ได้ที่เดียว
                self._write_slot_hotkey(owner, '')
            self._write_slot_hotkey(number, spec)
            note = f' (ย้ายมาจาก slot {owner})' if owner and owner != number else ''
            self._slots_changed(f'slot {number} = {spec}{note}')

        self._capture(done)

    def clear_slot_hotkey(self, number):
        self._write_slot_hotkey(number, '')
        self._slots_changed(f'ล้างปุ่มของ slot {number} แล้ว')

    def _write_slot_hotkey(self, number, spec):
        if number == 1:
            self.config_data['random_hotkey'] = spec
        else:
            self._slot(number)['hotkey'] = spec

    def _migrate_slots(self):
        """Up to v1.2 hotkeys lived on the sounds; move them into slots, in key order."""
        sounds = self._rows()
        if not self.config_data.get('slots'):
            keyed = [s for s in sounds if s.get('hotkey')]
            natural = lambda s: [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', s['hotkey'])]
            self.config_data['slots'] = [{'hotkey': s['hotkey'], 'path': s['path']}
                                         for s in sorted(keyed, key=natural)]
            while len(self.config_data['slots']) < DEFAULT_SLOTS:
                self.config_data['slots'].append({'hotkey': '', 'path': ''})
        for s in sounds:
            s.pop('hotkey', None)
        for slot in self.config_data['slots']:
            slot.setdefault('hotkey', '')
            slot.setdefault('path', '')
            if slot['path'] and self.sound_by_path(slot['path']) is None:
                slot['path'] = ''                # ไฟล์หายไปแล้ว เก็บปุ่มไว้ ล้างแค่เสียง

    # ---------------------------------------------------------------- sound picker
    @staticmethod
    def _source_of(sound):
        stem = sound['name']
        if re.match(r'^\d+ - ', stem):
            return 'Dota'
        if re.search(r'\[td\d+\]$', stem):
            return 'TiengDong'
        if re.search(r'\[yt[\w-]+-\d+\]$', stem):
            return 'YouTube'
        if re.search(r'\[\d+\]$', stem):
            return 'Myinstants'
        return 'ของฉัน'

    def pick_sound(self, number):
        slot = self._slot(number)
        win = self._dialog(f'เลือกเสียงให้ slot {number}', 700, 640, modal=False)
        ctk.CTkLabel(win, text=f'เลือกเสียงให้ slot {number}' +
                     (f"  ·  {slot['hotkey']}" if slot.get('hotkey') else ''),
                     font=T.font(17, 'bold'), text_color=T.TEXT).pack(pady=(18, 10))

        entry = ctk.CTkEntry(win, placeholder_text='พิมพ์ชื่อเสียง… (Enter = เลือกอันแรก)', height=40,
                             corner_radius=9, font=T.font(14), fg_color=T.INPUT,
                             border_color=T.BORDER, text_color=T.TEXT)
        entry.pack(fill='x', padx=20)

        chips = ctk.CTkFrame(win, fg_color='transparent')
        chips.pack(fill='x', padx=20, pady=(10, 6))
        src_var = ctk.StringVar(value='ทั้งหมด')
        free_var = ctk.BooleanVar(value=False)
        group = ToggleGroup(chips, ['ทั้งหมด', 'Dota', 'Myinstants', 'TiengDong', 'YouTube', 'ของฉัน'],
                            src_var, command=lambda _v: schedule())
        for b in group.buttons.values():
            b.configure(width=72, height=32)
        group.pack(side='left')
        ctk.CTkCheckBox(chips, text='ซ่อนที่อยู่ใน slot แล้ว', variable=free_var,
                        command=lambda: schedule(), font=T.font(12), text_color=T.TEXT_DIM,
                        fg_color=T.PURPLE, hover_color=T.PURPLE_DARK, border_color=T.BORDER,
                        checkmark_color=T.ON_ACCENT).pack(side='right')

        results = ctk.CTkScrollableFrame(win, fg_color=T.SURFACE, corner_radius=12,
                                         border_width=1, border_color=T.BORDER)
        results.pack(fill='both', expand=True, padx=20, pady=(4, 8))
        note = ctk.CTkLabel(win, text='', font=T.font(12), text_color=T.TEXT_FAINT)
        note.pack(pady=(0, 12))
        state = {'job': None, 'hits': []}

        def matches():
            needle = entry.get().strip().lower()
            want = src_var.get()
            used = {self._key(s['path']) for s in self.config_data['slots'] if s.get('path')}
            out = []
            for s in self._rows():
                if needle and needle not in s['name'].lower():
                    continue
                if want != 'ทั้งหมด' and self._source_of(s) != want:
                    continue
                if free_var.get() and self._key(s['path']) in used:
                    continue
                out.append(s)
            return out

        def choose(sound):
            slot['path'] = sound['path']
            win.destroy()
            self._slots_changed(f"slot {number} = {sound['name'][:40]}")

        def render():
            state['job'] = None
            for w in results.winfo_children():
                w.destroy()
            hits = matches()
            state['hits'] = hits
            current = self._key(slot['path']) if slot.get('path') else None
            for s in hits[:PICKER_ROWS]:
                mine = current == self._key(s['path'])
                row = ctk.CTkFrame(results, fg_color=T.SURFACE_3 if mine else T.SURFACE_2,
                                   corner_radius=9, height=44)
                row.pack(fill='x', padx=6, pady=3)
                row.pack_propagate(False)
                ctk.CTkButton(row, text='▶', width=34, height=30, corner_radius=8, font=T.font(12),
                              fg_color='transparent', border_width=1, border_color=T.BORDER,
                              hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
                              command=lambda p=s['path']: self._preview_file(p)).pack(side='left', padx=(8, 6))
                ctk.CTkButton(row, text=('✓  ' if mine else '') + s['name'], anchor='w', height=34,
                              corner_radius=8, font=T.font(14), fg_color='transparent',
                              hover_color=T.SURFACE_3, text_color=T.TEXT,
                              command=lambda snd=s: choose(snd)).pack(side='left', fill='x', expand=True)
                tag = self.slots_using(s['path'])
                ctk.CTkLabel(row, text=(f"slot {', '.join(map(str, tag))}" if tag else self._source_of(s)),
                             width=90, font=T.font(11), text_color=T.PURPLE if tag else T.TEXT_FAINT
                             ).pack(side='right', padx=8)
            more = len(hits) - PICKER_ROWS
            note.configure(text=(f'พบ {len(hits)} เสียง' + (f' — แสดง {PICKER_ROWS} แรก พิมพ์เพิ่มเพื่อกรอง'
                                                            if more > 0 else '') + '   ·   ▶ = ฟังทางหูฟัง ไม่เข้าเกม')
                           if hits else 'ไม่พบเสียงที่ตรงกับตัวกรอง')

        def schedule(*_):
            # รอให้พิมพ์จบก่อนค่อยวาดใหม่ พิมพ์รัว ๆ จะได้ไม่หน่วง
            if state['job']:
                win.after_cancel(state['job'])
            state['job'] = win.after(180, render)

        watch_text(entry, schedule)
        entry.bind('<Return>', lambda e: choose(state['hits'][0]) if state['hits'] else None)
        render()
        win.after(200, entry.focus_set)

    def _preview_file(self, path):
        try:
            where = self.engine.preview(path)
            self.say(f'ฟังตัวอย่างทาง {where} (ไม่เข้าเกม)', T.OK)
        except Exception as exc:
            self.say(str(exc), T.WARN)

    @staticmethod
    def _key(path):
        """Compare paths the way Windows does — 'a/b' and 'A\\B' are the same file."""
        return os.path.normcase(os.path.abspath(path))

    def _append(self, path, known):
        if not path.lower().endswith(AUDIO_EXT):
            return False
        key = self._key(path)
        if key in known or not os.path.isfile(path):
            return False
        self._rows().append({'name': os.path.splitext(os.path.basename(path))[0],
                             'path': path, 'hotkey': ''})
        known.add(key)
        return True

    def scan_sounds_folder(self):
        """Anything sitting in sounds/ should just show up, no import step needed."""
        if not os.path.isdir(SOUNDS_DIR):
            return 0
        known = {self._key(s['path']) for s in self._rows()}
        added = 0
        for name in sorted(os.listdir(SOUNDS_DIR)):
            added += bool(self._append(os.path.join(SOUNDS_DIR, name), known))
        return added

    def add_paths(self, paths):
        known = {self._key(s['path']) for s in self._rows()}
        added = sum(bool(self._append(p, known)) for p in paths)
        self.redraw()
        self.save_config()
        self.say(f'เพิ่ม {added} ไฟล์' if added else 'ไม่มีไฟล์ใหม่ (ซ้ำกับที่มีอยู่แล้ว)',
                 T.OK if added else T.WARN)
        return added

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title='เลือกไฟล์เสียง', initialdir=SOUNDS_DIR if os.path.isdir(SOUNDS_DIR) else APP_DIR,
            filetypes=[('Audio', '*.wav *.mp3 *.ogg *.flac *.aiff'), ('All files', '*.*')])
        if paths:
            self.add_paths(list(paths))

    def add_folder(self):
        folder = filedialog.askdirectory(
            title='เลือกโฟลเดอร์เสียง',
            initialdir=SOUNDS_DIR if os.path.isdir(SOUNDS_DIR) else APP_DIR)
        if not folder:
            return
        found = []
        for base, _dirs, files in os.walk(folder):
            found += [os.path.join(base, f) for f in sorted(files) if f.lower().endswith(AUDIO_EXT)]
        self.add_paths(found)

    def remove_sound(self, index):
        if 0 <= index < len(self._rows()):
            name = self._rows()[index]['name']
            gone = self._key(self._rows()[index]['path'])
            for slot in self.config_data['slots']:
                if slot.get('path') and self._key(slot['path']) == gone:
                    slot['path'] = ''
            del self._rows()[index]
            self.redraw()
            self.apply_hotkeys()
            self.save_config()
            self.say(f'ลบ "{name}" แล้ว')

    def rename_sound(self, index):
        row = self._rows()[index]
        win = self._dialog('เปลี่ยนชื่อ', 480, 215)
        ctk.CTkLabel(win, text='ชื่อที่จะแสดงในรายการ', font=T.font(14),
                     text_color=T.TEXT_DIM).pack(pady=(24, 8))
        entry = ctk.CTkEntry(win, width=370, height=40, font=T.font(15), corner_radius=9,
                             fg_color=T.INPUT, border_color=T.BORDER, text_color=T.TEXT)
        entry.insert(0, row['name'])
        entry.pack()
        entry.focus_set()

        def ok():
            row['name'] = entry.get().strip() or row['name']
            win.destroy()
            self.redraw()
            self.save_config()

        ctk.CTkButton(win, text='บันทึก', width=150, height=38, corner_radius=9,
                      font=T.font(14, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PURPLE, hover_color=T.PURPLE_DARK,
                      command=ok).pack(pady=20)
        win.bind('<Return>', lambda e: ok())

    def _cooldown_left(self):
        gap = float(self.config_data.get('cooldown') or 0)
        return gap - (time.monotonic() - getattr(self, '_last_hotkey_play', -1e9)) if gap > 0 else 0

    def _sound_volume(self, path):
        key = self._key(path)
        for s in self._rows():
            if self._key(s['path']) == key:
                return max(0, int(s.get('volume', 100))) / 100.0
        return 1.0

    def play_path(self, path, from_hotkey=False):
        """from_hotkey: triggered in-game, so the anti-spam gap applies. Clicks in the
        window are deliberate and never blocked."""
        if from_hotkey:
            left = self._cooldown_left()
            if left > 0:
                return self.say(f'กันสแปม: รออีก {left:.1f} วินาที', T.WARN)
            self._last_hotkey_play = time.monotonic()
        try:
            self.engine.play(path, gain=self._sound_volume(path))
            self.say(f'กำลังเล่น: {os.path.basename(path)}', T.OK)
            for row in self.rows:
                if row.data['path'] == path:
                    row.flash()
        except Exception as exc:
            self.say(f'เล่นไม่ได้: {exc}', T.DANGER)

    def play_random(self, from_hotkey=False):
        pool = [s['path'] for s in self._rows()]
        if not pool:
            return self.say('ยังไม่มีเสียงให้สุ่ม', T.WARN)
        last = getattr(self, '_last_random', None)
        if len(pool) > 1 and last in pool:
            pool.remove(last)                # ไม่สุ่มซ้ำตัวเดิมติดกัน
        pick = random.choice(pool)
        self._last_random = pick
        self.play_path(pick, from_hotkey=from_hotkey)

    # ---------------------------------------------------------------- per-sound volume
    def _set_volume(self, index, value):
        value = int(max(0, min(200, round(value))))
        row = self._rows()[index]
        row['volume'] = value
        for r in self.rows:
            if r.index == index:
                r.vol_chip.configure(text=f'🔊 {value}%',
                                     text_color=T.TEXT if value != 100 else T.TEXT_FAINT)
        return value

    def nudge_volume(self, index, step):
        value = self._set_volume(index, int(self._rows()[index].get('volume', 100)) + step)
        self.say(f"{self._rows()[index]['name'][:40]}: ดัง {value}%")
        self.save_config()

    def edit_volume(self, index):
        row = self._rows()[index]
        win = self._dialog('ดังเบาของเสียงนี้', 460, 230, modal=False)
        ctk.CTkLabel(win, text=row['name'][:48], font=T.font(14, 'bold'),
                     text_color=T.TEXT).pack(pady=(22, 10), padx=20)
        var = tk.DoubleVar(value=int(row.get('volume', 100)))
        pct = ctk.CTkLabel(win, text=f"{int(var.get())}%", font=T.font(22, 'bold'), text_color=T.PURPLE)

        def moved(v):
            pct.configure(text=f'{self._set_volume(index, float(v))}%')

        ctk.CTkSlider(win, from_=0, to=200, number_of_steps=40, variable=var, width=360,
                      progress_color=T.PINK, button_color=T.TEXT, button_hover_color=T.PURPLE,
                      fg_color=T.INPUT, command=moved).pack()
        pct.pack(pady=(8, 4))
        bar = ctk.CTkFrame(win, fg_color='transparent')
        bar.pack(pady=(4, 16))
        ctk.CTkButton(bar, text='▶ ลองฟัง', width=110, height=34, corner_radius=9,
                      font=T.font(13, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PURPLE,
                      hover_color=T.PURPLE_DARK,
                      command=lambda: self.play_path(row['path'])).pack(side='left', padx=5)
        ctk.CTkButton(bar, text='100%', width=80, height=34, corner_radius=9, font=T.font(13),
                      fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
                      command=lambda: (var.set(100), moved(100))).pack(side='left', padx=5)
        win.protocol('WM_DELETE_WINDOW', lambda: (self.save_config(), win.destroy()))

    def stop_all(self):
        self.engine.stop()
        self.say('หยุดเสียงแล้ว')

    # ---------------------------------------------------------------- hotkeys
    @staticmethod
    def _present(win, tries=8):
        """CustomTkinter withdraws a new CTkToplevel to repaint its title bar and can
        leave it unmapped, so keep asking until the window is really on screen."""
        try:
            if not win.winfo_exists():
                return
            win.deiconify()
            win.lift()
            win.focus_force()
            if win.winfo_viewable():
                return
        except Exception:
            return
        if tries > 0:
            win.after(120, lambda: App._present(win, tries - 1))

    @staticmethod
    def _safe_grab(win, tries=12):
        """grab_set() throws while the window is still unmapped, and a half-applied grab
        swallows every click on the main window — which looks exactly like a freeze."""
        try:
            if not win.winfo_exists():
                return
            if win.winfo_viewable():
                win.grab_set()
                return
        except Exception:
            return
        if tries > 0:
            win.after(100, lambda: App._safe_grab(win, tries - 1))

    def _dialog(self, title, w, h, modal=True):
        win = ctk.CTkToplevel(self, fg_color=T.BG)
        win.title(title)
        win.geometry(f'{w}x{h}')
        win.resizable(False, False)
        win.transient(self)
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 3
        win.geometry(f'+{max(0, x)}+{max(0, y)}')
        win.after(10, lambda: App._present(win))
        if modal:
            win.after(260, lambda: App._safe_grab(win))
        return win

    def report_callback_exception(self, exc, value, tb):
        """Never let a callback error escape into a missing stderr."""
        try:
            self.say(f'เกิดข้อผิดพลาด: {value}', T.DANGER)
        except Exception:
            pass

    def _capture(self, on_done):
        if self.hotkeys is None:
            messagebox.showerror('ฮอตคีย์', f'ระบบฮอตคีย์เริ่มไม่สำเร็จ\n{self.hotkey_error}')
            return
        if self.capturing:
            return
        self.capturing = True
        win = self._dialog('ตั้งฮอตคีย์', 460, 250)
        ctk.CTkLabel(win, text='⌨', font=T.font(38), text_color=T.PURPLE).pack(pady=(28, 6))
        ctk.CTkLabel(win, text='กดปุ่มหรือคอมโบที่ต้องการ', font=T.font(16, 'bold'),
                     text_color=T.TEXT).pack()
        ctk.CTkLabel(win, text='แนะนำ numpad หรือ ctrl+alt+…  •  Esc = ยกเลิก',
                     font=T.font(13), text_color=T.TEXT_FAINT).pack(pady=(6, 0))

        def finish(spec):
            if not self.capturing:
                return
            self.capturing = False
            try:
                win.destroy()
            except Exception:
                pass
            if spec:
                on_done(spec)

        def on_key(event):
            if event.keycode in hk.VK_MODIFIERS:
                return 'break'
            finish(None if event.keycode == 0x1B
                   else hk.format_spec(hk.current_modifiers(), event.keycode))
            return 'break'

        win.bind('<KeyPress>', on_key)
        win.protocol('WM_DELETE_WINDOW', lambda: finish(None))
        win.after(150, win.focus_force)

    def set_stop_hotkey(self):
        def done(spec):
            owner = self._hotkey_owner(spec)
            if owner:
                return self.say(f'{spec} ใช้กับ slot {owner} อยู่ — ล้างที่ slot ก่อน', T.WARN)
            self.config_data['stop_hotkey'] = spec
            self.stop_badge.configure(text=spec)
            self.apply_hotkeys()
            self.save_config()

        self._capture(done)

    def apply_hotkeys(self):
        self.config_data['hotkeys_enabled'] = bool(self.sw_hk.var.get())
        if self.hotkeys is None:
            return self.say(f'ฮอตคีย์ใช้ไม่ได้: {self.hotkey_error}', T.DANGER)
        if not self.config_data['hotkeys_enabled']:
            self.hotkeys.set_all([])
            self.save_config()
            return self.say('ปิดฮอตคีย์ชั่วคราวแล้ว', T.WARN)

        entries, specs, bad = [], [], []
        wanted = []
        for slot in self.config_data['slots']:
            sound = self.sound_by_path(slot.get('path'))
            if slot.get('hotkey') and sound is not None:
                wanted.append((slot['hotkey'], partial(self._fire, sound['path'])))
        wanted.append((self.config_data.get('stop_hotkey'), self._fire_stop))
        wanted.append((self.config_data.get('random_hotkey'), self._fire_random))
        for spec, callback in wanted:
            if not spec:
                continue
            parsed = hk.parse_spec(spec)
            if parsed is None:
                bad.append(spec)
                continue
            entries.append((parsed[0], parsed[1], callback))
            specs.append(spec)

        failed = [specs[i] for i in self.hotkeys.set_all(entries) if i < len(specs)]
        self.save_config()
        if failed or bad:
            parts = []
            if failed:
                parts.append('ปุ่มถูกโปรแกรมอื่นจองอยู่: ' + ', '.join(failed))
            if bad:
                parts.append('อ่านปุ่มไม่ออก: ' + ', '.join(bad))
            self.say(' | '.join(parts), T.DANGER)
        else:
            self.say(f'ฮอตคีย์พร้อมใช้งาน ({len(entries)} ปุ่ม)', T.OK)

    def _fire(self, path):
        self.events.put(('play', path))

    def _fire_stop(self):
        self.events.put(('stop',))

    def _fire_random(self):
        self.events.put(('random',))

    def _tick(self):
        try:
            while True:
                evt = self.events.get_nowait()
                if evt[0] == 'play':
                    self.play_path(evt[1], from_hotkey=True)
                elif evt[0] == 'random':
                    self.play_random(from_hotkey=True)
                elif evt[0] == 'stop':
                    self.stop_all()
        except queue.Empty:
            pass
        try:
            self.meter.set(min(1.0, self.engine.mic_peak() * 1.4))
        except Exception:
            pass
        self.after(80, self._tick)

    # ---------------------------------------------------------------- mic voice processing
    def _mic_fx_label(self):
        fx = self.engine.micfx
        on = [name for name, flag in (('ตัดเสียงรบกวน', fx.denoise), ('ตัดตอนไม่พูด', fx.gate),
                                      ('ปรับดังอัตโนมัติ', fx.agc), ('ตัดเสียงสะท้อน', fx.aec)) if flag]
        return '🎚  ปรับเสียงไมค์' + (f' · เปิด {len(on)}' if on else '')

    def _refresh_mic_fx_button(self):
        btn = getattr(self, 'btn_micfx', None)
        if btn is not None and btn.winfo_exists():
            active = self.engine.micfx.active
            btn.configure(text=self._mic_fx_label(), fg_color=T.PURPLE if active else T.INPUT,
                          text_color=T.ON_ACCENT if active else T.TEXT_DIM)

    def open_mic_fx(self):
        existing = getattr(self, '_fx_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        import numpy as np
        import micfx
        fx = self.engine.micfx
        cfg = self.config_data
        win = self._dialog('ปรับเสียงไมค์', 600, 820, modal=False)
        self._fx_win = win

        ctk.CTkLabel(win, text='🎚  ปรับเสียงไมค์', font=T.font(18, 'bold'),
                     text_color=T.TEXT).pack(anchor='w', padx=24, pady=(18, 0))
        ctk.CTkLabel(win, text='ทำกับไมค์จริงก่อนส่งเข้าเกม — แบบเดียวกับ Voice Processing ของ Discord · เปลี่ยนแล้วมีผลทันที',
                     font=T.font(12), text_color=T.TEXT_FAINT).pack(anchor='w', padx=24, pady=(2, 12))

        def card(title, desc, value, command):
            box = ctk.CTkFrame(win, fg_color=T.SURFACE, corner_radius=12, border_width=1, border_color=T.BORDER)
            box.pack(fill='x', padx=20, pady=5)
            head = ctk.CTkFrame(box, fg_color='transparent')
            head.pack(fill='x', padx=16, pady=(12, 2))
            ctk.CTkLabel(head, text=title, font=T.font(15, 'bold'), text_color=T.TEXT).pack(side='left')
            var = ctk.BooleanVar(value=value)
            ctk.CTkSwitch(head, text='', variable=var, command=command, width=44, switch_width=44,
                          switch_height=22, progress_color=T.PURPLE, fg_color=T.INPUT,
                          button_color=T.TEXT, button_hover_color=T.PINK).pack(side='right')
            ctk.CTkLabel(box, text=desc, font=T.font(12), text_color=T.TEXT_DIM, justify='left',
                         anchor='w', wraplength=520).pack(fill='x', padx=16, pady=(0, 12))
            return box, var

        ai_ok = micfx.available()
        _box, var_dn = card('ตัดเสียงรบกวน (AI)',
                            'ตัดเสียงพัดลม แอร์ คีย์บอร์ด เสียงรอบห้อง ให้เพื่อนได้ยินแต่เสียงพูด\n'
                            'ใช้ RNNoise · แรมเพิ่ม ~12 MB เฉพาะตอนเปิด · หน่วงเพิ่ม ~20 ms · ถ้าเปิดตัดเสียงสะท้อนด้วย '
                            'จะสลับไปใช้ตัวตัดของ WebRTC (เบากว่า) ไม่ให้เสียงพูดหายตอนพูดทับเสียงลำโพง',
                            fx.denoise, lambda: changed())
        gate_box, var_gate = card('ตัดไมค์ตอนไม่ได้พูด',
                                  'ไมค์เงียบสนิทระหว่างที่ไม่ได้พูด — ใช้ AI ฟังว่าเป็นเสียงคน '
                                  'เสียงกดคีย์บอร์ดหรือเสียงประตูจะไม่ทำให้ไมค์เปิด',
                                  fx.gate, lambda: changed())
        sens_row = ctk.CTkFrame(gate_box, fg_color='transparent')
        sens_row.pack(fill='x', padx=16, pady=(0, 12))
        ctk.CTkLabel(sens_row, text='ความไว', font=T.font(12), text_color=T.TEXT_DIM).pack(side='left')
        ctk.CTkLabel(sens_row, text='ต้องพูดชัด', font=T.font(11), text_color=T.TEXT_FAINT).pack(side='left', padx=(10, 6))
        sens = ctk.CTkSlider(sens_row, from_=0, to=100, number_of_steps=20, height=16,
                             button_color=T.PURPLE, button_hover_color=T.PINK, progress_color=T.PURPLE,
                             fg_color=T.INPUT, command=lambda v: sens_changed(v))
        sens.set(fx.sensitivity)
        sens.pack(side='left', fill='x', expand=True)
        ctk.CTkLabel(sens_row, text='เปิดง่าย', font=T.font(11), text_color=T.TEXT_FAINT).pack(side='left', padx=(6, 0))
        _box, var_agc = card('ปรับความดังอัตโนมัติ',
                             'พูดเบาก็ดังขึ้น ตะโกนก็ไม่แตก — ปรับเฉพาะตอนพูด ไม่ดันเสียงรบกวนขึ้นมา · '
                             'ได้ผลดีสุดเมื่อเปิด "ตัดเสียงรบกวน" ด้วย',
                             fx.agc, lambda: changed())
        aec_box, var_aec = card('ตัดเสียงสะท้อนจากลำโพง',
                                'ใช้ลำโพงแทนหูฟัง แล้วเพื่อนได้ยินเสียงเกม/เสียงเพื่อนย้อนกลับ — ตัวนี้ฟังว่าลำโพงเล่นอะไรอยู่ '
                                'แล้วลบเสียงนั้นออกจากไมค์ (WebRTC AEC3 แบบเดียวกับ Chrome/Discord) · '
                                'แรมเพิ่ม ~20 MB ตั้งแต่เปิดครั้งแรกจนปิดโปรแกรม · ใช้หูฟังอยู่ไม่ต้องเปิด',
                                fx.aec, lambda: changed())
        spk_row = ctk.CTkFrame(aec_box, fg_color='transparent')
        spk_row.pack(fill='x', padx=16, pady=(0, 12))
        ctk.CTkLabel(spk_row, text='ลำโพงที่ใช้', font=T.font(12), text_color=T.TEXT_DIM).pack(side='left')
        AUTO = 'อัตโนมัติ (ลำโพงหลักของ Windows)'
        try:
            speakers = micfx.speaker_names()
        except Exception:
            speakers = []
        spk_var = ctk.StringVar(value=fx.aec_device if fx.aec_device in speakers else AUTO)
        spk_menu = ctk.CTkOptionMenu(
            spk_row, variable=spk_var, values=[AUTO] + speakers, height=30, corner_radius=8,
            font=T.font(12), dropdown_font=T.font(12), fg_color=T.INPUT, button_color=T.INPUT,
            button_hover_color=T.SURFACE_3, dropdown_fg_color=T.SURFACE_2, dropdown_hover_color=T.SURFACE_3,
            text_color=T.TEXT, dropdown_text_color=T.TEXT, dynamic_resizing=False, width=330,
            command=lambda _v: speaker_changed())
        spk_menu.pack(side='left', padx=(10, 0))
        spk_used = ctk.CTkLabel(aec_box, text='', font=T.font(11), text_color=T.TEXT_FAINT, anchor='w')
        spk_used.pack(fill='x', padx=16, pady=(0, 10))

        # ---- live meters
        live = ctk.CTkFrame(win, fg_color=T.SURFACE_2, corner_radius=12)
        live.pack(fill='x', padx=20, pady=(10, 4))

        def meter(label):
            row = ctk.CTkFrame(live, fg_color='transparent')
            row.pack(fill='x', padx=16, pady=(10, 0))
            ctk.CTkLabel(row, text=label, width=110, anchor='w', font=T.font(12),
                         text_color=T.TEXT_DIM).pack(side='left')
            bar = ctk.CTkProgressBar(row, height=10, corner_radius=4, fg_color=T.INPUT, progress_color=T.OK)
            bar.pack(side='left', fill='x', expand=True)
            bar.set(0)
            return bar

        m_in = meter('เสียงจากไมค์')
        m_out = meter('เพื่อนได้ยิน')
        foot = ctk.CTkFrame(live, fg_color='transparent')
        foot.pack(fill='x', padx=16, pady=(8, 12))
        talk = ctk.CTkLabel(foot, text='', font=T.font(13, 'bold'), text_color=T.TEXT_FAINT)
        talk.pack(side='left')
        ctk.CTkSwitch(foot, text='ฟังเสียงตัวเอง (ลองดูผล)', variable=self.var_hearself,
                      command=self.apply_hear_self, font=T.font(12), text_color=T.TEXT_DIM,
                      progress_color=T.OK, fg_color=T.INPUT, button_color=T.TEXT,
                      button_hover_color=T.PINK, width=40, switch_width=38, switch_height=18).pack(side='right')

        note = ctk.CTkLabel(win, text='', font=T.font(12), text_color=T.TEXT_FAINT, justify='left',
                            anchor='w', wraplength=540)
        note.pack(fill='x', padx=24, pady=(8, 14))
        default_note = 'ใช้หูฟังอยู่ไม่ต้องเปิดตัดเสียงสะท้อน — เปิดเฉพาะตอนเล่นกับลำโพง'

        def set_note(text=None, tone=None):
            note.configure(text=text or default_note, text_color=tone or T.TEXT_FAINT)

        def sync():
            var_dn.set(fx.denoise)
            var_gate.set(fx.gate)
            var_agc.set(fx.agc)
            var_aec.set(fx.aec)
            state = 'normal' if fx.gate else 'disabled'
            sens.configure(state=state, button_color=T.PURPLE if fx.gate else T.SURFACE_3)
            spk_used.configure(text=f'กำลังฟังจาก: {fx.aec_name}' if fx.aec and fx.aec_name else '')
            self._refresh_mic_fx_button()

        def changed():
            if (var_dn.get() or var_gate.get()) and not ai_ok:
                var_dn.set(False)
                var_gate.set(False)
                set_note('ไม่พบไฟล์ตัวตัดเสียงรบกวน (rnnoise.dll) — ลองโหลดโปรแกรมใหม่', T.DANGER)
            self.engine.set_mic_fx(denoise=var_dn.get(), gate=var_gate.get(), agc=var_agc.get(),
                                   aec=var_aec.get())
            if fx.error:
                set_note(fx.error, T.DANGER)
            elif self.engine.mic is None and fx.active:
                set_note('ยังไม่ได้เลือก "ไมค์จริง" ในหน้าหลัก — ตั้งแล้วจะเริ่มทำงานเอง', T.WARN)
            elif fx.aec and self.var_hearself.get():
                set_note('ตอนเปิดตัดเสียงสะท้อน แนะนำปิด "ฟังเสียงตัวเอง" — เสียงตัวเองที่ออกลำโพง '
                         'จะไปกวนตัวตัดเสียงสะท้อน', T.WARN)
            else:
                set_note()
            cfg.update(mic_denoise=fx.denoise, mic_gate=fx.gate, mic_agc=fx.agc, mic_aec=fx.aec)
            self.save_config()
            sync()

        def speaker_changed():
            choice = spk_var.get()
            device = '' if choice == AUTO else choice
            self.engine.set_mic_fx(aec_device=device)
            cfg['mic_aec_device'] = device
            self.save_config()
            sync()

        def sens_changed(value):
            self.engine.set_mic_fx(sensitivity=value)
            cfg['mic_gate_sens'] = int(value)
            job = getattr(win, '_save_job', None)
            if job:
                win.after_cancel(job)
            win._save_job = win.after(500, self.save_config)

        def as_bar(level):
            db = 20 * np.log10(max(level, 1e-6))
            return min(1.0, max(0.0, (db + 60) / 60))      # -60 dBFS .. 0 dBFS

        def tick():
            if not win.winfo_exists():
                return
            mic = self.engine.mic
            if mic is None:
                m_in.set(0)
                m_out.set(0)
                talk.configure(text='ยังไม่ได้เลือกไมค์จริง', text_color=T.WARN)
            else:
                if fx.active:
                    lin, lout = fx.level_in, fx.level_out
                    fx.level_in *= 0.7
                    fx.level_out *= 0.7
                else:
                    lin = lout = mic.peak
                m_in.set(as_bar(lin))
                m_out.set(as_bar(0 if mic.muted else lout))
                if mic.muted:
                    talk.configure(text='ปิดไมค์อยู่', text_color=T.WARN)
                elif fx.gate:
                    talk.configure(text='● กำลังพูด' if fx.speaking else '○ ไมค์ปิดรอเสียงพูด',
                                   text_color=T.OK if fx.speaking else T.TEXT_FAINT)
                else:
                    talk.configure(text='')
                if fx.aec_error and note.cget('text') != fx.aec_error:
                    set_note(fx.aec_error, T.DANGER)
            win.after(60, tick)

        sync()
        set_note(fx.error or None, T.DANGER if fx.error else None)
        tick()

    # ---------------------------------------------------------------- YouTube clipper
    def open_yt_clipper(self):
        existing = getattr(self, '_yt_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        import numpy as np
        import soundfile as sf
        from PIL import Image, ImageTk
        import ytclip as Y

        Y.clean_work_dir()
        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        R = Y.RATE
        PAD_L = int(46 * scale)                  # room for the Hz labels
        PW = int(854 * scale)                    # plot width
        PH = int(300 * scale)                    # plot height
        RULER = int(24 * scale)
        OVH = int(46 * scale)
        CW = PAD_L + PW + int(6 * scale)
        LENGTHS = {'15 วิ': 15, '30 วิ': 30, '60 วิ': 60}
        MODES = ('ความถี่', 'คลื่นเสียง')

        win = self._dialog('ตัดเสียงจาก YouTube', 960, 790, modal=False)
        self._yt_win = win
        st = {'info': None, 'start': 0.0, 'data': None, 'len': 0.0, 'ref': None, 'peak': 1.0,
              'view': [0.0, 1.0], 'sel': [0.0, 0.0], 'busy': False, 'env': None, 'env_state': '',
              'base': None, 'dimmed': None, 'photo': None, 'ov_photo': None, 'drag': None,
              'pan': None, 'job': None, 'play': None}
        lut = Y.make_lut([T.INPUT, T.PURPLE_DARK, T.PURPLE, T.PINK, T.TEXT])

        ctk.CTkLabel(win, text='✂  ตัดเสียงจาก YouTube', font=T.font(18, 'bold'),
                     text_color=T.TEXT).pack(anchor='w', padx=24, pady=(16, 8))

        head = ctk.CTkFrame(win, fg_color='transparent')
        head.pack(fill='x', padx=24)
        url_ent = ctk.CTkEntry(head, placeholder_text='วางลิงก์ YouTube แล้วกด Enter  (ลิงก์ที่มีเวลา &t= จะเปิดตรงนั้นเลย)',
                               height=42, corner_radius=9, font=T.font(14), fg_color=T.INPUT,
                               border_color=T.BORDER, text_color=T.TEXT)
        url_ent.pack(side='left', fill='x', expand=True)
        open_btn = ctk.CTkButton(head, text='เปิดคลิป', width=110, height=42, corner_radius=9,
                                 font=T.font(14, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PINK,
                                 hover_color=T.PINK_DARK, command=lambda: open_clip())
        open_btn.pack(side='left', padx=(10, 0))

        info_row = ctk.CTkFrame(win, fg_color='transparent')
        info_row.pack(fill='x', padx=24, pady=(10, 4))
        info_lbl = ctk.CTkLabel(info_row, text='ยังไม่ได้เปิดคลิป', font=T.font(13), text_color=T.TEXT_DIM,
                                anchor='w')
        info_lbl.pack(side='left', fill='x', expand=True)
        len_var = ctk.StringVar(value='30 วิ')
        ToggleGroup(info_row, list(LENGTHS), len_var).pack(side='right')
        ctk.CTkLabel(info_row, text='โหลดทีละ', font=T.font(12),
                     text_color=T.TEXT_FAINT).pack(side='right', padx=(0, 8))

        ov = tk.Canvas(win, width=CW, height=OVH, bg=T.INPUT, highlightthickness=0, cursor='hand2')
        ov.pack(padx=24, pady=(2, 8))

        tools = ctk.CTkFrame(win, fg_color='transparent')
        tools.pack(fill='x', padx=24, pady=(0, 6))
        mode_var = ctk.StringVar(value=MODES[0])
        ToggleGroup(tools, list(MODES), mode_var, command=lambda _v: render()).pack(side='left')

        def tool_btn(text, cmd, width=44):
            b = ctk.CTkButton(tools, text=text, width=width, height=32, corner_radius=8, font=T.font(13),
                              fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT, command=cmd)
            b.pack(side='right', padx=(6, 0))
            return b

        tool_btn('ดูทั้งช่วง', lambda: set_view(0, st['len']), 84)
        tool_btn('ซูมที่เลือก', lambda: zoom_to_sel(), 90)
        tool_btn('＋', lambda: zoom(0.5))
        tool_btn('－', lambda: zoom(2.0))
        next_btn = tool_btn('ช่วงถัดไป ▶', lambda: shift_window(+1), 100)
        prev_btn = tool_btn('◀ ช่วงก่อน', lambda: shift_window(-1), 96)

        canvas = tk.Canvas(win, width=CW, height=PH + RULER, bg=T.BG, highlightthickness=0, cursor='crosshair')
        canvas.pack(padx=24)
        img_item = canvas.create_image(PAD_L, 0, anchor='nw')
        canvas.create_rectangle(PAD_L, 0, PAD_L + PW, PH, outline=T.BORDER, tags='frame')

        sel_row = ctk.CTkFrame(win, fg_color='transparent')
        sel_row.pack(fill='x', padx=24, pady=(10, 4))

        def time_box(label, which):
            ctk.CTkLabel(sel_row, text=label, font=T.font(13), text_color=T.TEXT_DIM).pack(side='left', padx=(0, 6))
            ent = ctk.CTkEntry(sel_row, width=120, height=34, corner_radius=8, font=T.font(14),
                               fg_color=T.INPUT, border_color=T.BORDER, text_color=T.TEXT, justify='center')
            ent.pack(side='left')
            for text, step in (('−', -0.01), ('+', 0.01)):
                ctk.CTkButton(sel_row, text=text, width=32, height=34, corner_radius=8, font=T.font(15),
                              fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT,
                              command=lambda w=which, s=step: nudge(w, s)).pack(side='left', padx=(4, 0))
            ent.bind('<Return>', lambda _e: commit(which))
            ent.bind('<FocusOut>', lambda _e: commit(which))
            return ent

        start_ent = time_box('เริ่ม', 0)
        ctk.CTkLabel(sel_row, text='', width=18).pack(side='left')
        end_ent = time_box('จบ', 1)
        len_lbl = ctk.CTkLabel(sel_row, text='', font=T.font(13, 'bold'), text_color=T.PINK)
        len_lbl.pack(side='right')

        act = ctk.CTkFrame(win, fg_color='transparent')
        act.pack(fill='x', padx=24, pady=(8, 4))
        ctk.CTkButton(act, text='▶  ฟังช่วงที่เลือก', width=150, height=40, corner_radius=9,
                      font=T.font(14, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PURPLE,
                      hover_color=T.PURPLE_DARK, command=lambda: play_sel()).pack(side='left')
        ctk.CTkButton(act, text='■', width=44, height=40, corner_radius=9, font=T.font(14),
                      fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT,
                      command=lambda: stop_play()).pack(side='left', padx=(8, 0))
        save_btn = ctk.CTkButton(act, text='บันทึกลงคลังเสียง', width=160, height=40, corner_radius=9,
                                 font=T.font(14, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PINK,
                                 hover_color=T.PINK_DARK, command=lambda: save())
        save_btn.pack(side='right')
        name_ent = ctk.CTkEntry(act, placeholder_text='ตั้งชื่อเสียง', height=40, corner_radius=9,
                                font=T.font(14), fg_color=T.INPUT, border_color=T.BORDER, text_color=T.TEXT)
        name_ent.pack(side='left', fill='x', expand=True, padx=(16, 10))

        HINT = ('ลากบนภาพ = เลือกช่วง  ·  ลากเส้นชมพู = ปรับขอบ  ·  ล้อเมาส์ = ซูม  ·  '
                'คลิกขวาลาก / Shift+ล้อ = เลื่อน  ·  Space = ฟัง')
        note = ctk.CTkLabel(win, text='วางลิงก์คลิปแล้วกด "เปิดคลิป"', font=T.font(12),
                            text_color=T.TEXT_FAINT, anchor='w')
        note.pack(fill='x', padx=26, pady=(6, 12))

        def set_note(text, tone=None):
            if win.winfo_exists():
                note.configure(text=text, text_color=tone or T.TEXT_FAINT)

        # ---------------------------------------------------- coordinates
        def t_to_x(t):
            v0, v1 = st['view']
            return PAD_L + (t - v0) / (v1 - v0) * PW

        def x_to_t(x):
            v0, v1 = st['view']
            return min(max(v0 + (x - PAD_L) / PW * (v1 - v0), 0.0), st['len'])

        # ---------------------------------------------------- drawing
        def schedule():
            if st['job']:
                win.after_cancel(st['job'])
            st['job'] = win.after(16, render)

        def render():
            st['job'] = None
            if st['data'] is None or not win.winfo_exists():
                return
            v0, v1 = st['view']
            if mode_var.get() == MODES[0]:
                if st['ref'] is None:                       # brightness scale from the whole window
                    _img, st['ref'] = Y.spectrogram_rgb(st['data'], R, 0, st['len'], 300, 60, lut)
                rgb, _ref = Y.spectrogram_rgb(st['data'], R, v0, v1, PW, PH, lut, st['ref'])
            else:
                rgb = Y.waveform_rgb(st['data'], R, v0, v1, PW, PH, T.INPUT, T.PURPLE, T.BORDER, st['peak'])
            st['base'], st['dimmed'] = rgb, Y.dim(rgb, T.INPUT)
            refresh()

        def refresh():
            compose()
            overlay()
            sync_entries()

        def compose():
            base, dimmed = st['base'], st['dimmed']
            if base is None:
                return
            x0 = int(round(min(max(t_to_x(st['sel'][0]) - PAD_L, 0), PW)))
            x1 = int(round(min(max(t_to_x(st['sel'][1]) - PAD_L, 0), PW)))
            img = dimmed.copy()
            if x1 > x0:
                img[:, x0:x1] = base[:, x0:x1]
            st['photo'] = ImageTk.PhotoImage(Image.fromarray(img), master=canvas)
            canvas.itemconfigure(img_item, image=st['photo'])

        def nice_step(span):
            for step in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30):
                if span / step <= 8:
                    return step
            return 60

        def overlay():
            canvas.delete('ov')
            small = ('Segoe UI', max(8, int(9 * scale)))
            if mode_var.get() == MODES[0]:
                for f, label in ((100, '100'), (300, '300'), (1000, '1k'), (3000, '3k'), (10000, '10k')):
                    y = Y.freq_to_y(f, PH)
                    canvas.create_line(PAD_L - 5, y, PAD_L, y, fill=T.TEXT_FAINT, tags='ov')
                    canvas.create_text(PAD_L - 8, y, text=label, anchor='e', fill=T.TEXT_FAINT, font=small, tags='ov')
                canvas.create_text(PAD_L - 8, 2, text='Hz', anchor='ne', fill=T.TEXT_FAINT, font=small, tags='ov')
            v0, v1 = st['view']
            step = nice_step(v1 - v0)
            t = np.ceil(v0 / step) * step
            edge = 26 * scale                          # labels this close to an end would be cut off
            while t <= v1 + 1e-9:
                x = t_to_x(t)
                canvas.create_line(x, PH, x, PH + 5, fill=T.TEXT_FAINT, tags='ov')
                if not PAD_L + edge <= x <= PAD_L + PW - edge:
                    t += step
                    continue
                label = Y.fmt_time(st['start'] + t)
                if step >= 1:
                    label = label.split('.')[0]
                elif step >= 0.1:
                    label = label[:-2]
                elif step >= 0.01:
                    label = label[:-1]
                canvas.create_text(x, PH + 7, text=label, anchor='n', fill=T.TEXT_DIM, font=small, tags='ov')
                t += step
            for t in st['sel']:
                x = t_to_x(t)
                if PAD_L - 1 <= x <= PAD_L + PW + 1:
                    canvas.create_line(x, 0, x, PH, fill=T.PINK, width=2, tags='ov')
                    s = 6 * scale
                    canvas.create_polygon(x - s, 0, x + s, 0, x, s * 1.4, fill=T.PINK, outline='', tags='ov')
                    canvas.create_polygon(x - s, PH, x + s, PH, x, PH - s * 1.4, fill=T.PINK, outline='', tags='ov')
            canvas.tag_raise('frame')
            draw_overview()

        def draw_overview():
            ov.delete('all')
            info = st['info']
            if not info:
                return
            dur = info['duration'] or max(st['start'] + st['len'], 1.0)
            if st['env']:
                st['ov_photo'] = ImageTk.PhotoImage(
                    Image.fromarray(Y.envelope_rgb(st['env'], PW, OVH, T.INPUT, T.PURPLE_DARK)), master=ov)
                ov.create_image(PAD_L, 0, anchor='nw', image=st['ov_photo'])
            else:
                ov.create_text(PAD_L + PW / 2, OVH / 2, text=st['env_state'], fill=T.TEXT_FAINT,
                               font=('Segoe UI', max(8, int(10 * scale))))
            small = ('Segoe UI', max(8, int(9 * scale)))
            ov.create_text(PAD_L - 8, OVH / 2, text='ทั้งคลิป', anchor='e', fill=T.TEXT_FAINT, font=small)
            ox = lambda t: PAD_L + t / dur * PW
            ov.create_rectangle(ox(st['start']), 1, max(ox(st['start'] + st['len']), ox(st['start']) + 3),
                                OVH - 1, outline=T.PINK, width=2)
            a, b = (st['start'] + t for t in st['sel'])
            ov.create_rectangle(ox(a), 4, max(ox(b), ox(a) + 2), OVH - 4, fill=T.PINK, outline='')

        def sync_entries():
            focus = win.focus_get()
            for ent, t in ((start_ent, st['sel'][0]), (end_ent, st['sel'][1])):
                if focus is not ent._entry:
                    ent.delete(0, 'end')
                    ent.insert(0, Y.fmt_time(st['start'] + t))
            a, b = st['sel']
            len_lbl.configure(text=f'ยาว {b - a:.3f} วินาที')

        # ---------------------------------------------------- view
        def set_view(a, b):
            if st['data'] is None:
                return
            span = min(max(b - a, 0.02), st['len'])
            a = min(max(a, 0.0), st['len'] - span)
            st['view'] = [a, a + span]
            schedule()

        def zoom(factor, at=None):
            v0, v1 = st['view']
            span = v1 - v0
            at = (v0 + v1) / 2 if at is None else at
            new = min(max(span * factor, 0.02), st['len'])
            set_view(at - (at - v0) * new / span, at - (at - v0) * new / span + new)

        def zoom_to_sel():
            a, b = st['sel']
            pad = max((b - a) * 0.15, 0.01)
            set_view(a - pad, b + pad)

        def ensure_visible(t):
            v0, v1 = st['view']
            if not v0 <= t <= v1:
                span = v1 - v0
                set_view(t - span / 2, t + span / 2)

        # ---------------------------------------------------- mouse
        def near(x, t):
            return abs(x - t_to_x(t)) <= 7 * scale

        def press(e):
            if st['data'] is None or e.y > PH:
                return
            s0, s1 = st['sel']
            if near(e.x, s1):
                st['drag'] = ['edge', 1]
            elif near(e.x, s0):
                st['drag'] = ['edge', 0]
            else:
                t = x_to_t(e.x)
                st['drag'] = ['new', t, list(st['sel'])]
                st['sel'] = [t, t]
            refresh()

        def motion(e):
            d = st['drag']
            if not d:
                return
            t = x_to_t(e.x)
            if d[0] == 'edge':
                st['sel'][d[1]] = t
                if st['sel'][0] > st['sel'][1]:
                    st['sel'].reverse()
                    d[1] = 1 - d[1]
            else:
                st['sel'] = [min(d[1], t), max(d[1], t)]
            refresh()

        def release(_e):
            d, st['drag'] = st['drag'], None
            if d and d[0] == 'new' and st['sel'][1] - st['sel'][0] < 0.005:
                st['sel'] = d[2]                     # a plain click keeps the old selection
                refresh()

        def hover(e):
            if st['data'] is not None and e.y <= PH and (near(e.x, st['sel'][0]) or near(e.x, st['sel'][1])):
                canvas.configure(cursor='sb_h_double_arrow')
            else:
                canvas.configure(cursor='crosshair')

        def wheel(e):
            if st['data'] is None:
                return
            v0, v1 = st['view']
            if e.state & 0x0001:                     # Shift: pan
                shift = -np.sign(e.delta) * 0.15 * (v1 - v0)
                set_view(v0 + shift, v1 + shift)
            else:
                zoom(0.8 if e.delta > 0 else 1.25, x_to_t(e.x) if PAD_L <= e.x <= PAD_L + PW else None)

        def pan_start(e):
            st['pan'] = (e.x, list(st['view']))

        def pan_move(e):
            if not st['pan'] or st['data'] is None:
                return
            x0, (v0, v1) = st['pan']
            dt = -(e.x - x0) / PW * (v1 - v0)
            set_view(v0 + dt, v1 + dt)

        canvas.bind('<ButtonPress-1>', press)
        canvas.bind('<B1-Motion>', motion)
        canvas.bind('<ButtonRelease-1>', release)
        canvas.bind('<Motion>', hover)
        canvas.bind('<MouseWheel>', wheel)
        canvas.bind('<ButtonPress-3>', pan_start)
        canvas.bind('<B3-Motion>', pan_move)

        def ov_click(e):
            info = st['info']
            if not info or st['busy']:
                return
            dur = info['duration'] or 0
            if dur <= 0:
                return
            t = min(max((e.x - PAD_L) / PW * dur, 0.0), dur)
            length = LENGTHS[len_var.get()]
            start = max(0.0, min(t - length / 2, dur - length))
            load('window', start, [t - start, t - start + 1.0])

        ov.bind('<ButtonPress-1>', ov_click)

        # ---------------------------------------------------- selection by numbers
        def put_sel(which, t):
            t = min(max(t, 0.0), st['len'])
            st['sel'][which] = t
            if st['sel'][0] > st['sel'][1]:
                st['sel'][1 - which] = t
            ensure_visible(t)
            refresh()

        def nudge(which, step):
            if st['data'] is not None:
                put_sel(which, st['sel'][which] + step)

        def commit(which):
            if st['data'] is None:
                return
            ent = (start_ent, end_ent)[which]
            value = Y.parse_time(ent.get())
            if value is None:
                set_note('พิมพ์เวลาแบบ 1:23.456 หรือ 83.456', T.WARN)
                return sync_entries()
            t = value - st['start']
            if 0 <= t <= st['len']:
                return put_sel(which, t)
            # outside what is loaded: fetch the window around that time, keep the selection length
            if st['busy']:
                return sync_entries()
            span = max(st['sel'][1] - st['sel'][0], 1.0)
            start = max(0.0, value - 1.0) if which == 0 else max(0.0, value - span - 1.0)
            hint = [value - start, value - start + span] if which == 0 else [value - span - start, value - start]
            load('window', start, hint)

        # ---------------------------------------------------- loading
        def busy(on, text=None, tone=None):
            st['busy'] = on
            for b in (open_btn, prev_btn, next_btn, save_btn):
                b.configure(state='disabled' if on else 'normal')
            if text:
                set_note(text, tone or (T.WARN if on else T.TEXT_FAINT))

        def load(op, start, hint=None, url=None):
            if st['busy']:
                return
            req = {'op': op, 'start': start, 'length': LENGTHS[len_var.get()]}
            if op == 'open':
                req['url'] = url
            else:
                req['info'] = st['info']
            busy(True, 'กำลังเปิดคลิป… (ราว 3-5 วินาที)' if op == 'open' else
                 f'กำลังโหลดช่วง {Y.fmt_time(start)[:-4]} …')

            def worker():
                try:
                    res = Y.run(req)
                    data, _sr = sf.read(res['wav'], dtype='float32')
                    Y._remove(res['wav'])
                    self.after(0, lambda: loaded(op, res, data, hint))
                except Exception as exc:
                    self.after(0, lambda: failed(exc))

            threading.Thread(target=worker, daemon=True).start()

        def loaded(op, res, data, hint):
            if not win.winfo_exists():
                return
            busy(False, HINT)
            if len(data) < R * 0.05:
                return set_note('ช่วงนี้ไม่มีเสียง ลองช่วงอื่น', T.WARN)
            first = op == 'open' or st['info'] is None or st['info']['id'] != res['info']['id']
            st.update(info=res['info'], start=res['start'], data=data, len=len(data) / R, ref=None,
                      peak=max(float(np.abs(data).max()), 1e-4))
            st['view'] = [0.0, st['len']]
            a, b = hint or (0.0, min(2.0, st['len']))
            a = min(max(a, 0.0), st['len'])
            st['sel'] = [a, min(max(b, a + 0.05), st['len'])]
            dur = res['info']['duration']
            info_lbl.configure(text=f"{res['info']['title'][:60]}   ·   ยาว {Y.fmt_time(dur).split('.')[0] if dur else '?'}")
            if first:
                name_ent.delete(0, 'end')
                name_ent.insert(0, src.clean(res['info']['title'], 40))
                st['env'] = None
                if 0 < dur <= Y.OVERVIEW_MAX_S:
                    st['env_state'] = 'กำลังโหลดภาพรวมทั้งคลิป…'
                    load_overview(res['info'])
                else:
                    st['env_state'] = 'คลิปยาวเกิน 20 นาที — ไม่แสดงภาพรวม (พิมพ์เวลาในช่อง "เริ่ม" เพื่อไปช่วงอื่น)'
            render()

        def failed(exc):
            if win.winfo_exists():
                busy(False, f'ไม่สำเร็จ: {exc}', T.DANGER)

        def load_overview(info):
            clip_id = info['id']

            def worker():
                try:
                    env = Y.run({'op': 'overview', 'info': info})['env']
                except Exception:
                    env = None
                self.after(0, lambda: got(env))

            def got(env):
                if not win.winfo_exists() or not st['info'] or st['info']['id'] != clip_id:
                    return
                st['env'] = env
                st['env_state'] = '' if env else 'โหลดภาพรวมไม่ได้ — ใช้ ◀ ▶ หรือพิมพ์เวลาแทน'
                draw_overview()

            threading.Thread(target=worker, daemon=True).start()

        def open_clip(*_):
            url = url_ent.get().strip()
            if not url.lower().startswith('http'):
                return set_note('วางลิงก์คลิป YouTube ก่อน (ขึ้นต้นด้วย https://)', T.WARN)
            stop_play()
            st['info'] = None
            start = Y.start_from_url(url)
            load('open', max(0.0, start - 1.0) if start else 0.0,
                 [1.0, 3.0] if start else None, url=url)

        def shift_window(direction):
            if st['info'] is None or st['busy']:
                return
            length = LENGTHS[len_var.get()]
            start = max(0.0, st['start'] + direction * length * 0.75)
            dur = st['info']['duration']
            if dur:
                start = min(start, max(0.0, dur - length))
            if abs(start - st['start']) < 0.01:
                return set_note('สุดคลิปแล้ว', T.WARN)
            load('window', start)

        url_ent.bind('<Return>', open_clip)

        # ---------------------------------------------------- listen & save
        def selection():
            a, b = st['sel']
            return Y.fade(st['data'][int(a * R):int(b * R)], R)

        def play_sel():
            if st['data'] is None:
                return
            a, b = st['sel']
            if b - a < 0.02:
                return set_note('เลือกช่วงก่อน — ลากบนภาพ', T.WARN)
            try:
                where = self.engine.preview_data(selection(), R)
            except Exception as exc:
                return set_note(str(exc), T.WARN)
            st['play'] = (time.monotonic(), a, b)
            set_note(f'กำลังฟังทาง {where} (ไม่เข้าเกม)', T.OK)
            tick_play()

        def tick_play():
            if not win.winfo_exists():
                return
            canvas.delete('ph')
            p = st['play']
            if not p:
                return
            t = p[1] + time.monotonic() - p[0]
            if t >= p[2]:
                st['play'] = None
                return
            x = t_to_x(t)
            if PAD_L <= x <= PAD_L + PW:
                canvas.create_line(x, 0, x, PH, fill=T.OK, width=2, tags='ph')
            win.after(30, tick_play)

        def stop_play():
            if st.get('play'):
                self.engine.stop()
            st['play'] = None
            if win.winfo_exists():
                canvas.delete('ph')

        def save():
            if st['data'] is None:
                return set_note('เปิดคลิปก่อน', T.WARN)
            a, b = st['sel']
            if b - a < 0.05:
                return set_note('ช่วงที่เลือกสั้นเกินไป', T.WARN)
            name = src.clean(name_ent.get().strip(), 60) or 'youtube clip'
            clip_id = re.sub(r'[^\w-]', '', st['info']['id'])[:24] or 'clip'
            ms = int(round((st['start'] + a) * 1000))
            os.makedirs(SOUNDS_DIR, exist_ok=True)
            path = os.path.join(SOUNDS_DIR, f'{name} [yt{clip_id}-{ms}].mp3')
            try:
                sf.write(path, selection(), R, format='MP3', subtype='MPEG_LAYER_III')
            except Exception as exc:
                return set_note(f'บันทึกไม่ได้: {exc}', T.DANGER)
            self.add_paths([path])
            set_note(f'บันทึกแล้ว: {os.path.basename(path)}  ({b - a:.2f} วิ) — ไปใส่ slot ได้เลย', T.OK)

        def on_space(e):
            if not isinstance(e.widget, tk.Entry):
                play_sel()

        win.bind('<space>', on_space)

        def close():
            Y.kill_all()
            stop_play()
            st.clear()
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', close)
        win.after(200, url_ent.focus_set)

    # ---------------------------------------------------------------- downloader
    def open_downloader(self):
        existing = getattr(self, '_dl_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        # deliberately not modal: nothing here needs to block the main window,
        # and a stuck grab is indistinguishable from a hang
        win = self._dialog('โหลดเสียงจากเว็บ', 820, 640, modal=False)
        win.resizable(True, True)
        self._dl_win = win

        picker = ctk.CTkFrame(win, fg_color='transparent')
        picker.pack(fill='x', padx=20, pady=(18, 0))
        ctk.CTkLabel(picker, text='เลือกเว็บ', font=T.font(13),
                     text_color=T.TEXT_FAINT).pack(side='left', padx=(2, 12))
        site_var = ctk.StringVar(value=src.SOURCES[0].label)
        ToggleGroup(picker, [s.label for s in src.SOURCES], site_var,
                    command=lambda _v: on_site()).pack(side='left')

        head = ctk.CTkFrame(win, fg_color='transparent')
        head.pack(fill='x', padx=20, pady=(12, 10))
        entry = ctk.CTkEntry(head, placeholder_text=src.SOURCES[0].hint,
                             height=42, corner_radius=9, font=T.font(14),
                             fg_color=T.INPUT, border_color=T.BORDER, text_color=T.TEXT)
        entry.pack(side='left', fill='x', expand=True)
        entry.focus_set()

        cat_var = ctk.StringVar(value='')
        cat_menu = ctk.CTkOptionMenu(
            head, variable=cat_var, values=['-'], width=170, height=42, corner_radius=9,
            font=T.font(13), dropdown_font=T.font(13), fg_color=T.INPUT, button_color=T.INPUT,
            button_hover_color=T.SURFACE_3, dropdown_fg_color=T.SURFACE_2,
            dropdown_hover_color=T.SURFACE_3, text_color=T.TEXT, dropdown_text_color=T.TEXT,
            dynamic_resizing=False, command=lambda _v: search())
        web_btn = ctk.CTkButton(
            head, text='🌐', width=44, height=42, corner_radius=9, font=T.font(16),
            fg_color=T.INPUT, hover_color=T.SURFACE_3, text_color=T.TEXT_DIM,
            command=lambda: open_in_browser())

        def current_source():
            return src.BY_LABEL[site_var.get()]

        def has_categories(source):
            return bool(getattr(source, 'CATEGORIES', None))

        def open_in_browser():
            source = current_source()
            webbrowser.open(source.search_url(entry.get().strip()))
            set_note('เปิดหน้าค้นหาในเบราว์เซอร์แล้ว — เจอเสียงที่ชอบ ก๊อปลิงก์หน้าเสียงมาวางที่ช่องนี้ได้')

        def on_site():
            source = current_source()
            entry.configure(placeholder_text=source.hint)
            clear()
            if has_categories(source):
                names = [c[0] for c in source.CATEGORIES]
                cat_menu.configure(values=names)
                cat_var.set(source.category if source.category in names else names[0])
                search_btn.pack_forget()
                cat_menu.pack(side='left', padx=(10, 0))
                web_btn.pack(side='left', padx=(8, 0))
                search()                               # เปิดมาก็เห็นเสียงเลย
            else:
                cat_menu.pack_forget()
                web_btn.pack_forget()
                search_btn.pack(side='left', padx=(10, 0))
                set_note(f'ค้นหาใน {source.label} — {source.hint}')

        results = ctk.CTkScrollableFrame(win, fg_color=T.SURFACE, corner_radius=12,
                                         border_width=1, border_color=T.BORDER)
        results.pack(fill='both', expand=True, padx=20, pady=(0, 10))
        foot = ctk.CTkFrame(win, fg_color='transparent')
        foot.pack(fill='x', padx=20, pady=(0, 14))
        note = ctk.CTkLabel(foot, text='', font=T.font(13), text_color=T.TEXT_FAINT, anchor='w')
        note.pack(side='left', fill='x', expand=True)
        bar = ctk.CTkProgressBar(foot, width=190, height=10, corner_radius=5,
                                 fg_color=T.INPUT, progress_color=T.PINK)
        bar.set(0)

        state = {'busy': False, 'bar': False}

        def set_note(text, tone=T.TEXT_FAINT):
            note.configure(text=text, text_color=tone)

        def set_progress(done, total):
            """Called from the download thread."""
            if not state['bar']:
                state['bar'] = True
                bar.pack(side='right', padx=(12, 2))
            bar.set(min(1.0, done / total) if total else 0.0)

        def hide_progress():
            if state['bar']:
                state['bar'] = False
                bar.pack_forget()

        def clear():
            for child in results.winfo_children():
                child.destroy()

        def show(hits, source):
            clear()
            for e in hits[:120]:
                row = ctk.CTkFrame(results, fg_color=T.SURFACE_2, corner_radius=9, height=52)
                row.pack(fill='x', padx=8, pady=3)
                row.pack_propagate(False)
                ctk.CTkLabel(row, text=e['id'], font=T.font(13, 'bold'), text_color=T.PINK,
                             width=72).pack(side='left', padx=(10, 4))
                ctk.CTkLabel(row, text=e['creator'], font=T.font(13), text_color=T.TEXT_FAINT,
                             width=128, anchor='w').pack(side='left')
                ctk.CTkLabel(row, text=e['text'], font=T.font(14), text_color=T.TEXT,
                             anchor='w').pack(side='left', fill='x', expand=True)
                ctk.CTkButton(row, text='⭳ โหลด', width=90, height=32, corner_radius=8,
                              font=T.font(13, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PURPLE,
                              hover_color=T.PURPLE_DARK,
                              command=partial(grab, e, source)).pack(side='right', padx=(4, 8))
                ctk.CTkButton(row, text='▶', width=34, height=32, corner_radius=8,
                              font=T.font(13), fg_color='transparent', border_width=1,
                              border_color=T.BORDER, hover_color=T.SURFACE_3,
                              text_color=T.TEXT_DIM,
                              command=partial(listen, e, source)).pack(side='right')
            set_note(f'{source.label}: พบ {len(hits)} รายการ'
                     + ('  (แสดง 120 แรก)' if len(hits) > 120 else ''))

        def progress(done_bytes, total):
            self.after(0, lambda: set_progress(done_bytes, total))

        def grab(entry_data, source, *_):
            def worker():
                try:
                    path = src.download(entry_data, SOUNDS_DIR, source, on_progress=progress)
                    self.after(0, lambda: done(path))
                except Exception as exc:
                    self.after(0, lambda: fail(exc))

            def done(path):
                hide_progress()
                self.add_paths([path])
                set_note(f'บันทึกแล้ว: {os.path.basename(path)}', T.OK)

            def fail(exc):
                hide_progress()
                set_note(f'โหลดไม่สำเร็จ: {exc}', T.DANGER)

            set_note(f"กำลังโหลด {entry_data['text'][:40]} ...", T.WARN)
            threading.Thread(target=worker, daemon=True).start()

        def listen(entry_data, source, *_):
            """Fetch into a temp folder and play it to the headphones only."""
            def worker():
                try:
                    path = src.download(entry_data, PREVIEW_DIR, source, on_progress=progress)
                    self.after(0, lambda: play(path))
                except Exception as exc:
                    self.after(0, lambda: fail(exc))

            def play(path):
                hide_progress()
                try:
                    where = self.engine.preview(path)
                    set_note(f'กำลังฟัง: {entry_data["text"][:34]}  (ออกทาง {where} — ไม่เข้าเกม)', T.OK)
                except Exception as exc:
                    set_note(str(exc), T.WARN)

            def fail(exc):
                hide_progress()
                set_note(f'ฟังไม่ได้: {exc}', T.DANGER)

            set_note(f"กำลังโหลดตัวอย่าง {entry_data['text'][:30]} ...", T.WARN)
            threading.Thread(target=worker, daemon=True).start()

        def search(*_):
            if state['busy']:
                return
            needle = entry.get().strip()
            source = current_source()
            if has_categories(source):
                source.category = cat_var.get()
            elif not needle:
                return set_note(f'พิมพ์คำค้นก่อน — {source.hint}', T.WARN)
            state['busy'] = True
            set_note(f'กำลังโหลด {source.label} · {cat_var.get()} ...' if has_categories(source)
                     else f'กำลังค้นหาใน {source.label} ...', T.WARN)

            def worker():
                try:
                    hits = source.search(needle)
                    self.after(0, lambda: show(hits, source) if hits
                               else set_note(f'ไม่พบอะไรที่ตรงกับ "{needle}"', T.WARN))
                except Exception as exc:
                    self.after(0, lambda: set_note(f'อ่านเว็บไม่สำเร็จ: {exc}', T.DANGER))
                finally:
                    state['busy'] = False

            threading.Thread(target=worker, daemon=True).start()

        search_btn = ctk.CTkButton(head, text='ค้นหา', width=104, height=42, corner_radius=9,
                                   font=T.font(14, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PINK,
                                   hover_color=T.PINK_DARK, command=search)
        search_btn.pack(side='left', padx=(10, 0))
        entry.bind('<Return>', search)

        def live_filter():
            # เว็บที่มีหมวด: กรองจากหน้าที่โหลดไว้แล้ว ไม่ยิงเว็บ พิมพ์ไปกรองไปได้เลย
            source = current_source()
            if has_categories(source) and not entry.get().strip().lower().startswith('http'):
                job = state.get('job')
                if job:
                    win.after_cancel(job)
                state['job'] = win.after(250, search)

        watch_text(entry, live_filter)
        set_note('▶ = ฟังตัวอย่างทางหูฟัง (ไม่เข้าเกม)   ⭳ = บันทึกลง sounds/ แล้วเพิ่มเข้ารายการ')

    # ---------------------------------------------------------------- settings
    def _apply_sound_processing(self):
        self.engine.cache.set_processing(bool(self.config_data.get('trim_silence', True)),
                                         bool(self.config_data.get('normalize', True)))
        self.engine.cache.budget = int(self.config_data.get('cache_mb', 40)) * 1024 * 1024

    def open_settings(self):
        win = self._dialog('ตั้งค่า', 580, 860, modal=False)
        ctk.CTkLabel(win, text='ตั้งค่า', font=T.font(19, 'bold'), text_color=T.TEXT).pack(pady=(18, 8))
        body = ctk.CTkFrame(win, fg_color=T.SURFACE, corner_radius=12,
                            border_width=1, border_color=T.BORDER)
        body.pack(fill='both', expand=True, padx=20, pady=(0, 18))

        def section(title, note=None):
            ctk.CTkLabel(body, text=title, font=T.font(14, 'bold'),
                         text_color=T.TEXT).pack(anchor='w', padx=18, pady=(14, 0))
            if note:
                ctk.CTkLabel(body, text=note, font=T.font(12), text_color=T.TEXT_FAINT,
                             justify='left').pack(anchor='w', padx=18)

        def switch(text, key, on_change=None, state='normal', default=True):
            var = ctk.BooleanVar(value=bool(self.config_data.get(key, default)))

            def changed():
                self.config_data[key] = bool(var.get())
                if on_change:
                    on_change(bool(var.get()))
                self.save_config()

            sw = ctk.CTkSwitch(body, text=text, variable=var, command=changed, font=T.font(13),
                               text_color=T.TEXT_DIM, progress_color=T.PURPLE, fg_color=T.INPUT,
                               button_color=T.TEXT, button_hover_color=T.PINK, state=state)
            sw.pack(anchor='w', padx=18, pady=(8, 0))
            return var, sw

        section('สีธีม', 'เปลี่ยนทันที ใช้ได้ทั้งโหมดมืดและสว่าง (สลับโหมดที่ปุ่มขวาบน)')
        swatches = ctk.CTkFrame(body, fg_color='transparent')
        swatches.pack(fill='x', padx=14, pady=(8, 0))
        swatches.grid_columnconfigure((0, 1, 2), weight=1, uniform='sw')
        for i, name in enumerate(T.NAMES):
            pal = T.palette(name, T.MODE)
            on = name == T.NAME
            card = ctk.CTkFrame(swatches, fg_color=pal['SURFACE'], corner_radius=10, height=62,
                                border_width=3 if on else 1,
                                border_color=pal['PURPLE'] if on else pal['BORDER'])
            card.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky='ew')
            card.pack_propagate(False)
            label = ctk.CTkLabel(card, text=name + ('  ✓' if on else ''), font=T.font(13, 'bold'),
                                 text_color=pal['TEXT'])
            label.pack(anchor='w', padx=10, pady=(6, 2))
            dots = ctk.CTkFrame(card, fg_color='transparent')
            dots.pack(anchor='w', padx=10)
            parts = [ctk.CTkFrame(dots, fg_color=pal[k], width=16, height=16, corner_radius=8)
                     for k in ('PURPLE', 'PINK', 'BLUE')]
            for d in parts:
                d.pack(side='left', padx=(0, 4))
            for w in (card, label, dots, *parts):
                w.bind('<Button-1>', lambda _e, n=name: self.apply_theme(color=n, reopen_settings=True))
                try:
                    w.configure(cursor='hand2')
                except Exception:
                    pass

        section('ประมวลผลเสียง', 'ทำครั้งเดียวตอนโหลดไฟล์ ไม่เพิ่มงานตอนเล่น')
        switch('ตัดช่วงเงียบหัวท้าย — กดแล้วเสียงออกทันที', 'trim_silence',
               lambda _v: self._apply_sound_processing())
        switch('ปรับทุกเสียงให้ดังพอ ๆ กัน', 'normalize', lambda _v: self._apply_sound_processing())

        section('กันสแปม', 'เว้นระยะขั้นต่ำระหว่างเสียงที่กดจากคีย์ลัด (กดในหน้าต่างไม่ถูกจำกัด)')
        gaps = {'ปิด': 0.0, '1 วิ': 1.0, '2 วิ': 2.0, '3 วิ': 3.0, '5 วิ': 5.0}
        now = float(self.config_data.get('cooldown') or 0)
        gap_var = ctk.StringVar(value=next((k for k, v in gaps.items() if v == now), '2 วิ'))

        def gap_changed(label):
            self.config_data['cooldown'] = gaps[label]
            self.save_config()

        gap_row = ToggleGroup(body, list(gaps), gap_var, command=gap_changed)
        for b in gap_row.buttons.values():
            b.configure(width=70)
        gap_row.pack(anchor='w', padx=18, pady=(8, 0))

        section('เปิดพร้อม Windows', 'เปิดเครื่องแล้วรอใน tray เลย ไม่มีหน้าต่างเด้ง'
                if autostart.supported() else 'ใช้ได้เฉพาะตอนรันจาก SoundSaoTer.exe')

        def toggle_autostart(on):
            try:
                autostart.enable() if on else autostart.disable()
                self.say('จะเปิดพร้อม Windows แล้ว' if on else 'ยกเลิกการเปิดพร้อม Windows แล้ว', T.OK)
            except Exception as exc:
                self.say(f'ตั้งค่าไม่สำเร็จ: {exc}', T.DANGER)

        self.config_data['autostart'] = autostart.enabled()
        switch('เปิดพร้อม Windows', 'autostart', toggle_autostart,
               state='normal' if autostart.supported() else 'disabled', default=False)

        section('หน่วยความจำ')
        mem = ctk.CTkLabel(body, text='', font=T.font(13), text_color=T.TEXT_DIM)
        mem.pack(anchor='w', padx=18, pady=(4, 0))

        def show_mem():
            used, budget, n = self.engine.cache.usage()
            mem.configure(text=f'เสียงในแรม {used / 1048576:.1f} / {budget / 1048576:.0f} MB  ({n} ชุด)')

        def clear_cache():
            self.engine.cache.clear()
            show_mem()

        show_mem()
        ctk.CTkButton(body, text='ล้างเสียงในแรม', width=140, height=30, corner_radius=8,
                      font=T.font(12), fg_color=T.INPUT, hover_color=T.SURFACE_3,
                      text_color=T.TEXT_DIM, command=clear_cache).pack(anchor='w', padx=18, pady=(6, 16))

    # ---------------------------------------------------------------- themes
    def toggle_mode(self):
        self.apply_theme(mode='light' if T.MODE == 'dark' else 'dark')

    def apply_theme(self, color=None, mode=None, reopen_settings=False):
        color = color or T.NAME
        mode = mode or T.MODE
        if (color, mode) == (T.NAME, T.MODE):
            return
        T.apply(color, mode)
        ctk.set_appearance_mode(T.MODE)
        self.config_data['color'], self.config_data['mode'] = T.NAME, T.MODE
        self.config_data.pop('theme', None)
        self.save_config()
        # the click came from a widget the rebuild destroys — let the handler return first
        self.after(20, lambda: self.rebuild_ui(reopen_settings))

    def rebuild_ui(self, reopen_settings=False):
        """Recreate every widget in the new colours. Audio streams, hotkeys and the tray
        are untouched, so a theme change never interrupts sound or the mic."""
        self.capturing = False
        for child in list(self.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._dl_win = None
        self.rows = []
        self.configure(fg_color=T.BG)
        self._build()
        self.dev_game.set_options(self.devices, False, self.config_data.get('game_device'))
        self.dev_mon.set_options(self.devices, True, self.config_data.get('monitor_device'))
        self.dev_mic.set_options(self.inputs, True, self.config_data.get('mic_device'))
        self._restore_widgets()
        for row in (self.dev_game, self.dev_mon, self.dev_mic):
            row.refresh_pct()
        if getattr(self, 'tray', None) is not None and not self.tray.available:
            self.sw_tray.configure(state='disabled')
            self.sw_tray.var.set(False)
        self._update_banner(getattr(self, '_last_problems', []))
        if getattr(self, '_pending_update', None):
            self._show_update_button(self._pending_update)
        self.say(f'ธีม {T.NAME} · {"โหมดสว่าง" if T.MODE == "light" else "โหมดมืด"}', T.OK)
        if reopen_settings:
            self.after(60, self.open_settings)

    # ---------------------------------------------------------------- updates
    def check_update(self, quiet=True):
        if not ver.UPDATE_MANIFEST_URL:
            if not quiet:
                self.say('ยังไม่ได้ตั้งที่อยู่ไฟล์อัปเดตใน version.py', T.WARN)
            return
        if getattr(self, '_checking', False):
            return
        self._checking = True
        if not quiet:
            self.say('กำลังตรวจหาเวอร์ชันใหม่...', T.WARN)

        def worker():
            try:
                info = updater.check()
                self.after(0, lambda: self._update_found(info, quiet))
            except Exception as exc:
                self.after(0, lambda: self._update_error(exc, quiet))
            finally:
                self._checking = False

        threading.Thread(target=worker, daemon=True).start()

    def _update_error(self, exc, quiet):
        if not quiet:
            self.say(f'ตรวจอัปเดตไม่สำเร็จ: {exc}', T.DANGER)

    def _update_found(self, info, quiet):
        if info is None:
            if not quiet:
                self.say(f'ใช้เวอร์ชันล่าสุดอยู่แล้ว (v{ver.VERSION})', T.OK)
            return
        self._show_update_button(info)
        self.say(f"มีเวอร์ชันใหม่ v{info['version']} — กดปุ่มสีชมพูมุมขวาล่างเพื่อติดตั้ง", T.OK)
        if getattr(self, 'tray', None) is not None and self.tray.available:
            self.tray.notify(f"SoundSaoTer มีเวอร์ชันใหม่ v{info['version']}")

    def _show_update_button(self, info):
        self._pending_update = info
        self.btn_update.configure(text=f"อัปเดต v{info['version']}", fg_color=T.PINK,
                                  hover_color=T.PINK_DARK, text_color=T.ON_ACCENT,
                                  command=lambda: self.offer_update(info))

    def offer_update(self, info):
        win = self._dialog(f"อัปเดตเป็น v{info['version']}", 540, 340, modal=False)
        ctk.CTkLabel(win, text=f"v{ver.VERSION}  →  v{info['version']}",
                     font=T.font(19, 'bold'), text_color=T.TEXT).pack(pady=(24, 6))
        notes = ctk.CTkTextbox(win, height=140, corner_radius=10, font=T.font(13),
                               fg_color=T.SURFACE, border_color=T.BORDER, border_width=1,
                               text_color=T.TEXT_DIM, wrap='word')
        notes.pack(fill='x', padx=24)
        notes.insert('1.0', info.get('notes') or 'ไม่มีรายละเอียดการเปลี่ยนแปลง')
        notes.configure(state='disabled')

        bar = ctk.CTkProgressBar(win, height=10, corner_radius=5,
                                 fg_color=T.INPUT, progress_color=T.PINK)
        note = ctk.CTkLabel(win, text='โปรแกรมจะปิดแล้วเปิดใหม่เป็นเวอร์ชันใหม่ให้เอง',
                            font=T.font(12), text_color=T.TEXT_FAINT)
        note.pack(pady=(12, 6))

        def install():
            btn.configure(state='disabled', text='กำลังโหลด...')
            bar.pack(fill='x', padx=24, pady=(0, 8))
            bar.set(0)

            def progress(done, total):
                self.after(0, lambda: bar.set(min(1.0, done / total) if total else 0))

            def worker():
                try:
                    path = updater.download(info, on_progress=progress)
                    self.after(0, lambda: finish(path))
                except Exception as exc:
                    self.after(0, lambda: failed(exc))

            def finish(path):
                note.configure(text='โหลดเสร็จแล้ว กำลังสลับเป็นเวอร์ชันใหม่...', text_color=T.OK)
                try:
                    updater.apply(path)
                except Exception as exc:
                    return failed(exc)
                self.after(300, lambda: self.on_close(force=True))

            def failed(exc):
                note.configure(text=f'อัปเดตไม่สำเร็จ: {exc}', text_color=T.DANGER)
                btn.configure(state='normal', text='ลองใหม่')
                bar.pack_forget()

            threading.Thread(target=worker, daemon=True).start()

        btn = ctk.CTkButton(win, text='ติดตั้งเลย', height=42, corner_radius=10,
                            font=T.font(15, 'bold'), text_color=T.ON_ACCENT, fg_color=T.PURPLE,
                            hover_color=T.PURPLE_DARK, command=install)
        btn.pack(fill='x', padx=24, pady=(4, 18))

    # ---------------------------------------------------------------- config
    def load_config(self):
        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                cfg = default_config()
                cfg.update({k: v for k, v in data.items() if k in cfg})
                self.config_data = cfg
            except Exception as exc:
                messagebox.showwarning('config.json', f'อ่าน config ไม่ได้ ใช้ค่าเริ่มต้นแทน\n{exc}')
        self.config_data['sounds'] = [s for s in self.config_data.get('sounds', [])
                                      if os.path.isfile(s.get('path', ''))]
        self._scanned = self.scan_sounds_folder()
        if self._scanned:
            self.save_config()
        self.config_data['color'], self.config_data['mode'] = T.NAME, T.MODE
        self.config_data.pop('theme', None)
        self._migrate_slots()
        self._apply_sound_processing()
        self._restore_widgets()

    def _restore_widgets(self):
        """Push the saved settings into freshly built widgets."""
        self.dev_game.vol.set(self.config_data['game_volume'])
        self.dev_mon.vol.set(self.config_data['monitor_volume'])
        self.dev_mic.vol.set(self.config_data['mic_volume'])
        self.sw_excl.var.set(self.config_data['exclusive'])
        self.sw_hk.var.set(self.config_data['hotkeys_enabled'])
        self.sw_tray.var.set(bool(self.config_data.get('tray', True)))
        self.stop_badge.configure(text=self.config_data['stop_hotkey'] or 'ตั้งปุ่ม')
        self.var_hearself.set(bool(self.config_data.get('hear_self')))
        self.engine.set_monitor_self(self.var_hearself.get())   # applied when the mic starts
        fx = self.engine.micfx                                  # also before the mic starts
        fx.denoise = bool(self.config_data.get('mic_denoise'))
        fx.gate = bool(self.config_data.get('mic_gate'))
        fx.sensitivity = float(self.config_data.get('mic_gate_sens', 50))
        fx.agc = bool(self.config_data.get('mic_agc'))
        fx.aec = bool(self.config_data.get('mic_aec'))
        fx.aec_device = self.config_data.get('mic_aec_device') or ''
        self._refresh_mic_fx_button()
        self.engine.exclusive = self.config_data['exclusive']
        self.view_var.set('📚  คลังเสียง' if self.config_data.get('view') == 'library' else '🎯  ช่องคีย์ลัด')
        self.view_tabs._paint()
        self.redraw()

    def save_config(self):
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as fh:
                json.dump(self.config_data, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.say(f'บันทึก config ไม่ได้: {exc}', T.DANGER)

    def on_close(self, force=False):
        if not force and self.config_data.get('tray') and getattr(self, 'tray', None)                 and self.tray.available:
            self.hide_window()          # keep running in the background
            return
        if getattr(self, 'tray', None) is not None:
            self.tray.stop()
        if self.hotkeys is not None:
            try:
                self.hotkeys.close()
            except Exception:
                pass
        if 'ytclip' in sys.modules:
            sys.modules['ytclip'].kill_all()
        self.save_config()
        self.engine.close()
        self.destroy()


def focus_existing_instance():
    """True if another copy is already running — two copies fight over the hotkeys."""
    import ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, 'SoundSaoTer.SingleInstance')
        if kernel32.GetLastError() != 183:      # ERROR_ALREADY_EXISTS
            return False
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, 'SoundSaoTer')
        if hwnd:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def main():
    if focus_existing_instance():
        return 0
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == '__main__':
    sys.exit(main())
