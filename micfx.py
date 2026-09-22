"""Voice processing for the live mic, in the spirit of Discord's "Voice Processing".

    noise suppression   RNNoise (Xiph) — a small recurrent network that keeps speech
                        and drops fans, AC, keyboards, room noise
    voice gate          mutes the mic between words; driven by RNNoise's
                        voice-activity score, not loudness, so a keyboard click or a
                        door does not open it
    auto volume         evens out quiet and loud speech; only adapts while you speak,
                        so it never pumps up the background
    limiter             soft ceiling, so boosted speech never clips
    echo cancellation   WebRTC AEC3 (through LiveKit's WebRTC build): records what the
                        speaker plays (WASAPI loopback) and subtracts its echo from
                        the mic — for playing on speakers instead of a headset

Everything runs at 48 kHz mono in 10 ms frames (RNNoise's native format) and adds
one frame of latency (+ RNNoise's own 10 ms). RNNoise is loaded only while the
suppressor or the gate is on (~12 MB), the echo canceller only while it is on (~21 MB).

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


# ---------------------------------------------------------------- echo cancellation
VIRTUAL_HINTS = ('cable', 'vb-audio', 'voicemeeter', 'virtual')


def is_virtual(name):
    low = (name or '').lower()
    return any(h in low for h in VIRTUAL_HINTS)


def speaker_names():
    """Real output devices, as Windows names them (for the 'which speaker' menu)."""
    import soundcard as sc
    return [s.name for s in sc.all_speakers() if not is_virtual(s.name)]


def pick_speaker(wanted='', hint=''):
    """The speaker whose sound may leak into the mic. Never a virtual cable: its
    loopback carries your own mic, and cancelling that would erase your voice."""
    import soundcard as sc
    speakers = [s for s in sc.all_speakers() if not is_virtual(s.name)]
    if wanted:
        for s in speakers:
            if s.name == wanted:
                return s
    default = sc.default_speaker()
    if default is not None and not is_virtual(default.name):
        return default
    if hint:                                        # the app's own "headphones" device
        for s in speakers:
            if s.name.lower().startswith(hint.lower()[:20]) or hint.lower().startswith(s.name.lower()[:20]):
                return s
    return speakers[0] if speakers else None


class _EchoReference:
    """Records what the speaker is playing (WASAPI loopback) on its own thread."""

    def __init__(self, speaker):
        import collections
        import threading
        self.name = speaker.name
        self._speaker = speaker
        self.frames = collections.deque(maxlen=50)   # 0.5 s of backlog at most
        self._stop = threading.Event()
        self.error = ''
        self._thread = threading.Thread(target=self._run, name='echo-reference', daemon=True)
        self._thread.start()

    def _run(self):
        import warnings
        import soundcard as sc
        ctypes.windll.ole32.CoInitializeEx(None, 0)   # COM for this thread
        # soundcard warns "data discontinuity" whenever nothing is playing; that is normal
        warnings.filterwarnings('ignore', message='data discontinuity')
        try:
            mic = sc.get_microphone(id=str(self._speaker.name), include_loopback=True)
            with mic.recorder(samplerate=RATE, channels=1, blocksize=FRAME) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=FRAME)
                    self.frames.append(np.ascontiguousarray(data[:, 0], dtype=np.float32))
        except Exception as exc:                      # noqa: BLE001 — shown in the dialog
            self.error = f'อ่านเสียงลำโพงไม่ได้: {exc}'

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)


class _EchoCanceller:
    """WebRTC's echo canceller (AEC3, via LiveKit's build of WebRTC)."""

    def __init__(self, speaker, denoise=False):
        from livekit import rtc                       # heavy: imported only when switched on
        self._rtc = rtc
        # With echo cancelling on, noise is suppressed here (WebRTC NS) rather than by
        # RNNoise: RNNoise mistakes AEC's leftovers for noise and, while you talk over
        # the speakers, cuts your voice by 10-30 dB. WebRTC NS is built to follow AEC3.
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=denoise,
                                             high_pass_filter=True)
        self.ref = _EchoReference(speaker)

    def _frame(self, samples):
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
        return self._rtc.AudioFrame(pcm, RATE, 1, FRAME)

    def process(self, frame):
        refs = self.ref.frames
        while refs:                                   # the speaker audio since the last mic frame
            self.apm.process_reverse_stream(self._frame(refs.popleft()))
        f = self._frame(frame)
        self.apm.process_stream(f)
        return np.frombuffer(f.data, np.int16).astype(np.float32) * (1 / 32767)

    def close(self):
        self.ref.close()
        handle = getattr(self.apm, '_ffi_handle', None)
        if handle is not None:
            handle.dispose()


def selftest(out_path):
    """Load RNNoise, the echo canceller and the speaker loopback like the app does,
    run a second of silence through them and write what happened (for bug reports)."""
    import json
    import time
    report = {}
    fx = MicProcessor()
    fx.denoise = fx.gate = fx.agc = fx.aec = True
    try:
        t = time.perf_counter()
        fx.attach()
        report['attach_s'] = round(time.perf_counter() - t, 3)
        report['rnnoise'] = fx._rnn is not None
        report['aec'] = fx._aec is not None
        report['speaker'] = fx.aec_name
        report['error'] = fx.error
        n = 0
        for _ in range(100):
            fx.process(np.zeros(FRAME, np.float32))
            n += 1
            time.sleep(0.01)
        report['frames'] = n
        report['loopback_frames_waiting'] = len(fx._aec.ref.frames) if fx._aec else None
        report['loopback_error'] = fx.aec_error
    except Exception as exc:                            # noqa: BLE001
        report['exception'] = f'{type(exc).__name__}: {exc}'
    finally:
        fx.detach()
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return 0 if report.get('rnnoise') and report.get('aec') and not report.get('exception') else 1


# ---------------------------------------------------------------- the processor
class MicProcessor:
    """Settings are plain attributes the UI may flip at any time (except which of
    them need RNNoise — the engine restarts the mic for that)."""

    def __init__(self):
        self.denoise = False
        self.gate = False
        self.sensitivity = 50        # 0 = only clear speech opens the gate, 100 = opens easily
        self.agc = False
        self.aec = False
        self.aec_device = ''         # '' = pick automatically
        self.aec_hint = ''           # the app's headphone device, used if Windows' default is a cable
        self.aec_name = ''           # the speaker actually used (for the dialog)
        self._rnn = None
        self._aec = None
        self._reset()

    # what the UI shows
    level_in = level_out = 0.0
    speaking = False

    @property
    def needs_ai(self):
        """RNNoise: for the gate's voice detection, and for suppression unless the echo
        canceller does the suppression itself."""
        return self.gate or (self.denoise and not self.aec)

    @property
    def active(self):
        return self.denoise or self.gate or self.agc or self.aec

    @property
    def loaded_parts(self):
        """What must be (un)loaded with the mic stopped — the engine restarts the mic
        when this changes."""
        return (self.needs_ai, self.aec, (self.aec_device, self.denoise) if self.aec else None)

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
                self.gate = False
                if not self.aec:
                    self.denoise = False
        self.aec_name = ''
        if self.aec and self._aec is None:
            try:
                speaker = pick_speaker(self.aec_device, self.aec_hint)
                if speaker is None:
                    raise RuntimeError('ไม่พบลำโพง/หูฟังจริงในเครื่อง')
                self._aec = _EchoCanceller(speaker, denoise=self.denoise)
                self.aec_name = speaker.name
            except Exception as exc:        # noqa: BLE001
                self.error = f'เปิดตัดเสียงสะท้อนไม่ได้: {exc}'
                self.aec = False

    def detach(self):
        """Mic stream is stopped: free RNNoise and the echo canceller."""
        rnn, self._rnn = self._rnn, None
        if rnn is not None:
            rnn.close()
        aec, self._aec = self._aec, None
        if aec is not None:
            aec.close()

    @property
    def aec_error(self):
        aec = self._aec
        return aec.ref.error if aec is not None else ''

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
        aec = self._aec
        if aec is not None:                 # first: take the speaker's sound back out
            frame = aec.process(frame)
        rnn = self._rnn
        vad = None
        if rnn is not None:
            cleaned, vad = rnn.process(frame)
            y = cleaned if self.denoise and aec is None else frame.copy()
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
