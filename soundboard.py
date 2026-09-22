"""Soundboard: press a hotkey, the sound file goes out through your game mic."""
import json
import os
import queue
import sys
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
    }


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
        group = ToggleGroup(chips, ['ทั้งหมด', 'Dota', 'Myinstants', 'TiengDong', 'ของฉัน'], src_var,
                            command=lambda _v: schedule())
        for b in group.buttons.values():
            b.configure(width=84, height=32)
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
