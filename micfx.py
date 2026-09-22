"""Voice processing for the live mic, in the spirit of Discord's "Voice Processing".

    noise suppression   RNNoise (Xiph) — a small recurrent network that keeps speech
                        and drops fans, AC, keyboards, room noise
    voice gate          mutes the mic between words; driven by RNNoise's
                        voice-activity score, not loudness, so a keyboard click or a
                        door does not open it
    auto volume         evens out quiet and loud speech; only adapts while you speak,
                        so it never pumps up the background
    limiter             soft ceiling, so boosted speech never clips

Everything runs at 48 kHz mono in 10 ms frames (RNNoise's native format) and adds
one frame of latency (+ RNNoise's own 10 ms). RNNoise is loaded only while the
suppressor or the gate is on — it is the one part that costs RAM (~20 MB).

Threading: the audio callback only calls process(). The RNNoise state is created
and freed by attach()/detach(), which the engine calls while the mic stream is
stopped, so the two never race.
"""
import ctypes
import os
import sys

import numpy as np

RATE = 48000
FRAME = 480                      # 10 ms — RNNoise's fixed frame size

TARGET_RMS = 10 ** (-20 / 20)    # auto volume aims speech at about -20 dBFS
MAX_BOOST = 10 ** (18 / 20)      # at most +18 dB for a very quiet mic
MAX_CUT = 10 ** (-10 / 20)       # at most -10 dB for a very loud one
HOLD_FRAMES = 25                 # gate stays open 250 ms after the last speech
CLOSE_STEP = 0.12                # gate fades shut over ~80 ms (no chopped word endings)


# ---------------------------------------------------------------- RNNoise library
_lib = None
_users = 0


def _dll_path():
    here = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    for path in (os.path.join(here, 'rnnoise', 'rnnoise.dll'),
                 os.path.join(here, 'vendor', 'rnnoise', 'rnnoise.dll')):
        if os.path.isfile(path):
            return path
    raise FileNotFoundError('ไม่พบ rnnoise.dll (ตัวตัดเสียงรบกวน)')


def available():
    try:
        _dll_path()
        return True
    except FileNotFoundError:
        return False


def _acquire():
    global _lib, _users
    if _lib is None:
        lib = ctypes.CDLL(_dll_path())
        lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        lib.rnnoise_create.restype = ctypes.c_void_p
        lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        lib.rnnoise_destroy.restype = None
        lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                              ctypes.POINTER(ctypes.c_float)]
        lib.rnnoise_process_frame.restype = ctypes.c_float
        lib.rnnoise_get_frame_size.restype = ctypes.c_int
        if lib.rnnoise_get_frame_size() != FRAME:
            raise RuntimeError('rnnoise.dll ใช้ขนาดเฟรมไม่ตรง')
        _lib = lib
    _users += 1
    return _lib


def _release():
    """Unload the DLL once nobody uses it — hands its ~20 MB of model back."""
    global _lib, _users
    _users = max(0, _users - 1)
    if _users == 0 and _lib is not None:
        handle, _lib = _lib._handle, None
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
        kernel32.FreeLibrary(handle)


class _Denoiser:
    def __init__(self):
        self.lib = _acquire()
        self.state = self.lib.rnnoise_create(None)
        self.inp = np.zeros(FRAME, np.float32)
        self.out = np.zeros(FRAME, np.float32)
        self._inp_ptr = self.inp.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self._out_ptr = self.out.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

    def process(self, frame):
        """frame: 480 float32 samples in -1..1 -> (cleaned copy, voice probability 0..1)."""
        np.multiply(frame, 32768.0, out=self.inp)            # RNNoise works in 16-bit scale
        vad = self.lib.rnnoise_process_frame(self.state, self._out_ptr, self._inp_ptr)
        return self.out * (1 / 32768.0), float(vad)

    def close(self):
        if self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None
            _release()


# ---------------------------------------------------------------- the processor
class MicProcessor:
    """Settings are plain attributes the UI may flip at any time (except which of
    them need RNNoise — the engine restarts the mic for that)."""

    def __init__(self):
        self.denoise = False
        self.gate = False
        self.sensitivity = 50        # 0 = only clear speech opens the gate, 100 = opens easily
        self.agc = False
        self._rnn = None
        self._reset()

    # what the UI shows
    level_in = level_out = 0.0
    speaking = False

    @property
    def needs_ai(self):
        return self.denoise or self.gate

    @property
    def active(self):
        return self.denoise or self.gate or self.agc

    def _reset(self):
        self._pending = np.zeros(0, np.float32)
        self._ready = np.zeros(FRAME, np.float32)    # the one frame of latency
        self._gate_gain = 1.0
        self._hold = 0
        self._agc_gain = 1.0
        self._floor = 1.0

    error = ''

    def attach(self):
        """Mic stream is stopped: load RNNoise if a setting needs it. If it cannot be
        loaded the mic still works — just without the AI parts."""
        self._reset()
        self.error = ''
        if self.needs_ai and self._rnn is None:
            try:
                self._rnn = _Denoiser()
            except Exception as exc:        # noqa: BLE001 — shown in the settings dialog
                self.error = f'โหลดตัวตัดเสียงรบกวนไม่ได้: {exc}'
                self.denoise = self.gate = False

    def detach(self):
        """Mic stream is stopped: free RNNoise."""
        rnn, self._rnn = self._rnn, None
        if rnn is not None:
            rnn.close()

    @property
    def threshold(self):
        # sensitivity 0..100 -> voice probability needed to open: 0.9 .. 0.1
        return 0.9 - max(0, min(100, self.sensitivity)) * 0.008

    def process(self, x):
        """x: mono float32 @ 48 kHz, any length -> same length, delayed by one frame."""
        n = len(x)
        pending = np.concatenate([self._pending, x]) if len(self._pending) else np.asarray(x, np.float32)
        done = []
        while len(pending) >= FRAME:
            done.append(self._frame(pending[:FRAME]))
            pending = pending[FRAME:]
        self._pending = pending.copy()
        ready = np.concatenate([self._ready] + done) if done else self._ready
        out, self._ready = ready[:n], ready[n:].copy()
        return out

    def _frame(self, frame):
        self.level_in = max(float(np.abs(frame).max()), self.level_in * 0.8)
        rnn = self._rnn
        vad = None
        if rnn is not None:
            cleaned, vad = rnn.process(frame)
            y = cleaned if self.denoise else frame.copy()
        else:
            y = frame.copy()

        start = end = 1.0
        if self.gate and vad is not None:
            if vad >= self.threshold:
                self._hold = HOLD_FRAMES
            elif self._hold > 0:
                self._hold -= 1
            target = 1.0 if self._hold > 0 else 0.0
            start = self._gate_gain
            end = target if target > start else max(target, start - CLOSE_STEP)
            self._gate_gain = end
            self.speaking = end > 0.5
        else:
            self.speaking = vad is None or vad >= self.threshold

        if self.agc:
            rms = float(np.sqrt(np.mean(y * y))) + 1e-9
            # background level: drops at once, creeps up ~0.4 dB/s — so it sits on the
            # quiet gaps between words, not on the words themselves
            self._floor = rms if rms < self._floor else self._floor * 1.0005
            if vad is not None:
                talking = vad >= 0.6
            else:                           # no AI: speech = clearly (+10 dB) above the room
                talking = rms > max(0.006, self._floor * 3.2)
            if talking:
                want = min(MAX_BOOST, max(MAX_CUT, TARGET_RMS / rms))
                rate = 0.3 if want < self._agc_gain else 0.02        # duck fast, rise slowly
                self._agc_gain += (want - self._agc_gain) * rate
            start *= self._agc_gain
            end *= self._agc_gain

        if start != 1.0 or end != 1.0:
            y *= np.linspace(start, end, FRAME, dtype=np.float32)
        if self.agc and float(np.abs(y).max()) > 0.9:
            y = (0.95 * np.tanh(y / 0.95)).astype(np.float32)    # soft limiter
        self.level_out = max(float(np.abs(y).max()), self.level_out * 0.8)
        return y
