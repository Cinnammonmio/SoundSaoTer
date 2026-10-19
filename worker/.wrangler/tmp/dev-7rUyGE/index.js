var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/index.js
var api = /* @__PURE__ */ __name((env) => env.GH_API || "https://api.github.com", "api");
var uploads = /* @__PURE__ */ __name((env) => env.GH_UPLOADS || "https://uploads.github.com", "uploads");
var INDEX_NAME = "index.json";
var QUEUE_NAME = "queue.json";
var EXT_OK = [".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aiff", ".aif"];
var src_default = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    try {
      if (request.method === "OPTIONS") return reply({ ok: true });
      if (path === "/" && request.method === "GET") return await status(env);
      if (path === "/upload" && request.method === "POST") return await upload(request, env);
      if (path === "/pending" && request.method === "GET") return await pending(request, env);
      if (path.startsWith("/file/") && request.method === "GET") return await file(request, env, path.slice(6));
      if (path === "/approve" && request.method === "POST") return await decide(request, env, true);
      if (path === "/reject" && request.method === "POST") return await decide(request, env, false);
      if (path === "/remove" && request.method === "POST") return await removeLive(request, env);
      return reply({ error: "\u0E44\u0E21\u0E48\u0E21\u0E35\u0E1B\u0E25\u0E32\u0E22\u0E17\u0E32\u0E07\u0E19\u0E35\u0E49" }, 404);
    } catch (err) {
      return reply({ error: String(err && err.message || err).slice(0, 200) }, 500);
    }
  }
};
async function status(env) {
  const index = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { sounds: [] });
  return reply({ ok: true, sounds: index.sounds.length, maxMb: num(env.MAX_MB, 8) });
}
__name(status, "status");
async function upload(request, env) {
  const maxBytes = num(env.MAX_MB, 8) * 1024 * 1024;
  const declared = Number(request.headers.get("Content-Length") || 0);
  if (declared > maxBytes) return reply({ error: `\u0E44\u0E1F\u0E25\u0E4C\u0E43\u0E2B\u0E0D\u0E48\u0E40\u0E01\u0E34\u0E19 ${num(env.MAX_MB, 8)} MB` }, 413);
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength === 0) return reply({ error: "\u0E44\u0E21\u0E48\u0E21\u0E35\u0E44\u0E1F\u0E25\u0E4C\u0E2A\u0E48\u0E07\u0E21\u0E32" }, 400);
  if (body.byteLength > maxBytes) return reply({ error: `\u0E44\u0E1F\u0E25\u0E4C\u0E43\u0E2B\u0E0D\u0E48\u0E40\u0E01\u0E34\u0E19 ${num(env.MAX_MB, 8)} MB` }, 413);
  if (!looksLikeAudio(body)) return reply({ error: "\u0E44\u0E1F\u0E25\u0E4C\u0E19\u0E35\u0E49\u0E44\u0E21\u0E48\u0E43\u0E0A\u0E48\u0E44\u0E1F\u0E25\u0E4C\u0E40\u0E2A\u0E35\u0E22\u0E07" }, 415);
  const gate = await rateLimit(request, env, body.byteLength);
  if (gate) return gate;
  let ext = (request.headers.get("X-Sound-Ext") || ".mp3").toLowerCase();
  if (!EXT_OK.includes(ext)) ext = ".mp3";
  const name = clean(unb64(request.headers.get("X-Sound-Name")), 70) || "\u0E40\u0E2A\u0E35\u0E22\u0E07\u0E44\u0E21\u0E48\u0E21\u0E35\u0E0A\u0E37\u0E48\u0E2D";
  const by = clean(unb64(request.headers.get("X-Sound-By")), 24);
  const sha256 = await digest(body);
  const id = sha256.slice(0, 12);
  const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { sounds: [] });
  if (live.sounds.some((s) => s.sha256 === sha256)) return reply({ status: "already", id });
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  if (queue.sounds.some((s) => s.sha256 === sha256)) return reply({ status: "pending", id });
  if (queue.sounds.length >= num(env.QUEUE_CAP, 300)) {
    return reply({ error: "\u0E04\u0E34\u0E27\u0E23\u0E2D\u0E15\u0E23\u0E27\u0E08\u0E40\u0E15\u0E47\u0E21 \u2014 \u0E25\u0E2D\u0E07\u0E43\u0E2B\u0E21\u0E48\u0E1E\u0E23\u0E38\u0E48\u0E07\u0E19\u0E35\u0E49" }, 429);
  }
  await putAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, id + ext, body, "application/octet-stream");
  queue.sounds.push({
    id,
    name,
    by,
    file: id + ext,
    size: body.byteLength,
    sha256,
    at: (/* @__PURE__ */ new Date()).toISOString(),
    from: await ipHash(request, env)
  });
  await writeJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, queue);
  return reply({ status: "pending", id, queue: queue.sounds.length });
}
__name(upload, "upload");
async function pending(request, env) {
  if (!isAdmin(request, env)) return reply({ error: "\u0E01\u0E38\u0E0D\u0E41\u0E08\u0E1C\u0E39\u0E49\u0E14\u0E39\u0E41\u0E25\u0E44\u0E21\u0E48\u0E16\u0E39\u0E01\u0E15\u0E49\u0E2D\u0E07" }, 401);
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  return reply({ sounds: queue.sounds });
}
__name(pending, "pending");
async function file(request, env, id) {
  if (!isAdmin(request, env)) return reply({ error: "\u0E01\u0E38\u0E0D\u0E41\u0E08\u0E1C\u0E39\u0E49\u0E14\u0E39\u0E41\u0E25\u0E44\u0E21\u0E48\u0E16\u0E39\u0E01\u0E15\u0E49\u0E2D\u0E07" }, 401);
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  const entry = queue.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: "\u0E44\u0E21\u0E48\u0E1E\u0E1A\u0E40\u0E2A\u0E35\u0E22\u0E07\u0E19\u0E31\u0E49\u0E19\u0E43\u0E19\u0E04\u0E34\u0E27" }, 404);
  const bytes = await getAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
  return new Response(bytes, { headers: { "Content-Type": "application/octet-stream", ...cors() } });
}
__name(file, "file");
async function decide(request, env, keep) {
  if (!isAdmin(request, env)) return reply({ error: "\u0E01\u0E38\u0E0D\u0E41\u0E08\u0E1C\u0E39\u0E49\u0E14\u0E39\u0E41\u0E25\u0E44\u0E21\u0E48\u0E16\u0E39\u0E01\u0E15\u0E49\u0E2D\u0E07" }, 401);
  const { id } = await request.json();
  const queue = await readJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, { sounds: [] });
  const entry = queue.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: "\u0E44\u0E21\u0E48\u0E1E\u0E1A\u0E40\u0E2A\u0E35\u0E22\u0E07\u0E19\u0E31\u0E49\u0E19\u0E43\u0E19\u0E04\u0E34\u0E27" }, 404);
  if (keep) {
    const bytes = await getAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
    await putAsset(
      env,
      env.PUBLIC_REPO,
      env.PUBLIC_TAG,
      entry.file,
      new Uint8Array(bytes),
      "application/octet-stream"
    );
    const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { version: 1, sounds: [] });
    live.sounds = live.sounds.filter((s) => s.sha256 !== entry.sha256);
    live.sounds.push({
      id: entry.id,
      name: entry.name,
      by: entry.by,
      file: entry.file,
      size: entry.size,
      sha256: entry.sha256,
      at: (/* @__PURE__ */ new Date()).toISOString()
    });
    await writeJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, live);
  }
  await deleteAsset(env, env.QUEUE_REPO, env.QUEUE_TAG, entry.file);
  queue.sounds = queue.sounds.filter((s) => s.id !== id);
  await writeJson(env, env.QUEUE_REPO, env.QUEUE_TAG, QUEUE_NAME, queue);
  return reply({ status: keep ? "approved" : "rejected", id, left: queue.sounds.length });
}
__name(decide, "decide");
async function removeLive(request, env) {
  if (!isAdmin(request, env)) return reply({ error: "\u0E01\u0E38\u0E0D\u0E41\u0E08\u0E1C\u0E39\u0E49\u0E14\u0E39\u0E41\u0E25\u0E44\u0E21\u0E48\u0E16\u0E39\u0E01\u0E15\u0E49\u0E2D\u0E07" }, 401);
  const { id } = await request.json();
  const live = await readJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, { version: 1, sounds: [] });
  const entry = live.sounds.find((s) => s.id === id);
  if (!entry) return reply({ error: "\u0E44\u0E21\u0E48\u0E1E\u0E1A\u0E40\u0E2A\u0E35\u0E22\u0E07\u0E19\u0E31\u0E49\u0E19\u0E43\u0E19\u0E04\u0E25\u0E31\u0E07" }, 404);
  await deleteAsset(env, env.PUBLIC_REPO, env.PUBLIC_TAG, entry.file);
  live.sounds = live.sounds.filter((s) => s.id !== id);
  await writeJson(env, env.PUBLIC_REPO, env.PUBLIC_TAG, INDEX_NAME, live);
  return reply({ status: "removed", id, sounds: live.sounds.length });
}
__name(removeLive, "removeLive");
async function rateLimit(request, env, bytes) {
  if (!env.RATE) return null;
  const who = await ipHash(request, env);
  const hour = Math.floor(Date.now() / 36e5);
  const day = Math.floor(Date.now() / 864e5);
  const perHour = num(env.PER_HOUR, 5);
  const perDay = num(env.PER_DAY, 15);
  const hKey = `h:${who}:${hour}`, dKey = `d:${who}:${day}`;
  const [h, d] = await Promise.all([env.RATE.get(hKey), env.RATE.get(dKey)]);
  if (Number(h || 0) >= perHour) return reply({ error: `\u0E2A\u0E48\u0E07\u0E44\u0E14\u0E49\u0E0A\u0E31\u0E48\u0E27\u0E42\u0E21\u0E07\u0E25\u0E30 ${perHour} \u0E40\u0E2A\u0E35\u0E22\u0E07` }, 429);
  if (Number(d || 0) >= perDay) return reply({ error: `\u0E2A\u0E48\u0E07\u0E44\u0E14\u0E49\u0E27\u0E31\u0E19\u0E25\u0E30 ${perDay} \u0E40\u0E2A\u0E35\u0E22\u0E07` }, 429);
  await Promise.all([
    env.RATE.put(hKey, String(Number(h || 0) + 1), { expirationTtl: 3700 }),
    env.RATE.put(dKey, String(Number(d || 0) + 1), { expirationTtl: 9e4 })
  ]);
  return null;
}
__name(rateLimit, "rateLimit");
async function ipHash(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const salt = env.ADMIN_KEY || "soundsaoter";
  const raw = new TextEncoder().encode(ip + "|" + salt);
  return (await digest(new Uint8Array(raw))).slice(0, 16);
}
__name(ipHash, "ipHash");
async function gh(env, method, url, body, headers) {
  const resp = await fetch(url, {
    method,
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "SoundSaoTer-Hub",
      ...headers || {}
    },
    body
  });
  if (!resp.ok) {
    const text = (await resp.text()).slice(0, 200);
    throw new Error(`GitHub ${resp.status}: ${text}`);
  }
  return resp;
}
__name(gh, "gh");
async function release(env, repo, tag) {
  const resp = await gh(env, "GET", `${api(env)}/repos/${repo}/releases/tags/${tag}`);
  return resp.json();
}
__name(release, "release");
async function assetOf(env, repo, tag, name) {
  const rel = await release(env, repo, tag);
  return (rel.assets || []).find((a) => a.name === name) || null;
}
__name(assetOf, "assetOf");
async function getAsset(env, repo, tag, name) {
  const asset = await assetOf(env, repo, tag, name);
  if (!asset) {
    const err = new Error(`\u0E44\u0E21\u0E48\u0E1E\u0E1A\u0E44\u0E1F\u0E25\u0E4C ${name}`);
    err.missing = true;
    throw err;
  }
  const resp = await gh(
    env,
    "GET",
    `${api(env)}/repos/${repo}/releases/assets/${asset.id}`,
    void 0,
    { "Accept": "application/octet-stream" }
  );
  return resp.arrayBuffer();
}
__name(getAsset, "getAsset");
async function putAsset(env, repo, tag, name, bytes, type) {
  const rel = await release(env, repo, tag);
  const old = (rel.assets || []).find((a) => a.name === name);
  if (old) await gh(env, "DELETE", `${api(env)}/repos/${repo}/releases/assets/${old.id}`);
  await gh(
    env,
    "POST",
    `${uploads(env)}/repos/${repo}/releases/${rel.id}/assets?name=${encodeURIComponent(name)}`,
    bytes,
    { "Content-Type": type, "Content-Length": String(bytes.byteLength) }
  );
}
__name(putAsset, "putAsset");
async function deleteAsset(env, repo, tag, name) {
  const asset = await assetOf(env, repo, tag, name);
  if (asset) await gh(env, "DELETE", `${api(env)}/repos/${repo}/releases/assets/${asset.id}`);
}
__name(deleteAsset, "deleteAsset");
async function readJson(env, repo, tag, name, fallback) {
  try {
    const bytes = await getAsset(env, repo, tag, name);
    const data = JSON.parse(new TextDecoder().decode(bytes));
    data.sounds = data.sounds || [];
    return data;
  } catch (err) {
    if (err && (err.missing || err instanceof SyntaxError)) return fallback;
    throw err;
  }
}
__name(readJson, "readJson");
async function writeJson(env, repo, tag, name, obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  await putAsset(env, repo, tag, name, bytes, "application/json");
}
__name(writeJson, "writeJson");
function isAdmin(request, env) {
  const url = new URL(request.url);
  const given = request.headers.get("X-Admin-Key") || url.searchParams.get("key") || "";
  const want = env.ADMIN_KEY || "";
  if (!want || given.length !== want.length) return false;
  let same = 0;
  for (let i = 0; i < want.length; i++) same |= given.charCodeAt(i) ^ want.charCodeAt(i);
  return same === 0;
}
__name(isAdmin, "isAdmin");
function looksLikeAudio(b) {
  const tag = /* @__PURE__ */ __name((i, s) => s.split("").every((c, k) => b[i + k] === c.charCodeAt(0)), "tag");
  if (b.length < 12) return false;
  if (tag(0, "ID3")) return true;
  if (b[0] === 255 && (b[1] & 224) === 224) return true;
  if (tag(0, "RIFF") && tag(8, "WAVE")) return true;
  if (tag(0, "OggS")) return true;
  if (tag(0, "fLaC")) return true;
  if (tag(0, "FORM") && (tag(8, "AIFF") || tag(8, "AIFC"))) return true;
  if (tag(4, "ftyp")) return true;
  return false;
}
__name(looksLikeAudio, "looksLikeAudio");
async function digest(bytes) {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
__name(digest, "digest");
function unb64(value) {
  if (!value) return "";
  try {
    const raw = Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
    return new TextDecoder().decode(raw);
  } catch (err) {
    return "";
  }
}
__name(unb64, "unb64");
function clean(text, limit) {
  return (text || "").replace(/[\u0000-\u001f<>]/g, "").trim().slice(0, limit);
}
__name(clean, "clean");
function num(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}
__name(num, "num");
function cors() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "X-Admin-Key, X-Sound-Name, X-Sound-By, X-Sound-Ext, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS"
  };
}
__name(cors, "cors");
function reply(obj, code = 200) {
  return new Response(JSON.stringify(obj), {
    status: code,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors() }
  });
}
__name(reply, "reply");

// ../../../Users/mioci/AppData/Local/npm-cache/_npx/c943b712072b77c4/node_modules/wrangler/templates/middleware/middleware-ensure-req-body-drained.ts
var drainBody = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } finally {
    try {
      if (request.body !== null && !request.bodyUsed) {
        const reader = request.body.getReader();
        while (!(await reader.read()).done) {
        }
      }
    } catch (e) {
      console.error("Failed to drain the unused request body.", e);
    }
  }
}, "drainBody");
var middleware_ensure_req_body_drained_default = drainBody;

// ../../../Users/mioci/AppData/Local/npm-cache/_npx/c943b712072b77c4/node_modules/wrangler/templates/middleware/middleware-miniflare3-json-error.ts
function reduceError(e) {
  return {
    name: e?.name,
    message: e?.message ?? String(e),
    stack: e?.stack,
    cause: e?.cause === void 0 ? void 0 : reduceError(e.cause)
  };
}
__name(reduceError, "reduceError");
var jsonError = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } catch (e) {
    const error = reduceError(e);
    const body = JSON.stringify(error);
    const headers = {
      "Content-Type": "application/json",
      "MF-Experimental-Error-Stack": "true"
    };
    const encoded = encodeURIComponent(body);
    if (encoded.length <= 8192) {
      headers["MF-Experimental-Error-Stack-Payload"] = encoded;
    }
    return new Response(body, { status: 500, headers });
  }
}, "jsonError");
var middleware_miniflare3_json_error_default = jsonError;

// .wrangler/tmp/bundle-3UdZzR/middleware-insertion-facade.js
var __INTERNAL_WRANGLER_MIDDLEWARE__ = [
  middleware_ensure_req_body_drained_default,
  middleware_miniflare3_json_error_default
];
var middleware_insertion_facade_default = src_default;

// ../../../Users/mioci/AppData/Local/npm-cache/_npx/c943b712072b77c4/node_modules/wrangler/templates/middleware/common.ts
var __facade_middleware__ = [];
function __facade_register__(...args) {
  __facade_middleware__.push(...args.flat());
}
__name(__facade_register__, "__facade_register__");
function __facade_invokeChain__(request, env, ctx, dispatch, middlewareChain) {
  const [head, ...tail] = middlewareChain;
  const middlewareCtx = {
    dispatch,
    next(newRequest, newEnv) {
      return __facade_invokeChain__(newRequest, newEnv, ctx, dispatch, tail);
    }
  };
  return head(request, env, ctx, middlewareCtx);
}
__name(__facade_invokeChain__, "__facade_invokeChain__");
function __facade_invoke__(request, env, ctx, dispatch, finalMiddleware) {
  return __facade_invokeChain__(request, env, ctx, dispatch, [
    ...__facade_middleware__,
    finalMiddleware
  ]);
}
__name(__facade_invoke__, "__facade_invoke__");

// .wrangler/tmp/bundle-3UdZzR/middleware-loader.entry.ts
var __Facade_ScheduledController__ = class ___Facade_ScheduledController__ {
  constructor(scheduledTime, cron, noRetry) {
    this.scheduledTime = scheduledTime;
    this.cron = cron;
    this.#noRetry = noRetry;
  }
  scheduledTime;
  cron;
  static {
    __name(this, "__Facade_ScheduledController__");
  }
  #noRetry;
  noRetry() {
    if (!(this instanceof ___Facade_ScheduledController__)) {
      throw new TypeError("Illegal invocation");
    }
    this.#noRetry();
  }
};
function wrapExportedHandler(worker) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return worker;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  const fetchDispatcher = /* @__PURE__ */ __name(function(request, env, ctx) {
    if (worker.fetch === void 0) {
      throw new Error("Handler does not export a fetch() function.");
    }
    return worker.fetch(request, env, ctx);
  }, "fetchDispatcher");
  return {
    ...worker,
    fetch(request, env, ctx) {
      const dispatcher = /* @__PURE__ */ __name(function(type, init) {
        if (type === "scheduled" && worker.scheduled !== void 0) {
          const controller = new __Facade_ScheduledController__(
            Date.now(),
            init.cron ?? "",
            () => {
            }
          );
          return worker.scheduled(controller, env, ctx);
        }
      }, "dispatcher");
      return __facade_invoke__(request, env, ctx, dispatcher, fetchDispatcher);
    }
  };
}
__name(wrapExportedHandler, "wrapExportedHandler");
function wrapWorkerEntrypoint(klass) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return klass;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  return class extends klass {
    #fetchDispatcher = /* @__PURE__ */ __name((request, env, ctx) => {
      this.env = env;
      this.ctx = ctx;
      if (super.fetch === void 0) {
        throw new Error("Entrypoint class does not define a fetch() function.");
      }
      return super.fetch(request);
    }, "#fetchDispatcher");
    #dispatcher = /* @__PURE__ */ __name((type, init) => {
      if (type === "scheduled" && super.scheduled !== void 0) {
        const controller = new __Facade_ScheduledController__(
          Date.now(),
          init.cron ?? "",
          () => {
          }
        );
        return super.scheduled(controller);
      }
    }, "#dispatcher");
    fetch(request) {
      return __facade_invoke__(
        request,
        this.env,
        this.ctx,
        this.#dispatcher,
        this.#fetchDispatcher
      );
    }
  };
}
__name(wrapWorkerEntrypoint, "wrapWorkerEntrypoint");
var WRAPPED_ENTRY;
if (typeof middleware_insertion_facade_default === "object") {
  WRAPPED_ENTRY = wrapExportedHandler(middleware_insertion_facade_default);
} else if (typeof middleware_insertion_facade_default === "function") {
  WRAPPED_ENTRY = wrapWorkerEntrypoint(middleware_insertion_facade_default);
}
var middleware_loader_entry_default = WRAPPED_ENTRY;
export {
  __INTERNAL_WRANGLER_MIDDLEWARE__,
  middleware_loader_entry_default as default
};
//# sourceMappingURL=index.js.map
