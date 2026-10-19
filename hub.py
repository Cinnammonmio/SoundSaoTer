"""คลังเสียงกลาง — เปิดโปรแกรมมาก็เห็นและโหลดได้เลย ไม่ต้องล็อกอิน ไม่ต้องมี token

เสียงที่ผ่านการตรวจแล้วอยู่ใน release ของ repo สาธารณะ (soundsaoter-hub) พร้อมไฟล์
index.json ที่บอกว่ามีอะไรบ้าง โปรแกรมโหลดสองอย่างนี้จาก URL ของ release ตรง ๆ —
ไม่ผ่าน GitHub API จึงไม่ติดเพดาน 60 ครั้ง/ชั่วโมงของผู้ที่ไม่ได้ล็อกอิน และไม่ต้องมี
token ของใครอยู่ในตัวโปรแกรมเลย

การส่งเสียงเข้าคลังยิงไปที่เซิร์ฟเวอร์เล็ก ๆ ของเรา (โค้ดอยู่ใน worker/) ที่ถือ token
เขียน GitHub ไว้ฝั่งเซิร์ฟเวอร์ เสียงที่ส่งเข้ามาจะไปกองอยู่ในคิวส่วนตัวก่อน
เจ้าของคลังกดอนุมัติแล้วถึงจะขึ้นคลังสาธารณะให้คนอื่นเห็น

รูปแบบ index.json:

    {"version": 1, "sounds": [{"id", "name", "file", "size", "sha256", "by", "at"}]}
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import soundshare as sh      # ใช้ตัวช่วยร่วมกัน: sha256_of / ตั้งชื่อไฟล์ / หาไฟล์ซ้ำ

OWNER = 'Cinnammonmio'
REPO = 'soundsaoter-hub'
TAG = 'sounds'
BASE = f'https://github.com/{OWNER}/{REPO}/releases/download/{TAG}'
INDEX_NAME = 'index.json'
PAGE_URL = f'https://github.com/{OWNER}/{REPO}'

# เซิร์ฟเวอร์รับเสียงเข้าคิว (Cloudflare Worker) — ตั้งทับได้ในหน้าโปรแกรม
DEFAULT_SERVER = 'https://soundsaoter-hub.cinnammonmio.workers.dev'
MAX_MB = 8                               # ใหญ่กว่านี้ไม่รับเข้าคิว
UA = {'User-Agent': 'SoundSaoTer'}
AUDIO_EXT = ('.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aiff', '.aif')
CRED_TARGET = 'SoundSaoTer/hub-admin'    # กุญแจผู้ดูแล เก็บใน Credential Manager


# ---------------------------------------------------------------- อ่าน (ไม่ต้องล็อกอิน)
def _fetch(url, timeout=25, on_progress=None, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        chunks, got = [], 0
        while True:
            block = resp.read(1 << 16)
            if not block:
                break
            chunks.append(block)
            got += len(block)
            if on_progress:
                on_progress(got, total)
    return b''.join(chunks)


def index(timeout=25):
    """รายการเสียงในคลังกลาง — ใส่เวลาต่อท้าย URL กัน CDN คืนของเก่า"""
    url = f'{BASE}/{INDEX_NAME}?t={int(time.time())}'
    try:
        data = json.loads(_fetch(url, timeout).decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'version': 1, 'sounds': []}       # ยังไม่มีเสียงสักอัน
        raise
    data.setdefault('sounds', [])
    return data


def newest_first(sounds):
    return sorted(sounds, key=lambda s: s.get('at') or '', reverse=True)


def download(entry, sounds_dir, on_progress=None):
    """โหลดเสียงหนึ่งอันลง sounds/ คืน (path, 'saved' | 'already')"""
    have = sh._find_same(sounds_dir, entry)
    if have:
        return have, 'already'
    blob = _fetch(f"{BASE}/{entry['file']}", 60, on_progress)
    if hashlib.sha256(blob).hexdigest() != entry['sha256']:
        raise ValueError('ไฟล์ที่โหลดมาไม่ตรงกับต้นฉบับ (เสียหายระหว่างทาง)')
    os.makedirs(sounds_dir, exist_ok=True)
    target = os.path.join(sounds_dir, sh._free_name(sounds_dir, sh._nice_name(entry)))
    tmp = target + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(blob)
    os.replace(tmp, target)
    return target, 'saved'


# ---------------------------------------------------------------- ส่งเข้าคิว
def _post(server, path, body=b'', headers=None, timeout=90, method='POST'):
    url = server.rstrip('/') + path
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode('utf-8')) if raw else {}


def _b64(text):
    import base64
    return base64.b64encode((text or '').encode('utf-8')).decode('ascii')


def submit(path, name='', by='', server=DEFAULT_SERVER, on_progress=None):
    """ส่งเสียงเข้าคิวรอตรวจ คืน dict ที่เซิร์ฟเวอร์ตอบกลับ"""
    if not server:
        raise ValueError('ยังไม่ได้ตั้งที่อยู่เซิร์ฟเวอร์ของคลังกลาง')
    ext = os.path.splitext(path)[1].lower()
    if ext not in AUDIO_EXT:
        raise ValueError(f'ไฟล์ {ext} ส่งเข้าคลังไม่ได้ — รับเฉพาะ {", ".join(AUDIO_EXT)}')
    size = os.path.getsize(path)
    if size > MAX_MB * 1024 * 1024:
        raise ValueError(f'ไฟล์ใหญ่เกิน {MAX_MB} MB ({size / 1048576:.1f} MB) — ตัดให้สั้นลงก่อน')
    with open(path, 'rb') as fh:
        blob = fh.read()
    if on_progress:
        on_progress(0, size)
    out = _post(server, '/upload', blob, {
        'Content-Type': 'application/octet-stream',
        'X-Sound-Name': _b64(name or os.path.splitext(os.path.basename(path))[0]),
        'X-Sound-By': _b64(by),
        'X-Sound-Ext': ext,
    })
    if on_progress:
        on_progress(size, size)
    return out


# ---------------------------------------------------------------- ฝั่งผู้ดูแล
def _key_ok(key):
    """หัว HTTP ส่งได้แต่อักขระ latin-1 — กันไม่ให้กุญแจภาษาไทยทำให้พังแบบงง ๆ"""
    key = (key or '').strip()
    if not key:
        raise ValueError('ยังไม่ได้ใส่กุญแจผู้ดูแล')
    try:
        key.encode('ascii')
    except UnicodeEncodeError:
        raise ValueError('กุญแจผู้ดูแลใช้ได้เฉพาะตัวอักษรอังกฤษ ตัวเลข และเครื่องหมาย')
    return key


def pending(server, key):
    return _post(server, '/pending', headers={'X-Admin-Key': _key_ok(key)},
                 method='GET').get('sounds', [])


def preview_url(server, key, sid):
    """ลิงก์ฟังเสียงในคิว (ยังไม่สาธารณะ) — ใส่กุญแจไปในลิงก์เพราะเล่นผ่าน URL ตรง ๆ"""
    return f"{server.rstrip('/')}/file/{urllib.parse.quote(sid)}?key={urllib.parse.quote(_key_ok(key))}"


def fetch_pending(server, key, sid, on_progress=None):
    return _fetch(preview_url(server, key, sid), 60, on_progress)


def approve(server, key, sid):
    body = json.dumps({'id': sid}).encode('utf-8')
    return _post(server, '/approve', body,
                 {'X-Admin-Key': _key_ok(key), 'Content-Type': 'application/json'})


def reject(server, key, sid):
    body = json.dumps({'id': sid}).encode('utf-8')
    return _post(server, '/reject', body,
                 {'X-Admin-Key': _key_ok(key), 'Content-Type': 'application/json'})


def remove(server, key, sid):
    """เอาเสียงที่ขึ้นคลังไปแล้วออก"""
    body = json.dumps({'id': sid}).encode('utf-8')
    return _post(server, '/remove', body,
                 {'X-Admin-Key': _key_ok(key), 'Content-Type': 'application/json'})


def ping(server):
    return _post(server, '/', method='GET', timeout=20)


# ---------------------------------------------------------------- ข้อความ error
def friendly_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        detail = ''
        try:
            detail = json.loads(exc.read().decode('utf-8')).get('error', '')
        except Exception:
            pass
        return {
            401: 'กุญแจผู้ดูแลไม่ถูกต้อง',
            403: 'เซิร์ฟเวอร์ไม่อนุญาต — ' + (detail or 'ลองใหม่อีกที'),
            404: 'ไม่พบเสียงนั้นแล้ว (อาจถูกตรวจไปก่อนหน้านี้)',
            413: f'ไฟล์ใหญ่เกิน {MAX_MB} MB',
            415: 'ไฟล์นี้ไม่ใช่ไฟล์เสียงที่รับได้',
            429: 'ส่งถี่เกินไป — ' + (detail or 'พักสักครู่แล้วลองใหม่'),
        }.get(exc.code, f'เซิร์ฟเวอร์ตอบกลับ {exc.code}: {detail[:120]}')
    if isinstance(exc, urllib.error.URLError):
        return 'ต่อเน็ตไม่ได้ — คลังกลางต้องใช้เน็ต'
    return str(exc)[:200]


# ---------------------------------------------------------------- กุญแจผู้ดูแล
def admin_key_get():
    return sh.keyring_get(target=CRED_TARGET)


def admin_key_set(key):
    return sh.keyring_set(_key_ok(key), target=CRED_TARGET)


def admin_key_clear():
    return sh.keyring_clear(target=CRED_TARGET)
