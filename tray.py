"""System-tray icon so the app can keep running (and keep its hotkeys) with no window.

Degrades to a no-op when pystray/Pillow are unavailable: the caller then just keeps
its old close-means-quit behaviour.
"""
import os
import threading

try:
    import pystray
    from PIL import Image
    AVAILABLE = True
except Exception:                       # pragma: no cover - depends on the install
    pystray = None
    Image = None
    AVAILABLE = False


class Tray:
    def __init__(self, icon_path, title='SoundSaoTer', on_show=None, on_toggle=None,
                 on_stop=None, on_quit=None, hotkeys_on=None):
        self.available = AVAILABLE
        self.icon = None
        self._lock = threading.Lock()
        if not AVAILABLE:
            return
        self.on_show = on_show or (lambda: None)
        self.on_toggle = on_toggle or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.hotkeys_on = hotkeys_on or (lambda: True)

        image = self._load(icon_path)
        if image is None:
            self.available = False
            return

        menu = pystray.Menu(
            pystray.MenuItem('เปิดหน้าต่าง', self._show, default=True),
            pystray.MenuItem('ฮอตคีย์', self._toggle, checked=lambda _i: bool(self.hotkeys_on())),
            pystray.MenuItem('หยุดเสียง', self._stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('ออกจากโปรแกรม', self._quit),
        )
        self.icon = pystray.Icon('soundsaoter', image, title, menu)

    @staticmethod
    def _load(icon_path):
        try:
            if icon_path and os.path.isfile(icon_path):
                img = Image.open(icon_path)
                img.load()
                return img.convert('RGBA')
        except Exception:
            pass
        try:                            # last resort so the tray still works
            return Image.new('RGBA', (64, 64), (168, 85, 247, 255))
        except Exception:
            return None

    # pystray calls these on its own thread; the callbacks marshal to the UI thread
    def _show(self, *_):
        self.on_show()

    def _toggle(self, *_):
        self.on_toggle()

    def _stop(self, *_):
        self.on_stop()

    def _quit(self, *_):
        self.on_quit()

    def start(self):
        if not self.available or self.icon is None:
            return False
        try:
            self.icon.run_detached()
            return True
        except Exception:
            self.available = False
            return False

    def notify(self, message, title='SoundSaoTer'):
        if self.available and self.icon is not None:
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def refresh(self):
        """Redraw the menu so the hotkey tick mark follows the app."""
        if self.available and self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        with self._lock:
            if self.icon is not None:
                try:
                    self.icon.stop()
                except Exception:
                    pass
                self.icon = None
