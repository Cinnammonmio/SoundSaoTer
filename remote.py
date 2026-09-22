"""📱 Use a phone as the soundboard — a tiny web server on the home Wi-Fi.

Off unless switched on. Every request must carry the key that is in the QR code,
so someone else on the same Wi-Fi cannot trigger sounds. Nothing here touches Tk:
the server only reads a snapshot the app refreshes on its own thread, and puts
commands on the same queue the global hotkeys use.
"""
import hmac
import json
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765


def new_key():
    return secrets.token_urlsafe(9)


def lan_ip():
    """The address other devices on the Wi-Fi reach this PC at (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


class RemoteServer:
    """snapshot: callable -> dict (cheap, pre-built by the app); commands go to `events`.
    cooldown_left: callable -> seconds the anti-spam still blocks a hotkey-style play."""

    def __init__(self, events, snapshot, key, port=DEFAULT_PORT, cooldown_left=None):
        self.events = events
        self.snapshot = snapshot
        self.cooldown_left = cooldown_left or (lambda: 0)
        self.key = key
        self.port = port
        self.phones = {}                # client ip -> last seen (monotonic)
        self._httpd = None
        self._thread = None

    # ---- lifecycle
    def start(self):
        handler = type('Handler', (_Handler,), {'remote': self})
        last = None
        for port in range(self.port, self.port + 10):     # the usual port may be taken
            try:
                self._httpd = ThreadingHTTPServer(('0.0.0.0', port), handler)
                break
            except OSError as exc:
                last = exc
        if self._httpd is None:
            raise OSError(f'เปิดพอร์ตไม่ได้: {last}')
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name='phone-remote', daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    @property
    def running(self):
        return self._httpd is not None

    def url(self):
        return f'http://{lan_ip()}:{self.port}/?k={urllib.parse.quote(self.key)}'

    def connected(self, within=15.0):
        now = time.monotonic()
        return sum(1 for seen in self.phones.values() if now - seen < within)


class _Handler(BaseHTTPRequestHandler):
    remote: RemoteServer = None
    server_version = 'SoundSaoTer'

    def log_message(self, *_args):          # a windowed exe has no stderr
        pass

    def _key_ok(self, query):
        given = self.headers.get('X-Key') or (query.get('k') or [''])[0]
        return hmac.compare_digest(given.encode(), self.remote.key.encode())

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self._key_ok(query):
            return self._send(403, _LOCKED, 'text/html; charset=utf-8')
        self.remote.phones[self.client_address[0]] = time.monotonic()
        if url.path == '/':
            snap = self.remote.snapshot()
            page = _PAGE.replace('/*THEME*/', snap.get('css', ''))
            return self._send(200, page, 'text/html; charset=utf-8')
        if url.path == '/api/state':
            snap = dict(self.remote.snapshot())
            snap.pop('css', None)
            snap.pop('paths', None)
            return self._json(200, snap)
        return self._json(404, {'ok': False})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._key_ok(urllib.parse.parse_qs(url.query)):
            return self._json(403, {'ok': False, 'msg': 'รหัสไม่ถูก — สแกน QR ใหม่'})
        self.remote.phones[self.client_address[0]] = time.monotonic()
        try:
            length = min(int(self.headers.get('Content-Length') or 0), 4096)
            body = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, json.JSONDecodeError):
            body = {}
        snap = self.remote.snapshot()
        events = self.remote.events
        if url.path == '/api/play':
            path = snap.get('paths', {}).get(str(body.get('id')))
            if not path:
                return self._json(404, {'ok': False, 'msg': 'ไม่พบเสียงนี้แล้ว'})
            wait = self.remote.cooldown_left()
            if wait > 0:
                return self._json(200, {'ok': False, 'msg': f'กันสแปม: รออีก {wait:.1f} วิ'})
            events.put(('play', path))
            return self._json(200, {'ok': True})
        if url.path == '/api/random':
            events.put(('random',))
            return self._json(200, {'ok': True})
        if url.path == '/api/stop':
            events.put(('stop',))
            return self._json(200, {'ok': True})
        if url.path == '/api/mute':
            events.put(('mute', bool(body.get('on'))))
            return self._json(200, {'ok': True})
        return self._json(404, {'ok': False})


_LOCKED = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SoundSaoTer</title><body style="font-family:system-ui;background:#0f0a1e;color:#e9e4ff;display:grid;place-items:center;height:90vh;text-align:center">
<div><div style="font-size:48px">🔒</div><h2>สแกน QR จากโปรแกรมก่อน</h2>
<p style="opacity:.7">SoundSaoTer → 📱 มือถือ → สแกน QR ด้วยกล้องมือถือ</p></div>"""

_PAGE = r"""<!doctype html>
<html lang="th"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f0a1e">
<title>SoundSaoTer</title>
<style>
:root{/*THEME*/}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--text);font-family:"Segoe UI",system-ui,-apple-system,"Noto Sans Thai",sans-serif;
  padding:calc(env(safe-area-inset-top) + 12px) 14px calc(env(safe-area-inset-bottom) + 90px)}
header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.badge{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:grid;place-items:center;font-size:20px;color:var(--on);flex:none}
h1{font-size:18px;margin:0;flex:1;line-height:1.1}
h1 small{display:block;font-size:12px;font-weight:400;color:var(--dim)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--warn);flex:none}
.dot.on{background:var(--ok)}
.tabs{display:flex;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:4px;gap:4px;margin-bottom:10px}
.tabs button{flex:1;border:0;background:transparent;color:var(--dim);font:inherit;font-weight:600;padding:9px;border-radius:9px}
.tabs button.sel{background:var(--accent);color:var(--on)}
input{width:100%;font:inherit;font-size:16px;padding:11px 14px;border-radius:12px;border:1px solid var(--border);
  background:var(--input);color:var(--text);margin-bottom:10px;outline:none}
input:focus{border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.pad{position:relative;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:16px;
  padding:14px 12px 12px;min-height:92px;text-align:left;font:inherit;display:flex;flex-direction:column;justify-content:space-between;
  transition:transform .08s,background .15s,border-color .15s;overflow:hidden}
.pad:active{transform:scale(.96)}
.pad .n{font-size:12px;font-weight:700;color:var(--accent);letter-spacing:.3px}
.pad .t{font-size:15px;font-weight:600;line-height:1.25;margin-top:6px;word-break:break-word;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.pad .k{font-size:11px;color:var(--faint);margin-top:6px}
.pad.rand{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:var(--on)}
.pad.rand .n,.pad.rand .k{color:var(--on);opacity:.85}
.pad.empty{opacity:.45}
.pad.hit{border-color:var(--accent);background:var(--surface3)}
.empty-msg{color:var(--faint);text-align:center;padding:40px 10px}
.bar{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;padding:10px 14px calc(env(safe-area-inset-bottom) + 10px);
  background:linear-gradient(transparent,var(--bg) 30%)}
.bar button{flex:1;border:0;border-radius:14px;padding:15px 8px;font:inherit;font-weight:700;font-size:15px}
.stop{background:var(--danger);color:#fff}
.mute{background:var(--surface);color:var(--text);border:1px solid var(--border)!important}
.mute.on{background:var(--warn);color:#111}
.toast{position:fixed;left:50%;bottom:calc(env(safe-area-inset-bottom) + 84px);transform:translateX(-50%) translateY(20px);
  background:var(--surface3);color:var(--text);padding:10px 16px;border-radius:12px;font-size:14px;opacity:0;
  transition:.2s;pointer-events:none;max-width:90vw;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.35)}
.toast.show{opacity:1;transform:translateX(-50%)}
</style></head>
<body>
<header>
  <div class="badge">♪</div>
  <h1>SoundSaoTer<small id="sub">กำลังเชื่อมต่อ…</small></h1>
  <div class="dot" id="dot"></div>
</header>
<div class="tabs"><button id="tSlots" class="sel">🎯 ช่องคีย์ลัด</button><button id="tLib">📚 คลังเสียง</button></div>
<input id="q" placeholder="ค้นหาเสียง…" style="display:none" autocomplete="off">
<div class="grid" id="grid"></div>
<div class="bar"><button class="mute" id="mute">🎤 ไมค์เปิด</button><button class="stop" id="stop">■ หยุดเสียง</button></div>
<div class="toast" id="toast"></div>
<script>
const KEY = new URLSearchParams(location.search).get('k') || '';
let S = null, view = 'slots', toastT = 0;
const $ = id => document.getElementById(id);
function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'),1600); }
async function api(path, body){
  try{
    const r = await fetch(path, {method: body===undefined?'GET':'POST', headers:{'X-Key':KEY,'Content-Type':'application/json'},
                                 body: body===undefined?undefined:JSON.stringify(body)});
    const j = await r.json(); online(true); return j;
  }catch(e){ online(false); return null; }
}
function online(ok){ $('dot').classList.toggle('on', ok); if(!ok) $('sub').textContent='ต่อโปรแกรมไม่ได้ — เปิดโปรแกรมและอยู่ Wi-Fi เดียวกัน'; }
function esc(s){ return String(s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function render(){
  if(!S) return;
  const g = $('grid'); let html = '';
  if(view === 'slots'){
    for(const s of S.slots){
      if(s.random) html += `<button class="pad rand" data-rand="1"><span class="n">SLOT ${s.n} · 🎲</span><span class="t">สุ่มเสียง</span><span class="k">${esc(s.hotkey||'')}</span></button>`;
      else if(!s.id) html += `<button class="pad empty" disabled><span class="n">SLOT ${s.n}</span><span class="t">ยังไม่ได้ใส่เสียง</span><span class="k">${esc(s.hotkey||'')}</span></button>`;
      else html += `<button class="pad" data-id="${s.id}"><span class="n">SLOT ${s.n}</span><span class="t">${esc(s.name)}</span><span class="k">${esc(s.hotkey||'')}</span></button>`;
    }
    if(!S.slots.length) html = '<div class="empty-msg">ยังไม่มี slot — ตั้งในโปรแกรมก่อน</div>';
  } else {
    const q = $('q').value.trim().toLowerCase();
    const hits = S.sounds.filter(s => !q || s.name.toLowerCase().includes(q));
    for(const s of hits.slice(0, 200)) html += `<button class="pad" data-id="${s.id}"><span class="n">${esc(s.src)}</span><span class="t">${esc(s.name)}</span></button>`;
    if(!hits.length) html = '<div class="empty-msg">ไม่พบเสียง</div>';
  }
  g.innerHTML = html;
  $('mute').classList.toggle('on', S.muted);
  $('mute').textContent = S.muted ? '🔇 ไมค์ปิดอยู่' : '🎤 ไมค์เปิด';
  $('sub').textContent = `${S.sounds.length} เสียง · ${S.status||'พร้อมใช้งาน'}`;
}
async function refresh(){ const j = await api('/api/state'); if(j){ S = j; render(); } }
$('grid').addEventListener('click', async e => {
  const b = e.target.closest('.pad'); if(!b || b.disabled) return;
  navigator.vibrate && navigator.vibrate(18);
  b.classList.add('hit'); setTimeout(()=>b.classList.remove('hit'), 250);
  const j = b.dataset.rand ? await api('/api/random', {}) : await api('/api/play', {id: b.dataset.id});
  if(j && !j.ok && j.msg) toast(j.msg);
});
$('stop').onclick = async () => { navigator.vibrate && navigator.vibrate(30); await api('/api/stop', {}); toast('หยุดเสียงแล้ว'); };
$('mute').onclick = async () => { if(!S) return; S.muted = !S.muted; render(); await api('/api/mute', {on: S.muted}); toast(S.muted?'ปิดไมค์แล้ว':'เปิดไมค์แล้ว'); };
$('tSlots').onclick = () => { view='slots'; $('tSlots').classList.add('sel'); $('tLib').classList.remove('sel'); $('q').style.display='none'; render(); };
$('tLib').onclick = () => { view='lib'; $('tLib').classList.add('sel'); $('tSlots').classList.remove('sel'); $('q').style.display='block'; render(); };
$('q').addEventListener('input', render);
refresh(); setInterval(refresh, 3000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) refresh(); });
</script>
</body></html>
"""
