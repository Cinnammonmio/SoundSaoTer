"""Sound sources the downloader can search: Dota 2 voice lines and Myinstants."""
import html as html_mod
import os
import re
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


SOURCES = [DotaVoiceLines(), MyInstants()]
BY_KEY = {s.key: s for s in SOURCES}
BY_LABEL = {s.label: s for s in SOURCES}


CHUNK = 32 * 1024


def download(entry, out_dir, source=None, on_progress=None):
    """Save one entry's audio into out_dir and return the path.

    on_progress(done_bytes, total_bytes) is called as it streams; total is 0 when
    the server does not send a Content-Length.
    """
    source = source or BY_LABEL.get(entry.get('creator')) or SOURCES[0]
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
