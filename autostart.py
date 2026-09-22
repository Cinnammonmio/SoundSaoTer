"""Start with Windows via the per-user Run key (no admin, no Startup-folder shortcut).

Only the exe build can register itself — pointing Windows at a .py file would open
it in an editor or a console instead of the app.
"""
import os
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME = 'SoundSaoTer'
TRAY_ARG = '--tray'

try:
    import winreg
except ImportError:          # pragma: no cover - not Windows
    winreg = None


def exe_path():
    return os.path.abspath(sys.argv[0]) if getattr(sys, 'frozen', False) else None


def supported():
    return winreg is not None and exe_path() is not None


def _command(path):
    return f'"{path}" {TRAY_ARG}'


def current(name=NAME):
    """The command Windows will run at logon, or None."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _type = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def enabled(name=NAME):
    return current(name) is not None


def enable(path=None, name=NAME):
    path = path or exe_path()
    if winreg is None or not path:
        raise RuntimeError('เปิดพร้อม Windows ใช้ได้เฉพาะตอนรันจากไฟล์ exe')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, _command(path))
    return _command(path)


def disable(name=NAME):
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


def refresh(name=NAME):
    """If the exe was moved, point the entry at where it lives now."""
    path = exe_path()
    if path and enabled(name) and current(name) != _command(path):
        enable(path, name)
        return True
    return False


def started_hidden():
    return TRAY_ARG in sys.argv[1:]
