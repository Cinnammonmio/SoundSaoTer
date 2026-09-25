"""พิมพ์ข้อความแล้วให้อ่าน — เสียงอ่านจาก Google Translate ต่อท้ายเสียงแจ้งเตือนโดเนท

Google's translate_tts endpoint takes at most 200 characters per request, so a long
message is cut into pieces at spaces or punctuation, fetched one by one (with a short
pause between, to stay polite) and glued back together.

The little alert chimes are generated here with numpy — plain sine tones, nothing
sampled from anywhere. Everything ends up as one mono 24 kHz clip the app saves into
sounds/ like any other sound, so it can sit in a slot and be replayed from a hotkey,
the phone, anything.
"""
import io
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import soundfile as sf

RATE = 24000                      # what Google returns
CHUNK_CHARS = 180                 # the endpoint refuses more than 200
PAUSE = 0.35                      # between chunk requests
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
ENDPOINT = 'https://translate.google.com/translate_tts'

LANGS = [('ไทย', 'th'), ('อังกฤษ', 'en'), ('ญี่ปุ่น', 'ja'), ('จีน', 'zh-CN'),
         ('เกาหลี', 'ko'), ('เวียดนาม', 'vi')]
CHIMES = ['เหรียญ', 'กระดิ่ง', 'ดิ๊งด่อง', 'ปิ๊งป่อง']
MAX_CHARS = 1200                  # a donate message longer than this is a wall of text


# ---------------------------------------------------------------- text -> pieces
def split_text(text, limit=CHUNK_CHARS):
    """Cut at a space or a punctuation mark when possible — Thai has no spaces, so a
    long unbroken run is cut at the limit."""
    text = re.sub(r'\s+', ' ', (text or '').strip())
    out = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        window = text[:limit]
        cut = max(window.rfind(' '), window.rfind(','), window.rfind('.'),
                  window.rfind('!'), window.rfind('?'), window.rfind('ๆ'))
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return [piece for piece in out if piece]


# ---------------------------------------------------------------- Google voice
def fetch_piece(piece, lang='th', timeout=20):
    query = urllib.parse.urlencode({'ie': 'UTF-8', 'q': piece, 'tl': lang,
                                    'client': 'tw-ob', 'total': 1, 'idx': 0,
                                    'textlen': len(piece)})
    req = urllib.request.Request(f'{ENDPOINT}?{query}', headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    audio, rate = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    return _mono(audio), rate


def speak(text, lang='th', on_progress=None):
    """text -> (mono float32 @ RATE, rate). Raises with a readable message."""
    pieces = split_text(text)
    if not pieces:
        raise ValueError('ยังไม่ได้พิมพ์ข้อความ')
    if len(text) > MAX_CHARS:
        raise ValueError(f'ข้อความยาวเกิน {MAX_CHARS} ตัวอักษร')
    parts, rate = [], RATE
    for i, piece in enumerate(pieces):
        if on_progress:
            on_progress(i, len(pieces))
        try:
            audio, rate = fetch_piece(piece, lang)
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                raise ValueError('Google ไม่รับข้อความท่อนนี้ — ลองตัดให้สั้นลงหรือเอาอักขระแปลก ๆ ออก')
            if exc.code == 429:
                raise RuntimeError('Google บอกว่าขอถี่เกินไป — พักสักครู่แล้วลองใหม่')
            raise RuntimeError(f'ขอเสียงอ่านไม่สำเร็จ (HTTP {exc.code})')
        except urllib.error.URLError:
            raise RuntimeError('ต่อเน็ตไม่ได้ — เสียงอ่านต้องใช้เน็ต')
        parts.append(audio)
        if i + 1 < len(pieces):
            parts.append(np.zeros(int(rate * 0.12), np.float32))   # breath between pieces
            time.sleep(PAUSE)
    if on_progress:
        on_progress(len(pieces), len(pieces))
    return np.concatenate(parts), rate


def windows_voice(text, lang='th'):
    """Fallback when Google is unreachable: whatever voice Windows itself has."""
    import tempfile
    out = os.path.join(tempfile.gettempdir(), f'sst_sapi_{os.getpid()}.wav')
    script = ('Add-Type -AssemblyName System.Speech; '
              '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
              f"$s.SetOutputToWaveFile('{out}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
    subprocess.run(['powershell', '-NoProfile', '-Command', script], input=text, text=True,
                   encoding='utf-8', capture_output=True, timeout=60, creationflags=0x08000000)
    if not os.path.isfile(out) or os.path.getsize(out) < 1000:
        raise RuntimeError('เสียงอ่านของ Windows ใช้ไม่ได้ (อาจไม่มีเสียงภาษานี้ในเครื่อง)')
    audio, rate = sf.read(out, dtype='float32', always_2d=True)
    try:
        os.remove(out)
    except OSError:
        pass
    return _mono(audio), rate


# ---------------------------------------------------------------- alert chimes
def chime(kind='เหรียญ', rate=RATE):
    """Short alert tones, generated here — nothing sampled from anywhere."""
    def tone(freq, seconds, decay=8.0, start=0.0, level=0.5, wobble=0.0):
        n = int(rate * seconds)
        t = np.arange(n) / rate
        f = freq * (1 + wobble * t)
        wave = np.sin(2 * math.pi * f * t) + 0.35 * np.sin(4 * math.pi * f * t)
        env = np.exp(-decay * t) * level
        piece = (wave * env).astype(np.float32)
        pad = int(rate * start)
        return np.concatenate([np.zeros(pad, np.float32), piece])

    if kind == 'กระดิ่ง':
        parts = [tone(880, 1.4, 4.5), tone(1318, 1.4, 5.0, level=0.3), tone(1760, 1.0, 7.0, level=0.15)]
    elif kind == 'ดิ๊งด่อง':
        parts = [tone(1046, 0.5, 9.0), tone(784, 1.0, 5.5, start=0.28)]
    elif kind == 'ปิ๊งป่อง':
        parts = [tone(1568, 0.28, 14.0), tone(2093, 0.4, 11.0, start=0.16, level=0.4)]
    else:                                   # เหรียญ — two quick bright blips
        parts = [tone(988, 0.12, 18.0, level=0.45), tone(1318, 0.5, 9.0, start=0.09, level=0.45)]
    longest = max(len(p) for p in parts)
    mix = np.zeros(longest, np.float32)
    for part in parts:
        mix[:len(part)] += part
    peak = float(np.abs(mix).max()) or 1.0
    return (mix / peak * 0.7).astype(np.float32)


# ---------------------------------------------------------------- putting it together
def _mono(audio):
    return np.ascontiguousarray(audio.mean(axis=1) if audio.ndim > 1 else audio, dtype=np.float32)


def _resample(audio, rate_in, rate_out):
    if rate_in == rate_out or len(audio) == 0:
        return audio
    n = int(round(len(audio) * rate_out / rate_in))
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def intro_audio(intro, rate=RATE):
    """intro: None · a chime name · a path to a sound file."""
    if not intro:
        return np.zeros(0, np.float32)
    if intro in CHIMES:
        return chime(intro, rate)
    audio, file_rate = sf.read(intro, dtype='float32', always_2d=True)
    return _resample(_mono(audio), file_rate, rate)


def build(text, lang='th', intro='เหรียญ', gap=0.25, on_progress=None, use_google=True):
    """The finished clip: alert chime, a beat of silence, then the spoken message."""
    if use_google:
        speech, rate = speak(text, lang, on_progress)
    else:
        speech, rate = windows_voice(text, lang)
    head = intro_audio(intro, rate)
    silence = np.zeros(int(rate * max(0.0, gap)), np.float32) if len(head) else np.zeros(0, np.float32)
    clip = np.concatenate([head, silence, speech])
    peak = float(np.abs(clip).max())
    if peak > 0.97:
        clip = (clip / peak * 0.97).astype(np.float32)
    return clip, rate


BAD_CHARS = re.compile(r'[<>:"/\\|?*\n\r\t]')


def filename_for(text, folder, prefix='พูด'):
    stem = BAD_CHARS.sub('', (text or '').strip())[:40].strip() or 'ข้อความ'
    name = f'{prefix} - {stem}.mp3'
    candidate, n = name, 2
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f'{prefix} - {stem} ({n}).mp3'
        n += 1
    return os.path.join(folder, candidate)


def save(clip, rate, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.part'
    sf.write(tmp, clip, rate, format='MP3', subtype='MPEG_LAYER_III')
    os.replace(tmp, path)
    return path
