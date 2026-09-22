"""Sound sources the downloader can search: Dota 2 voice lines, Myinstants and TiengDong."""
import html as html_mod
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def clean(text, limit=70):
    return BAD_CHARS.sub('', html_mod.unescape(text or '')).strip()[:limit]


class Source:
    """An entry is {'id', 'text', 'creator', 'url'}."""

    key = ''
    label = ''
    hint = ''

    def search(self, query):
        raise NotImplementedError

    def filename(self, entry):
        stem = ' - '.join(x for x in (entry['id'], clean(entry['creator'], 30),
                                      clean(entry['text'])) if x)
        ext = os.path.splitext(urllib.parse.urlparse(entry['url']).path)[1] or '.mp3'
        return (stem or entry['id']) + ext


class DotaVoiceLines(Source):
    """The front page builds its table from this fragment, which is plain HTML."""

    key = 'dota'
    label = 'Dota 2'
    hint = 'ค้นจากชื่อคนพากย์ ข้อความ หรือ ID เช่น 401945'
    PAGE = 'https://dota2voicelines.com/src/voice_lines_list.html'
    ROW_RE = re.compile(
        r"togglePlay\('(?P<id>\d+)',\s*'(?P<url>[^']+)'.*?"
        r'<span class="voice-line-text"[^>]*>(?P<text>.*?)</span>.*?'
        r'<td class="creator">(?P<creator>[^<]*)</td>',
        re.S)

    def __init__(self):
        self._catalog = None

    def catalog(self):
        if self._catalog is None:
            page = fetch(self.PAGE)
            self._catalog = {m.group('id'): {
                'id': m.group('id'),
                'url': m.group('url'),
                'text': html_mod.unescape(m.group('text')).strip(),
                'creator': html_mod.unescape(m.group('creator')).strip(),
            } for m in self.ROW_RE.finditer(page)}
        return self._catalog

    def search(self, query):
        needle = (query or '').strip().lower()
        items = self.catalog().values()
        if not needle:
            return sorted(items, key=lambda e: e['id'])
        hits = [e for e in items
                if needle in e['text'].lower() or needle in e['creator'].lower()
                or needle == e['id']]
        return sorted(hits, key=lambda e: e['id'])


class MyInstants(Source):
    key = 'myinstants'
    label = 'Myinstants'
    hint = 'พิมพ์คำค้น หรือวางลิงก์หน้าใดก็ได้ของ myinstants.com'
    BASE = 'https://www.myinstants.com'
    SEARCH = BASE + '/en/search/?name={}'
    PAGES = 3
    ITEM_RE = re.compile(
        r"onclick=\"play\('(?P<path>[^']+)',\s*'loader-(?P<id>\d+)',\s*'(?P<slug>[^']*)'\)"
        r'.*?<a href="[^"]*" class="instant-link[^"]*">(?P<name>.*?)</a>',
        re.S)

    def _parse(self, page):
        out = []
        for m in self.ITEM_RE.finditer(page):
            path = m.group('path')
            out.append({
                'id': m.group('id'),
                'text': html_mod.unescape(m.group('name')).strip(),
                'creator': 'myinstants',
                'url': path if path.startswith('http') else self.BASE + path,
                'page': f"{self.BASE}/en/instant/{m.group('slug')}/",
            })
        return out

    def search(self, query):
        query = (query or '').strip()
        # a pasted myinstants link means "list whatever is on that page"
        if query.lower().startswith('http') and 'myinstants.com' in query.lower():
            return self._dedupe(self._parse(fetch(query)))
        if not query:
            return []
        hits = []
        for page_no in range(1, self.PAGES + 1):
            url = self.SEARCH.format(urllib.parse.quote(query))
            if page_no > 1:
                url += f'&page={page_no}'
            try:
                found = self._parse(fetch(url))
            except Exception:
                if page_no == 1:        # a real failure; anything later just means no more pages
                    raise
                break
            if not found:
                break
            hits += found
        return self._dedupe(hits)

    @staticmethod
    def _dedupe(entries):
        seen, out = set(), []
        for e in entries:
            if e['id'] in seen:
                continue
            seen.add(e['id'])
            out.append(e)
        return out

    def filename(self, entry):
        name = clean(entry['text']) or entry['id']
        ext = os.path.splitext(urllib.parse.urlparse(entry['url']).path)[1] or '.mp3'
        return f'{name} [{entry["id"]}]{ext}'



class Robots:
    """robots.txt with Google-style wildcards.

    urllib.robotparser treats '*' literally, so for rules like 'Disallow: *?s=*' it
    wrongly says the URL is allowed. Longest matching rule wins; Allow wins a tie.
    """

    def __init__(self, text):
        self.rules = []
        applies = False
        for raw in text.splitlines():
            line = raw.split('#', 1)[0].strip()
            if ':' not in line:
                continue
            field, value = (s.strip() for s in line.split(':', 1))
            field = field.lower()
            if field == 'user-agent':
                applies = value == '*'
            elif applies and field in ('allow', 'disallow') and value:
                self.rules.append((field == 'allow', value, self._compile(value)))

    @staticmethod
    def _compile(pattern):
        anchored = pattern.endswith('$')
        body = pattern[:-1] if anchored else pattern
        rx = ''.join('.*' if ch == '*' else re.escape(ch) for ch in body)
        if not body.startswith('*'):
            rx = '^' + rx
        return re.compile(rx + ('$' if anchored else ''))

    def allowed(self, url):
        parts = urllib.parse.urlsplit(url)
        target = (parts.path or '/') + (('?' + parts.query) if parts.query else '')
        best = None
        for allow, pattern, rx in self.rules:
            if rx.search(target):
                key = (len(pattern), allow)
                if best is None or key > best[0]:
                    best = (key, allow)
        return True if best is None else best[1]


class TiengDong(Source):
    """tiengdong.com/th (Thai section) — its robots.txt forbids automated site search
    and the paged "latest" list, so this source only reads what it allows: the category
    pages and each sound's own page. Filtering by name happens here, on pages already
    loaded. Thai list pages carry no mp3 link, so resolve() opens the sound's page
    (once, cached) right before a preview or download."""

    key = 'tiengdong'
    label = 'TiengDong'
    hint = 'เลือกหมวดด้านขวา แล้วพิมพ์เพื่อกรอง หรือวางลิงก์หน้าเสียงของเว็บ'
    ROOT = 'https://tiengdong.com'
    BASE = ROOT + '/th'
    PAGES = 3                      # หน้าละ ~20-60 เสียง
    DELAY = 0.6                    # เว้นจังหวะระหว่างหน้า ไม่รัวเซิร์ฟเวอร์เขา
    CATEGORIES = [
        ('หน้าแรก', ''),              # หน้าเดียว — /th/page/* ห้ามตาม robots.txt
        ('เสียงมีมไทย', 'th-meme-sound-effects'),
        ('เสียงแนวโน้ม', 'th-viral-sound-effects'),
        ('เสียงหัวเราะ', 'th-laugh-sound-effects'),
        ('เสียงน้าค่อม', 'tag/th-kom-chauncheun-sound-effects'),
        ('เอฟเฟกต์ streamer', 'tag/th-streamer-sound-effects'),
        ('เสียงประกอบ', 'th-sound-effects'),
        ('เสียงต่อสู้ อาวุธ', 'th-fighting-weapons-sound-effects'),
        ('เสียงปืน', 'tag/th-gun-sound-effects'),
        ('เสียงสยองขวัญ', 'th-horror-ferocious-sound-effects'),
        ('เสียงสัตว์', 'th-animal-sound-effects'),
        ('เสียงแมวร้อง', 'tag/th-cat-meow-sound-effects'),
        ('เสียงการจราจร', 'th-vehicle-sound-effects'),
        ('ระฆัง นกหวีด', 'th-bells-whistles-sound-effects'),
        ('เสียงแจ้งเตือน', 'tag/th-notification-sounds'),
        ('เสียงเรียกเข้า', 'th-ringtones'),
        ('ริงโทนพี่เอก (HRK)', 'tag/th-hrk-ringtones'),
    ]
    # รายการในหน้าหมวด (ภาษาไทย): มีแค่ชื่อกับลิงก์หน้าเสียง ไม่มีลิงก์ mp3
    ITEM_RE = re.compile(
        r'<li class="audio-play-item">[\s\S]*?<a href="(?P<page>https://tiengdong\.com/th/th(?P<id>\d+))/?"[^>]*>'
        r'\s*(?P<name>[^<]+?)\s*</a>')
    # หน้าของเสียงแต่ละตัว: เสียงหลักอยู่ในเครื่องเล่น <audio>
    MAIN_RE = re.compile(r'<audio[^>]*id="audio-(?P<id>\d+)-[^"]*"[\s\S]*?<source[^>]*src="(?P<url>[^"?]+\.mp3)')
    H1_RE = re.compile(r'<h1[^>]*>([\s\S]*?)</h1>')

    def __init__(self):
        self.category = self.CATEGORIES[0][0]
        self._robots = None
        self._pages = {}           # url -> [entries]

    # -- polite, robots-aware fetching --
    def robots(self):
        if self._robots is None:
            self._robots = Robots(fetch(self.ROOT + '/robots.txt'))
        return self._robots

    def _get(self, url):
        if not url.startswith(self.ROOT):
            raise ValueError('ลิงก์นี้ไม่ใช่ของ tiengdong.com')
        if not self.robots().allowed(url):
            raise PermissionError('เว็บนี้ไม่อนุญาตให้โปรแกรมเปิดหน้านี้ (robots.txt) — '
                                  'ถ้าเป็นหน้าค้นหา ใช้ปุ่มเปิดในเบราว์เซอร์แทน')
        if url not in self._pages:
            if self._pages:
                time.sleep(self.DELAY)
            self._pages[url] = self._parse(fetch(url))
        return self._pages[url]

    def _parse(self, page):
        out = []
        main = self.MAIN_RE.search(page)
        if main:
            title = self.H1_RE.search(page)
            name = re.sub(r'<[^>]+>', '', title.group(1)) if title else main.group('id')
            out.append({'id': 'td' + main.group('id'), 'text': html_mod.unescape(name).strip(),
                        'creator': 'tiengdong', 'url': main.group('url'), 'page': ''})
        for m in self.ITEM_RE.finditer(page):
            out.append({'id': 'td' + m.group('id'),
                        'text': html_mod.unescape(m.group('name')).strip(),
                        'creator': 'tiengdong', 'url': '', 'page': m.group('page')})
        return out

    def category_urls(self, label):
        slug = dict(self.CATEGORIES).get(label, '')
        if not slug:
            return [self.BASE + '/']
        root = f'{self.BASE}/{slug}'
        return [root] + [f'{root}/page/{n}' for n in range(2, self.PAGES + 1)]

    def load_category(self, label=None):
        entries = []
        for url in self.category_urls(label or self.category):
            try:
                found = self._get(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and entries:      # หมวดเล็ก มีหน้าเดียว
                    break
                raise
            if not found:
                break
            entries += found
        return MyInstants._dedupe(entries)

    def resolve(self, entry):
        """Fill entry['url'] from the sound's own page (list pages have no mp3 link)."""
        if not entry.get('url'):
            found = [e for e in self._get(entry['page']) if not e['page']]
            if not found:
                raise ValueError('หาไฟล์เสียงในหน้านี้ไม่เจอ')
            entry['url'] = found[0]['url']
        return entry

    def search_url(self, query):
        """For the 'open in browser' button — a person searching is fine, a bot is not."""
        return f'{self.BASE}/?s={urllib.parse.quote(query)}'

    def search(self, query):
        query = (query or '').strip()
        if query.lower().startswith('http'):
            return MyInstants._dedupe(self._get(query))
        entries = self.load_category()
        words = fold(query).split()
        return [e for e in entries if all(w in fold(e['text']) for w in words)]

    def filename(self, entry):
        name = clean(entry['text']) or entry['id']
        return f'{name} [{entry["id"]}].mp3'


def fold(text):
    """Lower-case and strip accents, so 'cuoi' finds 'cười' and 'dan' finds 'đàn'."""
    text = unicodedata.normalize('NFKD', (text or '').lower().replace('đ', 'd'))
    return ''.join(ch for ch in text if not unicodedata.combining(ch))


SOURCES = [DotaVoiceLines(), MyInstants(), TiengDong()]
BY_KEY = {s.key: s for s in SOURCES}
BY_LABEL = {s.label: s for s in SOURCES}


CHUNK = 32 * 1024


def download(entry, out_dir, source=None, on_progress=None):
    """Save one entry's audio into out_dir and return the path.

    on_progress(done_bytes, total_bytes) is called as it streams; total is 0 when
    the server does not send a Content-Length.
    """
    source = source or BY_KEY.get(entry.get('creator')) or BY_LABEL.get(entry.get('creator')) or SOURCES[0]
    if not entry.get('url') and hasattr(source, 'resolve'):
        source.resolve(entry)       # TiengDong: เปิดหน้าเสียงหาลิงก์ mp3 ตอนจะใช้จริงเท่านั้น
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, source.filename(entry))
    if os.path.exists(path):
        if on_progress:
            size = os.path.getsize(path)
            on_progress(size, size)
        return path

    part = path + '.part'
    req = urllib.request.Request(entry['url'], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        done = 0
        if on_progress:
            on_progress(0, total)
        with open(part, 'wb') as fh:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    os.replace(part, path)          # never leave a half file where the app can load it
    return path
