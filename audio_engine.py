"""Audio engine: loads sound files and plays them to one or more output devices."""
import threading
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf

import micfx

MAX_SECONDS = 120  # safety cap so a huge file can't eat all the RAM
LIVE_MAX_MS = 120  # mic passthrough: drop the oldest audio past this much backlog


def _to_channels(data: np.ndarray, channels: int) -> np.ndarray:
    if data.shape[1] == channels:
        return data
    if data.shape[1] == 1:
        return np.repeat(data, channels, axis=1)
    if channels == 1:
        return data.mean(axis=1, keepdims=True).astype(np.float32)
    return np.repeat(data[:, :1], channels, axis=1)


def _resample(data: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return data
    n_new = int(round(data.shape[0] * sr_to / float(sr_from)))
    if n_new <= 0:
        return np.zeros((0, data.shape[1]), dtype=np.float32)
    x_old = np.arange(data.shape[0], dtype=np.float64)
    x_new = np.linspace(0, data.shape[0] - 1, n_new, dtype=np.float64)
    out = np.empty((n_new, data.shape[1]), dtype=np.float32)
    for c in range(data.shape[1]):
        out[:, c] = np.interp(x_new, x_old, data[:, c])
    return out


class StreamResampler:
    """Linear resampler that keeps its phase across calls, so a live stream has no clicks."""

    def __init__(self, sr_in, sr_out, channels):
        self.sr_in = sr_in
        self.sr_out = sr_out
        self.step = float(sr_in) / float(sr_out)
        self.channels = channels
        self.pos = 0.0
        self.tail = np.zeros((1, channels), dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.sr_in == self.sr_out:
            return block
        buf = np.concatenate((self.tail, block))
        last = buf.shape[0] - 1
        count = int(np.floor((last - self.pos) / self.step)) + 1
        if count <= 0:
            self.pos -= last
            self.tail = buf[-1:]
            return np.zeros((0, self.channels), dtype=np.float32)
        idx = self.pos + self.step * np.arange(count, dtype=np.float64)
        grid = np.arange(buf.shape[0], dtype=np.float64)
        out = np.empty((count, self.channels), dtype=np.float32)
        for c in range(self.channels):
            out[:, c] = np.interp(idx, grid, buf[:, c])
        self.pos = idx[-1] + self.step - last
        self.tail = buf[-1:]
        return out


class SoundCache:
    """Decoded sounds, ready to play, kept under a RAM budget.

    Every play needs the file as float32 at the device's rate — a 4 s stereo clip is
    ~1.5 MB decoded — so the cache keeps only the most recently used conversions and
    drops the rest once `budget_mb` is exceeded. A sound that is still playing keeps
    its own reference, so evicting it mid-play is harmless.
    """

    def __init__(self, budget_mb=40, trim=True, normalize=True):
        from collections import OrderedDict
        self.budget = int(budget_mb * 1024 * 1024)
        self.trim = trim
        self.normalize = normalize
        self._conv = OrderedDict()      # (path, sr, ch) -> data, oldest first
        self._bytes = 0
        self._lock = threading.Lock()

    # -- one-off processing, done at load time so playback stays a plain copy --
    def _decode(self, path):
        data, sr = sf.read(path, dtype='float32', always_2d=True)
        if data.shape[0] > sr * MAX_SECONDS:
            data = data[:sr * MAX_SECONDS]
        if self.trim:
            data = trim_silence(data, sr)
        if self.normalize:
            data = normalize_loudness(data)
        return data, sr

    def get(self, path, samplerate, channels):
        key = (path, samplerate, channels)
        with self._lock:
            hit = self._conv.get(key)
            if hit is not None:
                self._conv.move_to_end(key)
                return hit
        data, sr = self._decode(path)
        conv = np.ascontiguousarray(_to_channels(_resample(data, sr, samplerate), channels))
        with self._lock:
            if key not in self._conv:
                self._conv[key] = conv
                self._bytes += conv.nbytes
            while self._bytes > self.budget and len(self._conv) > 1:
                _k, old = self._conv.popitem(last=False)
                self._bytes -= old.nbytes
        return conv

    def usage(self):
        """(bytes used, budget bytes, entries)"""
        with self._lock:
            return self._bytes, self.budget, len(self._conv)

    def set_processing(self, trim, normalize):
        if (trim, normalize) != (self.trim, self.normalize):
            self.trim, self.normalize = trim, normalize
            self.clear()

    def clear(self):
        with self._lock:
            self._conv.clear()
            self._bytes = 0


def trim_silence(data, sr, floor_db=-45.0, pad_ms=15.0):
    """Cut the quiet lead-in and tail so a hotkey sounds instantly.

    'Quiet' is relative to the file's own peak, so a soft recording is not eaten.
    """
    if data.shape[0] == 0:
        return data
    level = np.abs(data).max(axis=1)
    peak = float(level.max())
    if peak <= 1e-6:
        return data
    threshold = max(peak * 10 ** (floor_db / 20.0), 1e-4)
    loud = np.flatnonzero(level > threshold)
    pad = int(sr * pad_ms / 1000.0)
    start = max(0, int(loud[0]) - pad)
    end = min(data.shape[0], int(loud[-1]) + pad + 1)
    if start == 0 and end == data.shape[0]:
        return data
    # copy, not a view — a view would keep the whole untrimmed file alive in RAM
    return data[start:end].copy()


def normalize_loudness(data, target_rms=0.12, max_boost=6.0, peak_ceiling=0.97):
    """Scale so every clip sits at about the same loudness.

    RMS is measured only over the parts that are actually sounding, so a short shout
    with a long quiet tail is not over-boosted; the peak ceiling stops clipping.
    """
    if data.shape[0] == 0:
        return data
    mono = np.abs(data).mean(axis=1)
    peak = float(np.abs(data).max())
    if peak <= 1e-6:
        return data
    active = mono[mono > peak * 0.05]
    rms = float(np.sqrt(np.mean(active ** 2))) if active.size else peak
    gain = min(target_rms / max(rms, 1e-6), max_boost, peak_ceiling / peak)
    return (data * np.float32(gain)).astype(np.float32)


class DevicePlayer:
    """One output stream on one device, mixing any number of overlapping sounds."""

    def __init__(self, device_index):
        info = sd.query_devices(device_index)
        self.device = device_index
        self.name = info['name']
        self.channels = max(1, min(2, int(info['max_output_channels'])))
        self.samplerate = int(info['default_samplerate']) or 48000
        self._voices = []
        self._lock = threading.Lock()
        self._live = deque()
        self._live_len = 0
        self._live_max = int(self.samplerate * LIVE_MAX_MS / 1000)
        self.live_gain = 1.0
        self.stream = sd.OutputStream(
            device=device_index,
            samplerate=self.samplerate,
            channels=self.channels,
            dtype='float32',
            latency='low',
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):  # noqa: ARG002
        outdata.fill(0.0)
        with self._lock:
            done = []
            for voice in self._voices:
                data, pos, gain = voice[0], voice[1], voice[2]
                chunk = data[pos:pos + frames]
                n = chunk.shape[0]
                if n:
                    outdata[:n] += chunk * gain
                voice[1] = pos + n
                if voice[1] >= data.shape[0]:
                    done.append(voice)
            for voice in done:
                self._voices.remove(voice)
            self._mix_live(outdata, frames)
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def _mix_live(self, outdata, frames):
        """Called with the lock held: drain the mic backlog into this block."""
        written = 0
        while written < frames and self._live:
            chunk = self._live[0]
            take = min(frames - written, chunk.shape[0])
            outdata[written:written + take] += chunk[:take] * self.live_gain
            if take == chunk.shape[0]:
                self._live.popleft()
            else:
                self._live[0] = chunk[take:]
            self._live_len -= take
            written += take

    def push_live(self, data):
        """Queue mic audio already converted to this device's samplerate and channels."""
        if data.shape[0] == 0:
            return
        with self._lock:
            self._live.append(data)
            self._live_len += data.shape[0]
            while self._live_len > self._live_max and self._live:
                dropped = self._live.popleft()
                self._live_len -= dropped.shape[0]

    def clear_live(self):
        with self._lock:
            self._live.clear()
            self._live_len = 0

    def play(self, data, gain=1.0, exclusive=True):
        with self._lock:
            if exclusive:
                self._voices.clear()
            self._voices.append([data, 0, float(gain)])

    def stop(self):
        with self._lock:
            self._voices.clear()

    def busy(self):
        with self._lock:
            return bool(self._voices)

    def close(self):
        try:
            self.stop()
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class MicInput:
    """Reads the real microphone and feeds it straight into a DevicePlayer."""

    def __init__(self, device_index, sink: DevicePlayer, monitor: DevicePlayer = None,
                 fx: 'micfx.MicProcessor' = None):
        info = sd.query_devices(device_index)
        self.fx = fx                    # noise suppression / gate / auto volume (optional)
        self._to48 = self._from48 = None
        self.device = device_index
        self.name = info['name']
        self.sink = sink
        self.monitor = monitor          # your own headphones, for hearing yourself
        self.monitor_on = False
        self._mon_resampler = None
        self.channels = max(1, min(2, int(info['max_input_channels'])))
        self.muted = False
        self.peak = 0.0
        native = int(info['default_samplerate']) or 48000
        self._resampler = None
        # Matching the sink's rate avoids resampling entirely; fall back to the mic's own rate.
        for rate in (sink.samplerate, native):
            try:
                self.stream = sd.InputStream(
                    device=device_index,
                    samplerate=rate,
                    channels=self.channels,
                    dtype='float32',
                    latency='low',
                    callback=self._callback,
                )
                self.samplerate = rate
                break
            except Exception:
                if rate == native:
                    raise
        if self.samplerate != sink.samplerate:
            self._resampler = StreamResampler(self.samplerate, sink.samplerate, self.channels)
        if monitor is not None and monitor.samplerate != sink.samplerate:
            # the monitor branch may run at a different rate than the cable
            self._mon_resampler = StreamResampler(sink.samplerate, monitor.samplerate, 1)
        if fx is not None:
            # voice processing runs at 48 kHz mono: bridge the mic and the cable to it
            if self.samplerate != micfx.RATE:
                self._to48 = StreamResampler(self.samplerate, micfx.RATE, 1)
            if sink.samplerate != micfx.RATE:
                self._from48 = StreamResampler(micfx.RATE, sink.samplerate, 1)
            fx.attach()                 # stream not running yet: safe to load RNNoise
        self.stream.start()

    def _process_fx(self, indata):
        mono = indata.mean(axis=1, keepdims=True) if indata.shape[1] > 1 else indata
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        if self._to48 is not None:
            mono = self._to48.process(mono)
        out = self.fx.process(mono[:, 0])[:, None]
        if self._from48 is not None:
            out = self._from48.process(np.ascontiguousarray(out))
        return out

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        fx = self.fx
        if fx is not None and fx.active:
            block = self._process_fx(indata)
        else:
            block = indata if self._resampler is None else self._resampler.process(indata)
        peak = float(np.abs(block).max()) if block.shape[0] else 0.0
        self.peak = max(peak, self.peak * 0.75)
        if self.muted or block.shape[0] == 0:
            return
        # indata is a reused buffer, so always hand the sink a copy of its own.
        self.sink.push_live(np.array(_to_channels(block, self.sink.channels), dtype=np.float32))

        mon = self.monitor
        if mon is not None and self.monitor_on:
            mono = block.mean(axis=1, keepdims=True) if block.shape[1] > 1 else block
            if self._mon_resampler is not None:
                mono = self._mon_resampler.process(np.ascontiguousarray(mono, dtype=np.float32))
            if mono.shape[0]:
                mon.push_live(np.array(_to_channels(mono, mon.channels), dtype=np.float32))

    def read_peak(self):
        peak, self.peak = self.peak, self.peak * 0.5
        return peak

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        if self.fx is not None:
            self.fx.detach()            # stream stopped: safe to free RNNoise
        self.sink.clear_live()
        if self.monitor is not None:
            self.monitor.clear_live()


class Engine:
    """Plays a file to the game device (virtual cable) and, optionally, to your own headphones."""

    def __init__(self):
        self.cache = SoundCache()
        self.players = {}     # role -> DevicePlayer
        self.gains = {'game': 1.0, 'monitor': 1.0}
        self.mic = None
        self.mic_device = None
        self.mic_gain = 1.0
        self.micfx = micfx.MicProcessor()
        self._preview = None
        self.monitor_self = False
        self.exclusive = True
        self._lock = threading.Lock()

    def set_device(self, role, device_index):
        # the mic feeds both the cable and (optionally) the headphones, so either
        # device changing means it has to be rebuilt against the new players
        if role in ('game', 'monitor'):
            self._stop_mic()
        with self._lock:
            old = self.players.pop(role, None)
        if old is not None:
            old.close()
        player = None
        if device_index is not None:
            player = DevicePlayer(device_index)
            with self._lock:
                self.players[role] = player
        if role in ('game', 'monitor'):
            self._start_mic()   # follow the new devices
        return player

    # -- microphone passthrough: real mic -> the same virtual cable the game listens to --
    def _stop_mic(self):
        mic, self.mic = self.mic, None
        if mic is not None:
            mic.close()

    def _start_mic(self):
        if self.mic_device is None:
            return None
        sink = self.players.get('game')
        if sink is None:
            return None
        sink.live_gain = self.mic_gain
        monitor = self.players.get('monitor')
        self.micfx.aec_hint = monitor.name if monitor is not None else ''
        self.mic = MicInput(self.mic_device, sink, monitor, self.micfx)
        self.mic.monitor_on = self.monitor_self
        return self.mic

    def set_monitor_self(self, on):
        """Hear your own mic in the headphones — needed to tune the voice changer."""
        self.monitor_self = bool(on)
        if self.mic is not None:
            self.mic.monitor_on = self.monitor_self
            if not self.monitor_self and self.mic.monitor is not None:
                self.mic.monitor.clear_live()
        return self.monitor_self

    def can_monitor_self(self):
        return self.mic is not None and self.mic.monitor is not None

    def set_mic(self, device_index):
        self._stop_mic()
        self.mic_device = device_index
        return self._start_mic()

    def set_mic_fx(self, denoise=None, gate=None, sensitivity=None, agc=None,
                   aec=None, aec_device=None):
        """Change voice processing live. Loading or unloading RNNoise or the echo
        canceller restarts the mic stream (~0.1 s), so they are never swapped under
        the audio callback."""
        fx = self.micfx
        before = fx.loaded_parts
        for name, value in (('denoise', denoise), ('gate', gate), ('agc', agc), ('aec', aec)):
            if value is not None:
                setattr(fx, name, bool(value))
        if sensitivity is not None:
            fx.sensitivity = float(sensitivity)
        if aec_device is not None:
            fx.aec_device = aec_device
        if fx.loaded_parts != before and self.mic is not None:
            muted = self.mic.muted
            self._stop_mic()
            if self._start_mic() is not None:
                self.mic.muted = muted      # a restart must not un-mute the mic

    def set_mic_gain(self, gain):
        self.mic_gain = float(gain)
        sink = self.players.get('game')
        if sink is not None:
            sink.live_gain = self.mic_gain

    def mic_peak(self):
        return self.mic.read_peak() if self.mic is not None else 0.0

    def device_ok(self, role):
        with self._lock:
            return role in self.players

    def play(self, path, gain=1.0):
        """gain is the per-sound volume, on top of each device's own slider."""
        with self._lock:
            items = list(self.players.items())
        if not items:
            raise RuntimeError('no output device selected')
        for role, player in items:
            data = self.cache.get(path, player.samplerate, player.channels)
            player.play(data, gain=self.gains.get(role, 1.0) * gain, exclusive=self.exclusive)

    # -- preview: your ears only, never the game device --
    def _preview_player(self):
        monitor = self.players.get('monitor')
        if monitor is not None:
            return monitor, self.gains.get('monitor', 1.0)
        existing = getattr(self, '_preview', None)
        if existing is None:
            game = self.players.get('game')
            idx = safe_monitor_device(exclude={game.device} if game else ())
            if idx is None:
                return None, 0.0
            existing = DevicePlayer(idx)
            self._preview = existing
        return existing, 1.0

    def preview(self, path):
        """Audition a file through the headphones without sending it to the cable."""
        player, gain = self._preview_player()
        if player is None:
            raise RuntimeError('ไม่มีอุปกรณ์สำหรับฟัง — เลือกช่อง "ฟังเองที่หูฟัง" ก่อน')
        data = self.cache.get(path, player.samplerate, player.channels)
        player.play(data, gain=max(0.3, gain), exclusive=True)
        return player.name

    def preview_data(self, samples, samplerate):
        """Same as preview(), for audio that only exists in memory (the YouTube clipper)."""
        player, gain = self._preview_player()
        if player is None:
            raise RuntimeError('ไม่มีอุปกรณ์สำหรับฟัง — เลือกช่อง "ฟังเองที่หูฟัง" ก่อน')
        data = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        data = np.ascontiguousarray(_to_channels(_resample(data, samplerate, player.samplerate),
                                                 player.channels))
        player.play(data, gain=max(0.3, gain), exclusive=True)
        return player.name

    def stop(self):
        with self._lock:
            players = list(self.players.values())
        preview = getattr(self, '_preview', None)
        if preview is not None:
            players.append(preview)
        for player in players:
            player.stop()

    def close(self):
        self._stop_mic()
        with self._lock:
            players = list(self.players.values())
            self.players.clear()
        preview = getattr(self, '_preview', None)
        if preview is not None:
            players.append(preview)
            self._preview = None
        for player in players:
            player.close()


def list_output_devices():
    """[(index, label)] for every output device, host API included in the label."""
    out = []
    apis = sd.query_hostapis()
    for idx, dev in enumerate(sd.query_devices()):
        if dev['max_output_channels'] < 1:
            continue
        api = apis[dev['hostapi']]['name']
        out.append((idx, f"{dev['name']}  [{api}]"))
    return out


def list_input_devices():
    """[(index, label)] for every recording device, host API included in the label."""
    out = []
    apis = sd.query_hostapis()
    for idx, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] < 1:
            continue
        api = apis[dev['hostapi']]['name']
        out.append((idx, f"{dev['name']}  [{api}]"))
    return out


def default_input_device():
    """Windows' current default recording device, or None."""
    try:
        idx = sd.default.device[0]
        return idx if idx is not None and idx >= 0 else None
    except Exception:
        return None


def guess_mic_device(devices):
    """Default mic, but never the virtual cable itself (that would feed back)."""
    cable = ('cable output', 'vb-audio', 'voicemeeter', 'virtual cable', 'voicemod')
    default = default_input_device()
    for idx, label in devices:
        if idx == default and not any(n in label.lower() for n in cable):
            return idx
    for idx, label in devices:
        if not any(n in label.lower() for n in cable):
            return idx
    return None


CABLE_NEEDLES = ('cable input', 'cable in ', 'cable in16', 'vb-audio', 'voicemeeter input',
                 'voicemod virtual', 'virtual cable')


def rank_cable_devices(devices):
    """Virtual-cable playback devices, best candidate first."""
    scored = []
    for idx, label in devices:
        low = label.lower()
        if not any(n in low for n in CABLE_NEEDLES) or 'wdm-ks' in low:
            continue
        score = 0
        if 'cable input' in low:
            score += 2
        if 'wasapi' in low:
            score += 3
        elif 'directsound' in low:
            score += 1
        scored.append((-score, idx))
    return [idx for _s, idx in sorted(scored)]


def can_open(device_index):
    """True if an output stream on this device actually opens."""
    try:
        player = DevicePlayer(device_index)
    except Exception:
        return False
    player.close()
    return True


def is_cable(label):
    return any(n in label.lower() for n in CABLE_NEEDLES)


def safe_monitor_device(exclude=()):
    """A device you can actually hear yourself on.

    Never a virtual cable: the VB-CABLE installer makes itself the Windows default
    playback device, so falling back to the default blindly would send a preview
    straight into the game.
    """
    devices = list_output_devices()
    labels = dict(devices)

    def usable(idx):
        label = labels.get(idx, '').lower()
        if idx in exclude or not label or is_cable(label) or 'wdm-ks' in label:
            return False
        return can_open(idx)

    default = default_output_device()
    if default is not None and usable(default):
        return default

    def rank(pair):
        label = pair[1].lower()
        return (1 if 'steam streaming' in label else 0,     # prefer real hardware
                0 if 'wasapi' in label else 1)

    for idx, _label in sorted(devices, key=rank):
        if usable(idx):
            return idx
    return None


def guess_cable_device(devices, verify=True):
    """Best virtual-cable device, preferring one that really opens.

    Windows can list endpoints that no longer exist — VB-CABLE often leaves a phantom
    'CABLE Input' behind while the working half is 'CABLE In 16ch' — so the top-ranked
    name is not trusted until a stream on it has opened.
    """
    ranked = rank_cable_devices(devices)
    if not ranked:
        return None
    if not verify:
        return ranked[0]
    for idx in ranked:
        if can_open(idx):
            return idx
    return ranked[0]


def default_output_device():
    """Windows' current default playback device, or None."""
    try:
        idx = sd.default.device[1]
        return idx if idx is not None and idx >= 0 else None
    except Exception:
        return None
