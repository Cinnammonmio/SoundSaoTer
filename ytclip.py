"""Cut a short sound out of a YouTube video (or anything else yt-dlp understands).

RAM is the point of the design. yt-dlp and ffmpeg (through PyAV) are big, so they
never load into the app itself: every job runs in a short-lived child process —
the frozen exe started again with --yt-worker — that writes its result to a file
and exits, handing all of its memory back to Windows.

The child also downloads only what it needs: PyAV opens the audio stream over
HTTP, seeks to the wanted second and decodes just that window (a 30 s window is
about 0.5 MB of the stream, not the whole video).

The app side only keeps the decoded window (mono float32, ~5.8 MB per 30 s) and
draws it — frequency view (spectrogram) or waveform — with plain numpy.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

import numpy as np

RATE = 48000                     # YouTube audio is opus @ 48 kHz — keep it, no resampling loss
WORK_DIR = os.path.join(tempfile.gettempdir(), 'SoundSaoTer_yt')
OVERVIEW_RATE = 8000             # the whole-clip strip only needs loudness, not fidelity
OVERVIEW_BINS = 1200
OVERVIEW_MAX_S = 20 * 60         # longer videos: skip the strip, it would pull the whole file
WORKER_FLAG = '--yt-worker'


# ======================================================================== child side
class _Quiet:
    def debug(self, _msg):
        pass

    warning = info = error = debug


def _extract(url):
    import yt_dlp
    opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True, 'skip_download': True,
            'format': 'bestaudio/best', 'logger': _Quiet(), 'socket_timeout': 20}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get('_type') == 'playlist':
        entries = [e for e in info.get('entries') or [] if e]
        if not entries:
            raise ValueError('ลิงก์นี้เป็นเพลย์ลิสต์ที่ไม่มีคลิป')
        info = entries[0]
    if info.get('is_live'):
        raise ValueError('ไลฟ์สดตัดไม่ได้ — รอให้ไลฟ์จบเป็นคลิปก่อน')
    if not info.get('url'):
        raise ValueError('หาไฟล์เสียงของคลิปนี้ไม่เจอ')
    return {'id': str(info.get('id') or 'clip'), 'title': info.get('title') or '',
            'duration': float(info.get('duration') or 0), 'stream': info['url'],
            'headers': info.get('http_headers') or {}, 'page': url}


def _open(info):
    import av
    headers = ''.join(f'{k}: {v}\r\n' for k, v in info['headers'].items())
    return av.open(info['stream'], options={'headers': headers}, timeout=20)


def _window(info, start, length):
    """Decode [start, start+length) seconds to mono float32 @ RATE."""
    import av
    container = _open(info)
    try:
        stream = container.streams.audio[0]
        if start > 0.5:
            container.seek(int(max(0.0, start - 1.0) / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format='flt', layout='mono', rate=RATE)
        chunks, first, end = [], None, start + length
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            ts = float(frame.pts * stream.time_base)
            if first is None:
                first = ts
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1).copy())
            if ts > end:
                break
    finally:
        container.close()
    if first is None:
        raise ValueError('ช่วงนี้ไม่มีเสียง (เลยความยาวคลิปแล้ว?)')
    data = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    offset = int(round((start - first) * RATE))
    if offset < 0:                                  # landed a hair late: pad the gap
        data = np.concatenate([np.zeros(-offset, np.float32), data])
        offset = 0
    return np.ascontiguousarray(data[offset:offset + int(round(length * RATE))], dtype=np.float32)


def _overview(info):
    """Loudness envelope of the whole clip, streamed — never holds the audio."""
    import av
    duration = info['duration']
    container = _open(info)
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format='flt', layout='mono', rate=OVERVIEW_RATE)
        per = max(1.0, duration * OVERVIEW_RATE / OVERVIEW_BINS)
        power = np.zeros(OVERVIEW_BINS)
        count = np.zeros(OVERVIEW_BINS)
        pos = 0
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                a = out.to_ndarray().reshape(-1)
                idx = np.minimum(((pos + np.arange(len(a))) / per).astype(np.int64), OVERVIEW_BINS - 1)
                power += np.bincount(idx, weights=a.astype(np.float64) ** 2, minlength=OVERVIEW_BINS)
                count += np.bincount(idx, minlength=OVERVIEW_BINS)
                pos += len(a)
    finally:
        container.close()
    env = np.sqrt(power / np.maximum(count, 1))
    env /= max(float(env.max()), 1e-6)
    return [round(float(v), 3) for v in env]


def _handle(req, work_dir):
    op = req['op']
    if op in ('open', 'window'):
        info = req.get('info') or _extract(req['url'])
        start = max(0.0, float(req.get('start') or 0.0))
        length = float(req['length'])
        if info['duration']:
            start = min(start, max(0.0, info['duration'] - 1.0))
            length = min(length, info['duration'] - start)
        try:
            data = _window(info, start, length)
        except Exception:
            if not req.get('info'):
                raise
            info = _extract(info['page'])           # stream links expire after a few hours
            data = _window(info, start, length)
        import soundfile as sf
        wav = os.path.join(work_dir, f'win_{os.getpid()}_{time.time_ns()}.wav')
        sf.write(wav, data, RATE, subtype='FLOAT')
        return {'info': info, 'start': start, 'wav': wav}
    if op == 'overview':
        return {'env': _overview(req['info'])}
    raise ValueError(f'unknown op {op}')


def _friendly(exc):
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(exc)).replace('ERROR: ', '')
    text = re.sub(r'^\[[\w:]+\] [\w-]+: ', '', text)       # "[youtube] abc123: ..."
    low = text.lower()
    for key, msg in (('private video', 'คลิปนี้เป็นคลิปส่วนตัว'),
                     ('sign in to confirm your age', 'คลิปนี้จำกัดอายุ ต้องล็อกอินถึงจะดูได้ — ตัดไม่ได้'),
                     ('members-only', 'คลิปนี้สำหรับสมาชิกช่องเท่านั้น'),
                     ('unavailable', 'คลิปนี้ดูไม่ได้ (ไม่มีอยู่ ถูกลบ หรือถูกบล็อก)'),
                     ('is not a valid url', 'ลิงก์ไม่ถูกต้อง'),
                     ('unsupported url', 'ลิงก์นี้ไม่รองรับ — ใช้ลิงก์คลิป YouTube'),
                     ('getaddrinfo', 'ต่อเน็ตไม่ได้'),
                     ('timed out', 'เน็ตช้าเกินไป ลองใหม่อีกที')):
        if key in low:
            return msg
    return text[:220] or exc.__class__.__name__


def worker_main(argv):
    """Entry point of the child process: argv = [request.json, result.json]."""
    for name in ('stdout', 'stderr'):               # windowed exe: no console at all
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w'))
    req_path, out_path = argv[0], argv[1]
    with open(req_path, encoding='utf-8') as fh:
        req = json.load(fh)
    try:
        result = _handle(req, os.path.dirname(out_path))
    except Exception as exc:                        # noqa: BLE001 — reported to the UI
        result = {'error': _friendly(exc)}
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False)
    os.replace(tmp, out_path)
    return 0


# ======================================================================== app side
_live = set()
_live_lock = threading.Lock()


def _worker_cmd():
    if getattr(sys, 'frozen', False):
        return [sys.executable, WORKER_FLAG]
    return [sys.executable, os.path.abspath(__file__), WORKER_FLAG]


def run(req, timeout=180):
    """Run one job in a child process and return its result (blocking — call from a thread)."""
    os.makedirs(WORK_DIR, exist_ok=True)
    tag = f'{os.getpid()}_{time.time_ns()}'
    req_path = os.path.join(WORK_DIR, f'req_{tag}.json')
    out_path = os.path.join(WORK_DIR, f'res_{tag}.json')
    with open(req_path, 'w', encoding='utf-8') as fh:
        json.dump(req, fh, ensure_ascii=False)
    proc = subprocess.Popen(_worker_cmd() + [req_path, out_path], cwd=WORK_DIR,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=0x08000000)  # CREATE_NO_WINDOW
    with _live_lock:
        _live.add(proc)
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError('ใช้เวลานานเกินไป — เน็ตช้าหรือคลิปยาวมาก ลองใหม่อีกที')
    finally:
        with _live_lock:
            _live.discard(proc)
        _remove(req_path)
    if not os.path.exists(out_path):
        raise RuntimeError('ยกเลิกแล้ว' if proc.returncode else 'ตัวตัดเสียงปิดตัวไปกลางคัน')
    with open(out_path, encoding='utf-8') as fh:
        result = json.load(fh)
    _remove(out_path)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


def kill_all():
    with _live_lock:
        procs = list(_live)
    for proc in procs:
        try:
            proc.kill()
        except Exception:
            pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clean_work_dir():
    """Leftovers from a crash or a killed job."""
    if os.path.isdir(WORK_DIR):
        for name in os.listdir(WORK_DIR):
            _remove(os.path.join(WORK_DIR, name))


# ---------------------------------------------------------------- time text
def fmt_time(t):
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f'{int(h)}:{int(m):02d}:{s:06.3f}'
    return f'{int(m)}:{s:06.3f}'


def parse_time(text):
    """'83.5', '1:23.5', '1:02:03.25' -> seconds; None if it is not a time."""
    parts = text.strip().replace(',', '.').split(':')
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(values) <= 3 or any(v < 0 for v in values):
        return None
    total = 0.0
    for v in values:
        total = total * 60 + v
    return total


def start_from_url(url):
    """YouTube links shared 'at current time' carry t=83 / t=1m23s — start there."""
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except ValueError:
        return 0.0
    raw = (query.get('t') or query.get('start') or [''])[0]
    if not raw:
        return 0.0
    if raw.isdigit():
        return float(raw)
    m = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?', raw)
    if not m:
        return 0.0
    h, mi, s = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


# ---------------------------------------------------------------- drawing
def _rgb(hex_color):
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def make_lut(stops):
    """256-step colour ramp through the given hex colours."""
    cols = np.array([_rgb(c) for c in stops], dtype=np.float64)
    x = np.linspace(0, 1, len(stops))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, x, cols[:, c]) for c in range(3)], axis=1).astype(np.uint8)


_HANN = {}


def spectrogram_rgb(data, sr, v0, v1, width, height, lut, ref=None,
                    fmin=40.0, fmax=16000.0, n_fft=2048, floor_db=62.0):
    """Frequency view of seconds [v0, v1]: one FFT per pixel column, log-frequency rows.

    Returns (H x W x 3 uint8, ref). Pass the returned ref back in so zooming keeps
    the same brightness scale.
    """
    half = n_fft // 2
    centers = v0 + (np.arange(width) + 0.5) * (v1 - v0) / width
    idx = np.clip(np.round(centers * sr).astype(np.int64), 0, len(data))
    padded = np.concatenate([np.zeros(half, np.float32), data, np.zeros(half, np.float32)])
    window = _HANN.get(n_fft)
    if window is None:
        window = _HANN[n_fft] = np.hanning(n_fft).astype(np.float32)
    frames = padded[idx[:, None] + np.arange(n_fft)[None, :]] * window
    spec = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    del frames
    fmax = min(fmax, sr / 2 - 1)
    freqs = fmin * (fmax / fmin) ** (1 - (np.arange(height) + 0.5) / height)   # top row = high
    pos = freqs / (sr / n_fft)
    lo = np.floor(pos).astype(np.int64)
    w = (pos - lo).astype(np.float32)
    rows = spec[:, lo] * (1 - w) + spec[:, lo + 1] * w                        # W x H
    db = 20 * np.log10(rows.T + 1e-7)                                          # H x W
    if ref is None:
        ref = float(np.percentile(db, 99.7))
    norm = np.clip((db - (ref - floor_db)) / floor_db, 0, 1)
    return lut[(norm ** 1.5 * 255).astype(np.uint8)], ref


def freq_to_y(freq, height, fmin=40.0, fmax=16000.0):
    return height * (1 - np.log(freq / fmin) / np.log(fmax / fmin))


def waveform_rgb(data, sr, v0, v1, width, height, bg, fg, mid_line, peak=None):
    """Classic min/max waveform of seconds [v0, v1]."""
    out = np.empty((height, width, 3), np.uint8)
    out[:] = _rgb(bg)
    s0 = max(0, int(v0 * sr))
    s1 = min(len(data), int(np.ceil(v1 * sr)) + 1)
    seg = data[s0:s1]
    mid = (height - 1) / 2
    out[int(mid)] = _rgb(mid_line)
    if len(seg) < 2:
        return out
    if len(seg) >= width * 2:
        starts = np.linspace(0, len(seg), width + 1).astype(np.int64)[:-1]
        lo = np.minimum.reduceat(seg, starts)
        hi = np.maximum.reduceat(seg, starts)
    else:                                   # zoomed past one sample per pixel: join the dots
        x = np.linspace(0, len(seg) - 1, width + 1)
        v = np.interp(x, np.arange(len(seg)), seg)
        lo, hi = np.minimum(v[:-1], v[1:]), np.maximum(v[:-1], v[1:])
    scale = 0.95 / max(float(peak if peak is not None else np.abs(data).max()), 1e-4)
    top = np.floor(mid - hi * scale * mid)
    bot = np.ceil(mid - lo * scale * mid)
    rows = np.arange(height)[:, None]
    out[(rows >= top[None, :]) & (rows <= bot[None, :])] = _rgb(fg)
    return out


def envelope_rgb(env, width, height, bg, fg):
    out = np.empty((height, width, 3), np.uint8)
    out[:] = _rgb(bg)
    if not env:
        return out
    x = np.linspace(0, len(env) - 1, width)
    v = np.interp(x, np.arange(len(env)), env) ** 0.8         # lifted a bit: quiet parts stay visible
    bar = np.round(v * (height - 2)).astype(np.int64)
    rows = np.arange(height)[:, None]
    out[rows >= (height - 1 - bar)[None, :]] = _rgb(fg)
    return out


def dim(rgb, bg, keep=0.38):
    """The part outside the selection: pulled towards the background colour."""
    return (rgb.astype(np.float32) * keep + np.array(_rgb(bg), np.float32) * (1 - keep)).astype(np.uint8)


def fade(clip, sr, ms=6):
    """A few ms of fade at both ends so the cut never clicks."""
    clip = np.array(clip, dtype=np.float32, copy=True)
    n = min(len(clip) // 2, int(sr * ms / 1000))
    if n > 1:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)
        clip[:n] *= ramp
        clip[-n:] *= ramp[::-1]
    return clip


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == WORKER_FLAG:
        sys.exit(worker_main(sys.argv[2:]))
