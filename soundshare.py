"""Share sounds with friends through a GitHub repo of your own — one sound at a time.

Every sound is its own file in one release (tag "sounds") of a repo the app creates
for you. Next to them sits index.json, the list of what is there:

    {"format": 2, "sounds": [{"name", "file", "size", "sha256", "volume"}]}

The repo is created **private**, so the audio is not published to the world.
A friend gets in with a share code carrying the repo name and a token.

The file names on GitHub are the first 12 characters of each file's sha256 plus its
extension: unique, ascii, and the same sound uploaded twice lands on itself. The
Thai name people read lives in index.json.

Nothing here touches Tk. The token never goes into config.json — it is kept in the
Windows Credential Manager (keyring_get / keyring_set).
"""
import base64
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.github.com'
UPLOADS = 'https://uploads.github.com'
TAG = 'sounds'
INDEX_NAME = 'index.json'
DEFAULT_REPO = 'soundsaoter-sounds'
MAX_SOUND_MB = 40
UA = {'User-Agent': 'SoundSaoTer', 'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'}
CRED_TARGET = 'SoundSaoTer/github'


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def asset_name(path, digest=None):
    ext = os.path.splitext(path)[1].lower() or '.mp3'
    return (digest or sha256_of(path))[:12] + ext


def entry_for(path, name='', volume=100):
    digest = sha256_of(path)
    return {'name': name or os.path.splitext(os.path.basename(path))[0],
            'file': asset_name(path, digest), 'size': os.path.getsize(path),
            'sha256': digest, 'volume': int(volume or 100)}


# ---------------------------------------------------------------- share code
def make_code(repo, token):
    raw = json.dumps({'r': repo, 't': token}, separators=(',', ':')).encode('utf-8')
    return 'SST1-' + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def read_code(code):
    code = (code or '').strip()
    if not code.startswith('SST1-'):
        raise ValueError('รหัสชุดเสียงไม่ถูกต้อง (ต้องขึ้นต้นด้วย SST1-)')
    body = code[5:]
    body += '=' * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(body).decode('utf-8'))
        return data['r'], data['t']
    except Exception:
        raise ValueError('รหัสชุดเสียงอ่านไม่ออก — ก๊อปมาครบหรือเปล่า')


# ---------------------------------------------------------------- GitHub
class Shelf:
    """One repo's shelf of shared sounds."""

    def __init__(self, token, repo=''):
        self.token = (token or '').strip()
        self.repo = repo
        self._release = None

    # -- plumbing
    def _req(self, method, url, data=None, headers=None, raw=False, timeout=90):
        head = dict(UA, Authorization=f'Bearer {self.token}')
        if headers:
            head.update(headers)
        body = data
        if data is not None and not isinstance(data, (bytes, bytearray)):
            body = json.dumps(data).encode('utf-8')
            head['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=body, headers=head, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
        if raw:
            return payload
        return json.loads(payload.decode('utf-8')) if payload else {}

    def whoami(self):
        return self._req('GET', f'{API}/user')['login']

    def ensure_repo(self, name=DEFAULT_REPO, private=True):
        """Find (or create, private) the repo that holds the sounds."""
        owner = self.whoami()
        repo = name if '/' in name else f'{owner}/{name}'
        try:
            self._req('GET', f'{API}/repos/{repo}')
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            self._req('POST', f'{API}/user/repos',
                      {'name': repo.split('/')[-1], 'private': bool(private), 'auto_init': True,
                       'description': 'ชุดเสียงของ SoundSaoTer'})
        self.repo = repo
        self._release = None
        return repo

    def release(self, create=False):
        if self._release is None or create:
            try:
                self._release = self._req('GET', f'{API}/repos/{self.repo}/releases/tags/{TAG}')
            except urllib.error.HTTPError as exc:
                if exc.code != 404 or not create:
                    raise
                self._release = self._req('POST', f'{API}/repos/{self.repo}/releases',
                                          {'tag_name': TAG, 'name': 'ชุดเสียง',
                                           'body': 'เสียงที่แชร์จาก SoundSaoTer'})
        return self._release

    def _assets(self, create=False):
        return {a['name']: a for a in self.release(create).get('assets', [])}

    def _put_asset(self, name, blob, content_type, on_progress=None):
        release = self.release(create=True)
        old = self._assets().get(name)
        if old:
            self._req('DELETE', f"{API}/repos/{self.repo}/releases/assets/{old['id']}")
        url = f"{UPLOADS}/repos/{self.repo}/releases/{release['id']}/assets?name={urllib.parse.quote(name)}"
        if on_progress:
            on_progress(0, len(blob))
        out = self._req('POST', url, blob, {'Content-Type': content_type}, timeout=900)
        if on_progress:
            on_progress(len(blob), len(blob))
        self._release = None                 # its asset list is stale now
        return out

    def _get_asset(self, asset, on_progress=None):
        head = dict(UA, Authorization=f'Bearer {self.token}', Accept='application/octet-stream')
        req = urllib.request.Request(f"{API}/repos/{self.repo}/releases/assets/{asset['id']}",
                                     headers=head)
        total = int(asset.get('size') or 0)
        chunks, done = [], 0
        with urllib.request.urlopen(req, timeout=600) as resp:
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                chunks.append(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        return b''.join(chunks)

    # -- the index
    def index(self):
        """What is on the shelf. An empty shelf reads as an empty list."""
        try:
            asset = self._assets().get(INDEX_NAME)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {'format': 2, 'sounds': []}
            raise
        if not asset:
            return {'format': 2, 'sounds': []}
        data = json.loads(self._get_asset(asset).decode('utf-8'))
        data.setdefault('sounds', [])
        return data

    def _save_index(self, data):
        blob = json.dumps(data, ensure_ascii=False, indent=1).encode('utf-8')
        self._put_asset(INDEX_NAME, blob, 'application/json')

    # -- one sound at a time
    def upload(self, path, name='', volume=100, on_progress=None):
        """Put one sound on the shelf. Returns (entry, 'added' | 'already there')."""
        size_mb = os.path.getsize(path) / 2 ** 20
        if size_mb > MAX_SOUND_MB:
            raise ValueError(f'ไฟล์ใหญ่เกิน {MAX_SOUND_MB} MB ({size_mb:.0f} MB)')
        entry = entry_for(path, name, volume)
        data = self.index()
        same = next((s for s in data['sounds'] if s['sha256'] == entry['sha256']), None)
        if same and same['file'] in self._assets():
            if same.get('name') != entry['name'] or same.get('volume') != entry['volume']:
                same.update(name=entry['name'], volume=entry['volume'])
                self._save_index(data)
            return same, 'already'
        with open(path, 'rb') as fh:
            blob = fh.read()
        self._put_asset(entry['file'], blob,
                        mimetypes.guess_type(path)[0] or 'application/octet-stream', on_progress)
        data['sounds'] = [s for s in data['sounds'] if s['sha256'] != entry['sha256']] + [entry]
        self._save_index(data)
        return entry, 'added'

    def download(self, entry, sounds_dir, on_progress=None):
        """Fetch one sound into sounds_dir. Returns (path, 'saved' | 'already')."""
        have = _find_same(sounds_dir, entry)
        if have:
            return have, 'already'
        asset = self._assets().get(entry['file'])
        if not asset:
            raise FileNotFoundError(f"เสียง \"{entry['name']}\" ถูกลบไปจาก repo แล้ว")
        blob = self._get_asset(asset, on_progress)
        got = hashlib.sha256(blob).hexdigest()
        if got != entry['sha256']:
            raise ValueError('ไฟล์ที่โหลดมาไม่ตรงกับต้นฉบับ (เสียหายระหว่างทาง)')
        os.makedirs(sounds_dir, exist_ok=True)
        target = os.path.join(sounds_dir, _free_name(sounds_dir, _nice_name(entry)))
        tmp = target + '.part'
        with open(tmp, 'wb') as fh:
            fh.write(blob)
        os.replace(tmp, target)
        return target, 'saved'

    def remove(self, entry):
        """Take one sound off the shelf."""
        asset = self._assets().get(entry['file'])
        if asset:
            self._req('DELETE', f"{API}/repos/{self.repo}/releases/assets/{asset['id']}")
            self._release = None
        data = self.index()
        data['sounds'] = [s for s in data['sounds'] if s['sha256'] != entry['sha256']]
        self._save_index(data)

    def code(self):
        return make_code(self.repo, self.token)


BAD_CHARS = '<>:"/\\|?*'


def _nice_name(entry):
    stem = ''.join(c for c in (entry.get('name') or 'sound') if c not in BAD_CHARS).strip()
    return (stem or 'sound')[:70] + os.path.splitext(entry['file'])[1]


def _free_name(folder, filename):
    stem, ext = os.path.splitext(filename)
    candidate, n = filename, 2
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f'{stem} ({n}){ext}'
        n += 1
    return candidate


def _find_same(folder, entry):
    """A file already in sounds/ with the same contents — no need to download again."""
    if not os.path.isdir(folder):
        return ''
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        if os.path.isfile(full) and os.path.getsize(full) == entry['size'] \
                and sha256_of(full) == entry['sha256']:
            return full
    return ''


def friendly_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        detail = ''
        try:
            detail = json.loads(exc.read().decode('utf-8')).get('message', '')
        except Exception:
            pass
        if exc.code in (401, 403):
            return 'token ใช้ไม่ได้หรือสิทธิ์ไม่พอ — ต้องติ๊กสิทธิ์ Contents: Read and write'
        if exc.code == 404:
            return 'ไม่พบ repo หรือเสียงนั้น — ตรวจรหัสชุดเสียงอีกที'
        return f'GitHub ตอบกลับ {exc.code}: {detail[:120]}'
    if isinstance(exc, urllib.error.URLError):
        return 'ต่อเน็ตไม่ได้'
    return str(exc)[:200]


# ---------------------------------------------------------------- token storage
class _Cred(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_byte)),
                ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
                ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR),
                ('UserName', wintypes.LPWSTR)]


def keyring_set(token, target=None):
    """Keep the token in the Windows Credential Manager, not in config.json."""
    target = target or CRED_TARGET          # read at call time, so tests can redirect it
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    blob = (token or '').encode('utf-16le')
    buf = (ctypes.c_byte * max(len(blob), 1)).from_buffer_copy(blob or b'\0')
    cred = _Cred(Flags=0, Type=1, TargetName=target, Comment=None,
                 LastWritten=wintypes.FILETIME(), CredentialBlobSize=len(blob),
                 CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)),
                 Persist=2, AttributeCount=0, Attributes=None, TargetAlias=None,
                 UserName='SoundSaoTer')
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_Cred), wintypes.DWORD]
    if not advapi.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f'เก็บ token ไม่ได้ (error {ctypes.get_last_error()})')


def keyring_get(target=None):
    target = target or CRED_TARGET
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_Cred))]
    ptr = ctypes.POINTER(_Cred)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        return ''
    try:
        cred = ptr.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode('utf-16le')
    finally:
        advapi.CredFree(ptr)


def keyring_clear(target=None):
    target = target or CRED_TARGET
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW(target, 1, 0)
