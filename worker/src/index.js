/**
 * เซิร์ฟเวอร์รับเสียงเข้าคลังกลางของ SoundSaoTer (Cloudflare Worker)
 *
 * ตัวโปรแกรมบนเครื่องผู้ใช้ไม่มี token ของ GitHub อยู่เลย — token อยู่ที่นี่ที่เดียว
 * (ตั้งเป็น secret ของ Worker) ใครกดส่งเสียงก็ยิงมาที่ /upload ไฟล์จะไปกองใน repo
 * คิวส่วนตัวก่อน เจ้าของคลังเปิดโปรแกรมแล้วกดอนุมัติ ไฟล์ถึงจะถูกย้ายขึ้น repo
 * สาธารณะและโผล่ใน index.json ให้คนอื่นเห็น
 *
 *   POST /upload    ใครก็ได้ — body = ไฟล์เสียง, หัว X-Sound-Name / X-Sound-By / X-Sound-Ext
 *   GET  /pending   เฉพาะผู้ดูแล (X-Admin-Key) — รายการที่รอตรวจ
 *   GET  /file/:id  เฉพาะผู้ดูแล (?key=) — ไฟล์ในคิว ไว้ฟังก่อนอนุมัติ
 *   POST /approve   เฉพาะผู้ดูแล — {id} ย้ายขึ้นคลังสาธารณะ
 *   POST /reject    เฉพาะผู้ดูแล — {id} ทิ้ง
 *   POST /remove    เฉพาะผู้ดูแล — {id} เอาออกจากคลังสาธารณะ
 *   GET  /          สถานะสั้น ๆ
 */

// ที่อยู่ GitHub — ตั้งทับได้ตอนทดสอบ (ชี้ไปที่ GitHub ปลอมบนเครื่อง)
const api = (env) => env.GH_API || 'https://api.github.com';
const uploads = (env) => env.GH_UPLOADS || 'https://uploads.github.com';
const INDEX_NAME = 'index.json';
const QUEUE_NAME = 'queue.json';
const EXT_OK = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aiff', '.aif'];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    try {
      if (request.method === 'OPTIONS') return reply({ ok: true });
      if (path === '/' && request.method === 'GET') return await status(env);
      if (path === '/upload' && request.method === 'POST') return await upload(request, env);
      if (path === '/pending' && request.method === 'GET') return await pending(request, env);
      if (path.startsWith('/file/') && request.method === 'GET') return await file(request, env, path.slice(6));
      if (path === '/approve' && request.method === 'POST') return await decide(request, env, true);
      if (path === '/reject' && request.method === 'POST') return await decide(request, env, false);
      if (path === '/remove' && request.method === 'POST') return await removeLive(request, env);
      return reply({ error: 'ไม่มีปลายทางนี้' }, 404);
    } catch (err) {
      return reply({ error: String(err && err.message || err).slice(0, 200) }, 500);
    }
  },
};

// ---------------------------------------------------------------- ปลายทาง
async function status(env) {
  const index = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { sounds: [] });
  return reply({ ok: true, sounds: index.sounds.length, maxMb: num(env.MAX_MB, 8) });
}

async function upload(request, env) {
  const maxBytes = num(env.MAX_MB, 8) * 1024 * 1024;
  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > maxBytes) return reply({ error: `ไฟล์ใหญ่เกิน ${num(env.MAX_MB, 8)} MB` }, 413);

  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength === 0) return reply({ error: 'ไม่มีไฟล์ส่งมา' }, 400);
  if (body.byteLength > maxBytes) return reply({ error: `ไฟล์ใหญ่เกิน ${num(env.MAX_MB, 8)} MB` }, 413);
  if (!looksLikeAudio(body)) return reply({ error: 'ไฟล์นี้ไม่ใช่ไฟล์เสียง' }, 415);

  const gate = await rateLimit(request, env, body.byteLength);
  if (gate) return gate;

  let ext = (request.headers.get('X-Sound-Ext') || '.mp3').toLowerCase();
  if (!EXT_OK.includes(ext)) ext = '.mp3';
  const name = clean(unb64(request.headers.get('X-Sound-Name')), 70) || 'เสียงไม่มีชื่อ';
  const by = clean(unb64(request.headers.get('X-Sound-By')), 24);
  const sha256 = await digest(body);
  const id = sha256.slice(0, 12);

  const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { sounds: [] });
  if (live.sounds.some((s) => s.sha256 === sha256)) return reply({ status: 'already', id });
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  if (queue.sounds.some((s) => s.sha256 === sha256)) return reply({ status: 'pending', id });
  if (queue.sounds.length >= num(env.QUEUE_CAP, 300)) {
    return reply({ error: 'คิวรอตรวจเต็ม — ลองใหม่พรุ่งนี้' }, 429);
  }

  await putAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, id + ext, body, 'application/octet-stream');
  queue.sounds.push({
    id, name, by, file: id + ext, size: body.byteLength, sha256,
    at: new Date().toISOString(), from: await ipHash(request, env),
  });
  await writeJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, queue);
  return reply({ status: 'pending', id, queue: queue.sounds.length });
}

async function pending(request, env) {
  if (!isAdmin(request, env)) return reply({ error: 'กุญแจผู้ดูแลไม่ถูกต้อง' }, 401);
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  return reply({ sounds: queue.sounds });
}

async function file(request, env, id) {
  if (!isAdmin(request, env)) return reply({ error: 'กุญแจผู้ดูแลไม่ถูกต้อง' }, 401);
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  const entry = queue.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: 'ไม่พบเสียงนั้นในคิว' }, 404);
  const bytes = await getAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
  return new Response(bytes, { headers: { 'Content-Type': 'application/octet-stream', ...cors() } });
}

async function decide(request, env, keep) {
  if (!isAdmin(request, env)) return reply({ error: 'กุญแจผู้ดูแลไม่ถูกต้อง' }, 401);
  const { id } = await request.json();
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  const entry = queue.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: 'ไม่พบเสียงนั้นในคิว' }, 404);

  if (keep) {
    const bytes = await getAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
    await putAsset(env, env.PUBLIC_REPO, env.PUBLIC_TAG, entry.file, new Uint8Array(bytes),
                   'application/octet-stream');
    const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { version: 1, sounds: [] });
    live.sounds = live.sounds.filter((s) => s.sha256 !== entry.sha256);
    live.sounds.push({
      id: entry.id, name: entry.name, by: entry.by, file: entry.file,
      size: entry.size, sha256: entry.sha256, at: new Date().toISOString(),
    });
    await writeJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, live);
  }
  await deleteAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
  queue.sounds = queue.sounds.filter((s) => s.id !== id);
  await writeJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, queue);
  return reply({ status: keep ? 'approved' : 'rejected', id, left: queue.sounds.length });
}

async function removeLive(request, env) {
  if (!isAdmin(request, env)) return reply({ error: 'กุญแจผู้ดูแลไม่ถูกต้อง' }, 401);
  const { id } = await request.json();
  const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { version: 1, sounds: [] });
  const entry = live.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: 'ไม่พบเสียงนั้นในคลัง' }, 404);
  await deleteAsset(env, env.PUBLIC_REPO, env.PUBLIC_TAG, entry.file);
  live.sounds = live.sounds.filter((s) => s.id !== id);
  await writeJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, live);
  return reply({ status: 'removed', id, sounds: live.sounds.length });
}

// ---------------------------------------------------------------- กันสแปม
async function rateLimit(request, env, bytes) {
  if (!env.RATE) return null;                       // ยังไม่ได้ผูก KV ก็ปล่อยผ่าน
  const who = await ipHash(request, env);
  const hour = Math.floor(Date.now() / 3600000);
  const day = Math.floor(Date.now() / 86400000);
  const perHour = num(env.PER_HOUR, 5);
  const perDay = num(env.PER_DAY, 15);
  const hKey = `h:${who}:${hour}`, dKey = `d:${who}:${day}`;
  const [h, d] = await Promise.all([env.RATE.get(hKey), env.RATE.get(dKey)]);
  if (Number(h || 0) >= perHour) return reply({ error: `ส่งได้ชั่วโมงละ ${perHour} เสียง` }, 429);
  if (Number(d || 0) >= perDay) return reply({ error: `ส่งได้วันละ ${perDay} เสียง` }, 429);
  await Promise.all([
    env.RATE.put(hKey, String(Number(h || 0) + 1), { expirationTtl: 3700 }),
    env.RATE.put(dKey, String(Number(d || 0) + 1), { expirationTtl: 90000 }),
  ]);
  return null;
}

async function ipHash(request, env) {
  // เก็บเป็นแฮชสั้น ๆ พอให้นับโควตาได้ ไม่เก็บ IP จริง
  const ip = request.headers.get('CF-Connecting-IP') || '0.0.0.0';
  const salt = env.ADMIN_KEY || 'soundsaoter';
  const raw = new TextEncoder().encode(ip + '|' + salt);
  return (await digest(new Uint8Array(raw))).slice(0, 16);
}

// ---------------------------------------------------------------- GitHub
async function gh(env, method, url, body, headers) {
  const resp = await fetch(url, {
    method,
    headers: {
      'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'SoundSaoTer-Hub',
      ...(headers || {}),
    },
    body,
  });
  if (!resp.ok) {
    const text = (await resp.text()).slice(0, 200);
    throw new Error(`GitHub ${resp.status}: ${text}`);
  }
  return resp;
}

async function release(env, repo, tag) {
  const resp = await gh(env, 'GET', `${api(env)}/repos/${repo}/releases/tags/${tag}`);
  return resp.json();
}

async function assetOf(env, repo, tag, name) {
  const rel = await release(env, repo, tag);
  return (rel.assets || []).find((a) => a.name === name) || null;
}

async function getAsset(env, repo, tag, name) {
  const asset = await assetOf(env, repo, tag, name);
  if (!asset) {
    const err = new Error(`ไม่พบไฟล์ ${name}`);
    err.missing = true;
    throw err;
  }
  const resp = await gh(env, 'GET', `${api(env)}/repos/${repo}/releases/assets/${asset.id}`, undefined,
                        { 'Accept': 'application/octet-stream' });
  return resp.arrayBuffer();
}

async function putAsset(env, repo, tag, name, bytes, type) {
  const rel = await release(env, repo, tag);
  const old = (rel.assets || []).find((a) => a.name === name);
  if (old) await gh(env, 'DELETE', `${api(env)}/repos/${repo}/releases/assets/${old.id}`);
  await gh(env, 'POST', `${uploads(env)}/repos/${repo}/releases/${rel.id}/assets?name=${encodeURIComponent(name)}`,
           bytes, { 'Content-Type': type, 'Content-Length': String(bytes.byteLength) });
}

async function deleteAsset(env, repo, tag, name) {
  const asset = await assetOf(env, repo, tag, name);
  if (asset) await gh(env, 'DELETE', `${api(env)}/repos/${repo}/releases/assets/${asset.id}`);
}

async function readJson(env, repo, tag, name, fallback) {
  try {
    const bytes = await getAsset(env, repo, tag, name);
    const data = JSON.parse(new TextDecoder().decode(bytes));
    data.sounds = data.sounds || [];
    return data;
  } catch (err) {
    // ยังไม่มีไฟล์ หรือไฟล์เสีย -> เริ่มใหม่ได้ · อ่านไม่ได้เพราะเหตุอื่น (เน็ต/สิทธิ์) ต้องโยนต่อ
    if (err && (err.missing || err instanceof SyntaxError)) return fallback;
    throw err;
  }
}

async function writeJson(env, repo, tag, name, obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  await putAsset(env, repo, tag, name, bytes, 'application/json');
}

// ---------------------------------------------------------------- จิปาถะ
function isAdmin(request, env) {
  const url = new URL(request.url);
  const given = request.headers.get('X-Admin-Key') || url.searchParams.get('key') || '';
  const want = env.ADMIN_KEY || '';
  if (!want || given.length !== want.length) return false;
  let same = 0;                                   // เทียบให้ใช้เวลาเท่ากันทุกกรณี
  for (let i = 0; i < want.length; i++) same |= given.charCodeAt(i) ^ want.charCodeAt(i);
  return same === 0;
}

function looksLikeAudio(b) {
  const tag = (i, s) => s.split('').every((c, k) => b[i + k] === c.charCodeAt(0));
  if (b.length < 12) return false;
  if (tag(0, 'ID3')) return true;                               // mp3 ที่มีแท็ก
  if (b[0] === 0xff && (b[1] & 0xe0) === 0xe0) return true;     // mp3 frame
  if (tag(0, 'RIFF') && tag(8, 'WAVE')) return true;
  if (tag(0, 'OggS')) return true;
  if (tag(0, 'fLaC')) return true;
  if (tag(0, 'FORM') && (tag(8, 'AIFF') || tag(8, 'AIFC'))) return true;
  if (tag(4, 'ftyp')) return true;                              // m4a/mp4
  return false;
}

async function digest(bytes) {
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map((x) => x.toString(16).padStart(2, '0')).join('');
}

function unb64(value) {
  if (!value) return '';
  try {
    const raw = Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
    return new TextDecoder().decode(raw);
  } catch (err) {
    return '';
  }
}

function clean(text, limit) {
  return (text || '').replace(/[\u0000-\u001f<>]/g, '').trim().slice(0, limit);
}

function num(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function cors() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'X-Admin-Key, X-Sound-Name, X-Sound-By, X-Sound-Ext, Content-Type',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  };
}

function reply(obj, code = 200) {
  return new Response(JSON.stringify(obj), {
    status: code,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...cors() },
  });
}
