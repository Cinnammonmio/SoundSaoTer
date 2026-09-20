"""Global hotkeys through the documented Win32 RegisterHotKey API.

No low-level keyboard hook, no synthetic input: the app never sees keystrokes that
are not one of its own hotkeys, which is what keeps it clear of anti-cheat heuristics.
The trade-off is that a registered combo is swallowed by Windows and never reaches
the foreground app, so pick keys the game does not use.
"""
import ctypes
import queue
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_USER = 0x0400
WM_APPLY = WM_USER + 11
WM_STOP = WM_USER + 12
PM_NOREMOVE = 0x0000

VK_MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetKeyState.argtypes = (ctypes.c_int,)
user32.GetKeyState.restype = ctypes.c_short


def _build_vk_table():
    table = {}
    for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
        table[c.lower()] = ord(c)
    for i in range(1, 25):
        table[f'f{i}'] = 0x6F + i
    for i in range(10):
        table[f'num {i}'] = 0x60 + i
    table.update({
        'num *': 0x6A, 'num +': 0x6B, 'num -': 0x6D, 'num .': 0x6E, 'num /': 0x6F,
        'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'esc': 0x1B, 'space': 0x20,
        'page up': 0x21, 'page down': 0x22, 'end': 0x23, 'home': 0x24,
        'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
        'insert': 0x2D, 'delete': 0x2E, 'pause': 0x13, 'scroll lock': 0x91,
        ';': 0xBA, '=': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE, '/': 0xBF,
        '`': 0xC0, '[': 0xDB, '\\': 0xDC, ']': 0xDD, "'": 0xDE,
    })
    return table


NAME_TO_VK = _build_vk_table()
VK_TO_NAME = {vk: name for name, vk in reversed(list(NAME_TO_VK.items()))}

_MOD_NAMES = (('ctrl', MOD_CONTROL), ('alt', MOD_ALT), ('shift', MOD_SHIFT), ('win', MOD_WIN))
_MOD_ALIASES = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT,
                'shift': MOD_SHIFT, 'win': MOD_WIN, 'windows': MOD_WIN, 'cmd': MOD_WIN}


def vk_name(vk):
    return VK_TO_NAME.get(vk, f'vk{vk:02x}')


def format_spec(mods, vk):
    """(2, 0x61) -> 'ctrl+num 1'"""
    parts = [name for name, bit in _MOD_NAMES if mods & bit]
    parts.append(vk_name(vk))
    return '+'.join(parts)


def parse_spec(spec):
    """'ctrl+alt+s' -> (mods, vk). Returns None if nothing usable is in the string."""
    if not spec:
        return None
    mods, vk = 0, None
    # '+' is the separator, so shield the two keys that are themselves a '+'
    text = str(spec).strip().lower().replace('num +', '\x00num_plus\x00')
    tokens = [t.replace('\x00', '') for t in text.split('+')]
    for token in tokens:
        key = token.strip().lower()
        if not key:
            vk = NAME_TO_VK['=']      # a trailing '+' means the plus key itself
            continue
        if key == 'num_plus':
            vk = NAME_TO_VK['num +']
            continue
        if key in _MOD_ALIASES:
            mods |= _MOD_ALIASES[key]
        elif key in NAME_TO_VK:
            vk = NAME_TO_VK[key]
        elif key.startswith('vk'):
            try:
                vk = int(key[2:], 16)
            except ValueError:
                return None
        elif key.startswith('num') and key[3:].strip().isdigit():
            vk = 0x60 + int(key[3:].strip())
        else:
            return None
    return (mods, vk) if vk is not None else None


def current_modifiers():
    """Modifier bits held down right now, for the hotkey-capture dialog."""
    mods = 0
    for vk, bit in ((0x11, MOD_CONTROL), (0x12, MOD_ALT), (0x10, MOD_SHIFT)):
        if user32.GetKeyState(vk) & 0x8000:
            mods |= bit
    if (user32.GetKeyState(0x5B) & 0x8000) or (user32.GetKeyState(0x5C) & 0x8000):
        mods |= MOD_WIN
    return mods


class HotkeyManager:
    """Owns a thread with a message loop; every hotkey lives on that one thread."""

    def __init__(self):
        self._callbacks = {}          # hotkey id -> callable
        self._pending = queue.Queue()
        self._results = queue.Queue()
        self._ready = threading.Event()
        self._tid = 0
        self._thread = threading.Thread(target=self._run, name='hotkeys', daemon=True)
        self._thread.start()
        self._ready.wait(5.0)

    # ---- worker thread ----
    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # touch the queue so PostThreadMessage can never arrive before it exists
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)
        self._ready.set()
        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                callback = self._callbacks.get(int(msg.wParam))
                if callback is not None:
                    try:
                        callback()
                    except Exception:
                        pass
            elif msg.message == WM_APPLY:
                self._apply()
            elif msg.message == WM_STOP:
                self._clear()
                break
        self._clear()

    def _clear(self):
        for hid in list(self._callbacks):
            user32.UnregisterHotKey(None, hid)
        self._callbacks.clear()

    def _apply(self):
        try:
            entries = self._pending.get_nowait()
        except queue.Empty:
            return
        self._clear()
        failed = []
        for i, (mods, vk, callback) in enumerate(entries):
            hid = i + 1
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._callbacks[hid] = callback
            else:
                failed.append(i)
        self._results.put(failed)

    # ---- caller thread ----
    def set_all(self, entries, timeout=3.0):
        """entries: [(mods, vk, callback)]. Returns the indexes that could not register."""
        if not self._ready.is_set():
            return list(range(len(entries)))
        while not self._results.empty():
            self._results.get_nowait()
        self._pending.put(list(entries))
        user32.PostThreadMessageW(self._tid, WM_APPLY, 0, 0)
        try:
            return self._results.get(timeout=timeout)
        except queue.Empty:
            return []

    def close(self):
        if self._ready.is_set():
            user32.PostThreadMessageW(self._tid, WM_STOP, 0, 0)
            self._thread.join(timeout=2.0)
