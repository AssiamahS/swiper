// swiper-judge: the swiper vision brain, so the Tinder lane works from any device with no Mac involved.
// POST /judge { text, urls: [photo urls] }  header X-Key  ->  verdict JSON ({ error, kind } when it cannot judge)
// Photos are fetched here (the CDN has no CORS for the page), Gemini judges (own key), Workers AI llama-vision is the fallback.
// every flash model has its own free quota and its own 503 spikes; fastest first (probed 2026-09-28: 3.6 1.2s, 3-preview 1.8s, lite-preview 4.6s, 3.8 4.9s, lite-latest 6.6s, 3.5/3.1-lite 12-20s under load)
const GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.8-flash", "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"];
const CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct";
const TINY = 12 * 1024;          // below this a "photo" is a preview thumbnail, never judge it
const MODEL_TIMEOUT = 12000;     // ms per model call (several models must fit in the budget)
const BUDGET = 40000;            // ms total, the page waits 45s
const UID_RE = /\/u\/([^/]+)\//;
const pick = (out) => { if (typeof out === "string") return out; const r = out?.response ?? out?.choices?.[0]?.message?.content ?? out; return typeof r === "string" ? r : JSON.stringify(r); };

function cors(request) {
  const o = request.headers.get("Origin") || "";
  const ok = /^https:\/\/([a-z0-9-]+\.)?tinder\.com$/.test(o) || /^https:\/\/([a-z0-9-]+\.)?bumble\.com$/.test(o);
  return { "Access-Control-Allow-Origin": ok ? o : "https://tinder.com", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-Key", "Access-Control-Max-Age": "86400", "Vary": "Origin" };
}
const json = (obj, status, request) => Response.json(obj, { status: status || 200, headers: cors(request) });

function firstJson(text) {
  try { const w = JSON.parse(text); return Array.isArray(w) ? w[0] : w; } catch {}
  const i = text.indexOf("{");
  if (i < 0) throw new Error("no json");
  let depth = 0;
  for (let j = i; j < text.length; j++) {
    if (text[j] === "{") depth++;
    else if (text[j] === "}" && --depth === 0) return JSON.parse(text.slice(i, j + 1));
  }
  throw new Error("no json");
}
const num = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
const emptyVerdict = (v) => !num(v.face) && !num(v.feminine) && !num(v.photo_quality) && !num(v.curves);

function ownPhotos(urls) {
  const groups = {};
  for (const u of urls) { const k = (UID_RE.exec(u) || [])[1] || u; (groups[k] = groups[k] || []).push(u); }
  const best = Object.values(groups).sort((a, b) => b.length - a.length)[0] || [];
  return best.length > 1 && best.length < urls.length ? best : urls;
}

async function fetchPhoto(u) {
  const m = /\/(\d+)x(\d+)_/.exec(u);
  const candidates = m && +m[1] < 400 ? [u.replace(/\/(\d+)x(\d+)_/, "/640x800_"), u.replace(/\/(\d+)x(\d+)_/, "/"), u] : [u];
  let best = null;
  for (const c of candidates) {
    try {
      const r = await fetch(c, { headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://tinder.com/" }, cf: { cacheTtl: 0 } });
      if (!r.ok) continue;
      const bytes = new Uint8Array(await r.arrayBuffer());
      const ct = (r.headers.get("Content-Type") || "image/jpeg").split(";")[0];
      if (!best || bytes.length > best.bytes.length) best = { bytes, ct };
      if (bytes.length >= TINY) break;
    } catch {}
  }
  return best;
}

function b64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const modelDownUntil = {};   // isolate-local, PER MODEL: a 429 benches a model 60s, a 503 15s; the others keep serving
const RACE = 2;              // models asked at the same time; the first clean verdict wins (503 spikes and slow models cost nothing)
function geminiKeys(env) { return String(env.GEMINI_KEYS || env.GEMINI_KEY || "").split(",").map((k) => k.trim()).filter(Boolean); }
let keyTurn = 0;
async function geminiOne(env, model, text, imgs, deadline) {
  const left = deadline - Date.now();
  if (left < 3000) throw new Error(`${model}: no time`);
  const keys = geminiKeys(env);
  const key = keys[(keyTurn++) % keys.length];   // several AI Studio keys = several daily quotas; a 429 benches only this key+model pair
  const bench = model + "|" + key.slice(-6);
  const parts = [{ text }, ...imgs.map((im) => ({ inline_data: { mime_type: im.ct, data: im.b64 } }))];
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), Math.min(MODEL_TIMEOUT, left));
  try {
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${key}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
      body: JSON.stringify({ contents: [{ parts }], generationConfig: { temperature: 0, maxOutputTokens: 800, responseMimeType: "application/json" } }),
    });
    const d = await r.json();
    if (!r.ok) {
      if (r.status === 429) modelDownUntil[bench] = Date.now() + (/daily|per day|PerDay/i.test(d.error && d.error.message || "") ? 3600000 : 60000);
      else if (r.status === 503) modelDownUntil[bench] = Date.now() + 15000;
      throw new Error(`${model}: HTTP ${r.status} ${(d.error && d.error.message || "").slice(0, 60)}`);
    }
    const out = (d.candidates[0].content.parts || []).map((p) => p.text || "").join("");
    const v = firstJson(out);
    if (emptyVerdict(v)) throw new Error(`${model}: empty verdict`);
    v._model = model; return v;
  } catch (e) {
    if (/abort|timeout/i.test(String(e))) modelDownUntil[bench] = Date.now() + 15000;
    throw e instanceof Error ? e : new Error(`${model}: ${String(e).slice(0, 60)}`);
  } finally { clearTimeout(t); }
}
async function gemini(env, text, imgs, deadline, reverse) {
  const keys = geminiKeys(env);
  if (!keys.length) throw new Error("gemini: no key");
  const live = (m) => keys.some((k) => Date.now() >= (modelDownUntil[m + "|" + k.slice(-6)] || 0));
  const order = (reverse ? [...GEMINI_MODELS].reverse() : GEMINI_MODELS).filter(live);
  if (!order.length) throw new Error("gemini: every model+key benched (503/429/timeouts), retry in " + Math.round((Math.min(...Object.values(modelDownUntil)) - Date.now()) / 1000) + "s");
  const errs = [];
  for (let i = 0; i < order.length; i += RACE) {
    if (deadline - Date.now() < 3000) break;
    const batch = order.slice(i, i + RACE);
    try {
      return await Promise.any(batch.map((m) => geminiOne(env, m, text, imgs, deadline)));
    } catch (e) { for (const x of (e.errors || [e])) errs.push(String(x.message || x).slice(0, 70)); }
  }
  throw new Error(errs[errs.length - 1] || "gemini failed");
}

const LLAMA_PROMPT = 'You rate dating profile photos. Look at the photo and reply with ONLY a JSON object, no other words. Keys and allowed values: ' +
  'is_woman (true/false), feminine (integer 0-10, 10 = unmistakably a woman), body (one of "slim","athletic","average","curvy","plus"; plus = visibly heavy), body_confidence (0-1), ' +
  'in_shape (true/false), full_body_visible (true if head to at least mid-thigh is visible), swimwear (true for bikini, swimsuit or lingerie), curves (0-10), glutes (0-10), bust (0-10), ' +
  'face (0-10, how attractive the face is), sexy_vibe (0-10), photo_quality (0-10), grainy (true/false), group_photo (true/false), dyed_hair (true only for unnatural hair colors), ' +
  'facial_piercings (true for septum, lip, eyebrow, bridge, cheek, or two or more face piercings; one nose stud = false), alt_style (true for emo, goth or punk styling), gym_selfie (true/false). ' +
  'Rate what you actually see; do not guess middle values for everything.';
const MISTRAL = "@cf/mistralai/mistral-small-3.1-24b-instruct";
async function mistralVision(env, text, imgs, deadline) {
  // second brain: Mistral Small 3.1 on Workers AI, takes several images in one call, same full prompt as Gemini
  const left = deadline - Date.now();
  if (left < 6000) throw new Error("no time");
  const content = [{ type: "text", text }, ...imgs.slice(0, 6).map((im) => ({ type: "image_url", image_url: { url: `data:${im.ct};base64,${im.b64}` } }))];
  const out = await env.AI.run(MISTRAL, { messages: [{ role: "user", content }], max_tokens: 500, temperature: 0 });
  const v = firstJson(String(pick(out))); v._model = "cf/mistral-small-3.1"; return v;
}

async function llamaVision(env, text, imgs, deadline) {
  // single image per call on this model; judge up to 3 photos and merge (max scores, any-true flags, body from the fullest photo)
  const outs = [], errs = [];
  for (const im of imgs.slice(0, 4)) {           // this model refuses or zeroes some photos: skip those, keep going, merge what it rated
    if (deadline - Date.now() < 5000 || outs.length >= 3) break;
    try {
      const out = await env.AI.run(CF_MODEL, { messages: [{ role: "user", content: [{ type: "text", text: LLAMA_PROMPT }, { type: "image_url", image_url: { url: `data:${im.ct};base64,${im.b64}` } }] }], max_tokens: 400, temperature: 0 });
      const raw = String(pick(out));
      let v;
      try { v = firstJson(raw); } catch { errs.push("no json: " + raw.slice(0, 60)); continue; }
      if (emptyVerdict(v)) { errs.push("zeros"); continue; }
      outs.push(v);
    } catch (e) { errs.push(String(e).slice(0, 60)); }
  }
  if (!outs.length) throw new Error("llama: " + (errs[0] || "no verdicts"));
  const v = { ...outs[0] };
  for (const o of outs.slice(1)) {
    for (const k of Object.keys(o)) {
      if (k === "feminine") v[k] = Math.min(num(v[k]), o[k]);                       // gender is a veto across photos, never a max
      else if (k === "is_woman") v[k] = v[k] === true && o[k] === true;
      else if (typeof o[k] === "number") v[k] = Math.max(num(v[k]), o[k]);
      else if (typeof o[k] === "boolean") v[k] = k === "grainy" || k === "group_photo" ? (v[k] && o[k]) : (v[k] || o[k]);
    }
    if (o.full_body_visible && !outs[0].full_body_visible) v.body = o.body;
  }
  v._model = "cf/llama-3.2-11b-vision"; v._v = 4; return v;
}

async function githubModels(env, text, imgs, deadline) {
  // third brain: GitHub Models (free tier, own daily quota). GITHUB_TOKEN secret = a github token; openai/gpt-4o-mini takes several images
  if (!env.GITHUB_TOKEN) throw new Error("no github token");
  const left = deadline - Date.now();
  if (left < 5000) throw new Error("no time");
  const content = [{ type: "text", text }, ...imgs.slice(0, 6).map((im) => ({ type: "image_url", image_url: { url: `data:${im.ct};base64,${im.b64}`, detail: "low" } }))];
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), Math.min(MODEL_TIMEOUT, left));
  try {
    const r = await fetch("https://models.github.ai/inference/chat/completions", {
      method: "POST", signal: ctl.signal,
      headers: { "Authorization": "Bearer " + env.GITHUB_TOKEN, "Content-Type": "application/json", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "swiper-judge" },
      body: JSON.stringify({ model: "openai/gpt-4o-mini", temperature: 0, max_tokens: 500, response_format: { type: "json_object" }, messages: [{ role: "user", content }] }),
    });
    const raw = await r.text();
    if (!r.ok) throw new Error(`github: HTTP ${r.status} ${raw.slice(0, 80)}`);
    const d = JSON.parse(raw);
    const v = firstJson(d.choices[0].message.content); v._model = "github/gpt-4o-mini"; return v;
  } finally { clearTimeout(t); }
}

const OR_MODELS = ["google/gemma-4-26b-a4b-it:free", "qwen/qwen3.8-27b:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemma-4-31b-it:free", "dots-studio/dots-3-note-preview:free", "thinkingmachines/inkling:free"];
const orDownUntil = {};
async function openRouter(env, text, imgs, deadline) {
  // fourth brain: OpenRouter free vision models (own daily quota per account); two at a time, first JSON wins
  if (!env.OPENROUTER_KEY) throw new Error("openrouter: no key");
  const content = [{ type: "text", text }, ...imgs.slice(0, 4).map((im) => ({ type: "image_url", image_url: { url: `data:${im.ct};base64,${im.b64}` } }))];
  const one = async (model) => {
    const left = deadline - Date.now();
    if (left < 4000) throw new Error(`${model}: no time`);
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), Math.min(25000, left));
    try {
      const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST", signal: ctl.signal,
        headers: { "Authorization": "Bearer " + env.OPENROUTER_KEY, "Content-Type": "application/json", "HTTP-Referer": "https://swiper-judge.workers.dev", "X-Title": "swiper-judge" },
        body: JSON.stringify({ model, temperature: 0, max_tokens: 1500, reasoning: { effort: "low" }, messages: [{ role: "user", content }] }),
      });
      const d = await r.json();
      if (!r.ok || d.error) { if (r.status === 429) orDownUntil[model] = Date.now() + 600000; throw new Error(`${model}: ${r.status} ${String(d.error && d.error.message || "").slice(0, 60)}`); }
      const msg = d.choices[0].message;
      let c = msg.content || "";
      if (!/\{[\s\S]*\}/.test(c) && msg.reasoning) c = msg.reasoning;
      const v = firstJson(c);
      if (emptyVerdict(v)) throw new Error(`${model}: empty verdict`);
      v._model = "or/" + model.split("/")[1]; return v;
    } finally { clearTimeout(t); }
  };
  const order = OR_MODELS.filter((m) => Date.now() >= (orDownUntil[m] || 0));
  const errs = [];
  for (let i = 0; i < order.length; i += 2) {
    if (deadline - Date.now() < 4000) break;
    try { return await Promise.any(order.slice(i, i + 2).map(one)); }
    catch (e) { for (const x of (e.errors || [e])) errs.push(String(x.message || x).slice(0, 70)); }
  }
  throw new Error(errs[errs.length - 1] || "openrouter failed");
}

// ---------------------------------------------------------------- relay to the GitHub-hosted runner (open-weights vision model, no API key)
export class Relay {
  constructor(state, env) { this.state = state; this.env = env; this.pending = new Map(); this.seat = null; }
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/relay/ws") {
      if (request.headers.get("Upgrade") !== "websocket") return new Response("expected websocket", { status: 426 });
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.state.acceptWebSocket(server);
      this.seat = server;
      return new Response(null, { status: 101, webSocket: client });
    }
    if (url.pathname === "/relay/status") {
      return Response.json({ seat: this.state.getWebSockets().length > 0, pending: this.pending.size, sockets: this.state.getWebSockets().length });
    }
    if (url.pathname === "/relay/job") {
      // several runners can hold seats; hand the job to one that is not busy (least jobs in flight)
      this.busy = this.busy || new Map();
      const seats = this.state.getWebSockets();
      if (!seats.length) return Response.json({ error: "runner: no seat connected" });
      const seat = seats.slice().sort((a, b) => (this.busy.get(a) || 0) - (this.busy.get(b) || 0))[0];
      this.busy.set(seat, (this.busy.get(seat) || 0) + 1);
      const done = () => this.busy.set(seat, Math.max(0, (this.busy.get(seat) || 1) - 1));
      const body = await request.json();
      const id = "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const waitMs = Math.max(1000, Math.min(120000, body.waitMs || 38000));
      const verdict = await new Promise((resolve) => {
        const t = setTimeout(() => { this.pending.delete(id); done(); resolve({ error: "runner: no answer in " + Math.round(waitMs / 1000) + "s" }); }, waitMs);
        this.pending.set(id, (v) => { clearTimeout(t); done(); resolve(v); });
        try { seat.send(JSON.stringify({ type: "job", id, text: body.text, urls: body.urls })); }
        catch (e) { clearTimeout(t); this.pending.delete(id); done(); resolve({ error: "runner: send failed " + String(e).slice(0, 60) }); }
      });
      return Response.json(verdict);
    }
    return new Response("relay", { status: 404 });
  }
  webSocketMessage(ws, msg) {
    let m; try { m = JSON.parse(msg); } catch { return; }
    if (m.type === "ping") { try { ws.send(JSON.stringify({ type: "pong" })); } catch {} return; }
    if (m.type === "result" && this.pending.has(m.id)) this.pending.get(m.id)(m.verdict || { error: "runner: empty result" });
  }
  webSocketClose(ws) { if (this.seat === ws) this.seat = null; }
  webSocketError(ws) { if (this.seat === ws) this.seat = null; }
}
const RUNNER_WINDOW = 120000;  // the CPU runner needs 60-80s per card (one photo); the page prefetches so this rarely blocks a swipe
async function runnerJudge(env, text, imgs, urls, t0) {
  if (!env.RELAY) throw new Error("runner: no relay binding");
  const left = t0 + RUNNER_WINDOW - Date.now();
  if (left < 15000) throw new Error("runner: no time");
  const stub = env.RELAY.get(env.RELAY.idFromName("seat"));
  const r = await stub.fetch("https://relay/relay/job", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, urls, waitMs: left - 2000 }) });
  const v = await r.json();
  if (v.error) throw new Error(v.error);
  if (emptyVerdict(v)) throw new Error("runner: empty verdict");
  return v;
}

const INSTALL_HTML = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>swiper</title>
<body style="font:17px -apple-system,system-ui;background:#111;color:#eee;padding:24px;max-width:520px;margin:auto">
<h2>swiper</h2><p>1. <a style="color:#fd5068" href="/Swiper.shortcut">Add the Shortcut</a> (tap, then Add Shortcut).</p>
<p>2. In Safari open <b>tinder.com</b>, tap Share, run <b>Swiper</b>.</p><p>3. Panel &rarr; Vision tab &rarr; paste the worker key once. Start.</p></body>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(request) });
    if (request.method === "GET" && (url.pathname === "/swiper.js" || url.pathname === "/Swiper.shortcut")) {
      // the script the iOS Shortcut evals inside tinder.com (repo is private, so GitHub raw is out); any origin, never cached
      const r = await env.ASSETS.fetch(request);
      const h = new Headers(r.headers); h.set("Access-Control-Allow-Origin", "*"); h.set("Cache-Control", "no-store");
      if (url.pathname === "/swiper.js") h.set("Content-Type", "application/javascript; charset=utf-8");
      return new Response(r.body, { status: r.status, headers: h });
    }
    if (request.method === "GET" && url.pathname === "/") return new Response(INSTALL_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    if (!env.JUDGE_KEY || request.headers.get("X-Key") !== env.JUDGE_KEY) return json({ error: "bad key" }, 401, request);
    if (url.pathname === "/relay/ws" || url.pathname === "/relay/status") {
      return env.RELAY.get(env.RELAY.idFromName("seat")).fetch(request);
    }
    if (url.pathname === "/log" && request.method === "POST") {
      // taste dataset: verdict + decision only, never photos. The page sends batches ({entries:[...]}) = ONE KV write per ~25 cards
      // (free KV is 1,000 writes/day; per-card writes hit 50% in an afternoon). key = time-sortable
      let e; try { e = await request.json(); } catch { return json({ error: "bad json" }, 400, request); }
      const ua = (request.headers.get("User-Agent") || "").slice(0, 60);
      const entries = (Array.isArray(e.entries) ? e.entries : [e]).slice(0, 500).map((x) => ({ ...x, ua }));
      if (!entries.length) return json({ ok: true, n: 0 }, 200, request);
      const key = `log:${new Date().toISOString()}:${Math.random().toString(36).slice(2, 7)}`;
      try { await env.LOG.put(key, JSON.stringify(entries)); }
      catch (err) { return json({ ok: false, error: String(err).slice(0, 100) }, 503, request); }   // quota gone: the page keeps the batch and retries later
      return json({ ok: true, n: entries.length }, 200, request);
    }
    if (url.pathname === "/log" && request.method === "GET") {
      const lim = Math.min(1000, +(url.searchParams.get("limit") || 200));
      const list = await env.LOG.list({ prefix: "log:", limit: lim, cursor: url.searchParams.get("cursor") || undefined });
      const vals = await Promise.all(list.keys.map((k) => env.LOG.get(k.name)));   // parallel: a sequential loop over hundreds of keys blew the request budget
      const rows = [];
      list.keys.forEach((k, j) => {
        const v = JSON.parse(vals[j] || "null");
        (Array.isArray(v) ? v : [v]).forEach((r, i) => { if (r) rows.push({ key: k.name + (Array.isArray(v) ? ":" + i : ""), ...r }); });
      });
      return json({ rows, cursor: list.list_complete ? null : list.cursor }, 200, request);
    }
    if (request.method !== "POST") return new Response("swiper-judge ok", { status: 200 });
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad json" }, 400, request); }
    const t0 = Date.now(), deadline = t0 + BUDGET;
    const urls = ownPhotos((body.urls || []).slice(0, 9));
    if (!urls.length) return json({ error: "no photos" }, 400, request);
    const fetched = (await Promise.all(urls.map(fetchPhoto))).filter(Boolean);
    const real = fetched.filter((p) => p.bytes.length >= TINY);
    if (fetched.length && !real.length) return json({ error: `no usable photos (${fetched.length} tiny thumbnails, profile has no real pictures)` }, 200, request);
    if (!real.length) return json({ error: "no photos could be fetched" }, 200, request);
    const imgs = real.map((p) => ({ ct: p.ct, b64: b64(p.bytes) }));
    const kb = Math.round(real.reduce((a, p) => a + p.bytes.length, 0) / 1024);
    const t1 = Date.now();
    const errs = [];
    if (body.force === "runner") {
      try {
        const v = await runnerJudge(env, body.text || "", imgs, urls, t0);
        v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
        return json(v, 200, request);
      } catch (e) { return json({ error: "forced runner | " + String(e.message || e).slice(0, 100) }, 200, request); }
    }
    if (body.force === "llama" || body.force === "github" || body.force === "mistral") {
      errs.push("forced " + body.force);
    } else {
      try {
        let v = await gemini(env, body.text || "", imgs, deadline, false);
        if (emptyVerdict(v) && deadline - Date.now() > 8000) {
          try { const v2 = await gemini(env, body.text || "", imgs, deadline, true); if (!emptyVerdict(v2)) v = v2; } catch {}
        }
        v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
        return json(v, 200, request);
      } catch (e) { errs.push(String(e.message || e).slice(0, 100)); }
    }
    if (body.force !== "llama" && body.force !== "github") {
      try {
        const v = await mistralVision(env, body.text || "", imgs, deadline);
        v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
        v._fallback = errs[0];
        return json(v, 200, request);
      } catch (e) { errs.push("mistral: " + String(e.message || e).slice(0, 100)); }
      if (body.force === "mistral") return json({ error: errs.join(" | ") }, 200, request);
    }
    if (body.force === "github") {
      try {
        const v = await githubModels(env, body.text || "", imgs, deadline);
        v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
        v._fallback = errs[0];
        return json(v, 200, request);
      } catch (e) { errs.push(String(e.message || e).slice(0, 100)); }
      if (body.force === "github") return json({ error: errs.join(" | ") }, 200, request);
    }
    try {
      const v = await llamaVision(env, body.text || "", imgs, deadline);
      v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
      v._fallback = errs[0];
      return json(v, 200, request);
    } catch (e) { errs.push("llama: " + String(e.message || e).slice(0, 100)); }
    try {
      const v = await openRouter(env, body.text || "", imgs, deadline);
      v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
      v._fallback = errs[0];
      return json(v, 200, request);
    } catch (e) { errs.push("openrouter: " + String(e.message || e).slice(0, 100)); }
    // last resort: the GitHub-hosted CPU runner (slow, but no quota); only when a seat is connected
    try {
      const v = await runnerJudge(env, body.text || "", imgs, urls, t0);
      v._timing = `fetch ${((t1 - t0) / 1000).toFixed(1)}s (${kb}KB, ${imgs.length} photos) model ${((Date.now() - t1) / 1000).toFixed(1)}s`;
      v._fallback = errs[0];
      return json(v, 200, request);
    } catch (e) { errs.push(String(e.message || e).slice(0, 100)); }
    return json({ error: "all models failed: " + errs.join(" | "), kind: "brain_down" }, 200, request);
  },
};
