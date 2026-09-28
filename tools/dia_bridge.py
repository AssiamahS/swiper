#!/usr/bin/env python3
"""Mac-side helper for the Tinder lane running in Dia.

Polls the page's judge queue over CDP, fetches the card's photos here (the CDN has no CORS and
Chrome blocks page->loopback), asks Gemini (model list from the keychain key, OpenRouter free
models as fallback), and delivers the verdict back into the page. Also keeps the lane alive:
if the tab reloaded or the panel is stopped, it re-injects swiper.js and presses Start.

    python3 tools/dia_bridge.py            # runs forever; launchd: com.sly.swiper-bridge
"""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error
import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
CDP = os.environ.get("SWIPER_CDP", "http://127.0.0.1:9223")
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
OR_MODELS = ["nex-agi/nex-n2.5-pro:free", "google/gemma-4-31b-it:free"]  # ling-3.0 is 404 now, dots returns no JSON


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def keychain(s):
    return subprocess.run(["security", "find-generic-password", "-s", s, "-w"], capture_output=True, text=True).stdout.strip()


GKEY, ORKEY = keychain("gemini"), keychain("openrouter")


class Tab:
    def __init__(self):
        tabs = json.load(urllib.request.urlopen(CDP + "/json/list"))
        t = next((t for t in tabs if "tinder.com" in t["url"] and t.get("type") == "page"), None)
        if not t:
            raise RuntimeError("no tinder tab in Dia")
        self.ws = websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self.n = 0

    def js(self, expr):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
        while True:
            raw = self.ws.recv()
            try:
                m = json.loads(raw)
            except ValueError:
                continue  # pymobiledevice3's CDP server sends the odd empty/non-JSON frame
            if m.get("id") == self.n:
                return m.get("result", {}).get("result", {}).get("value")


def _get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tinder.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), (r.headers.get("Content-Type") or "image/jpeg").split(";")[0]


SIZE_RE = re.compile(r"/(\d+)x(\d+)_")
TINY = 12 * 1024  # a real card photo is 40-120KB; the mobile card sometimes exposes 84x106 thumbnails


def fetch_photo(u):
    """Fetch the photo; when the URL is a small processed size (or the bytes are tiny) try the 640x800
    rendition, then the original (the CDN signature covers the whole /u/<uid>/ folder)."""
    m = SIZE_RE.search(u)
    candidates = [u]
    if m and int(m.group(1)) < 400:
        candidates = [SIZE_RE.sub("/640x800_", u), SIZE_RE.sub("/", u), u]
    best = None
    for c in candidates:
        try:
            b, ct = _get(c)
        except Exception:
            continue
        if best is None or len(b) > len(best[0]):
            best = (b, ct)
        if len(b) >= TINY:
            break
    if best is None:
        raise RuntimeError("all renditions failed")
    if len(best[0]) < TINY:
        log(f"tiny photo {len(best[0]) // 1024}KB {u[:90]}")
    return best


def first_json(text):
    try:
        w = json.loads(text)
        if isinstance(w, list):
            w = w[0]
        if isinstance(w, dict):
            return w
    except Exception:
        pass
    i = text.find("{")
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{": depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[i:j + 1])
    raise ValueError("no json")


def empty_verdict(v):
    return not any(float(v.get(k) or 0) for k in ("face", "feminine", "photo_quality", "curves"))


MODEL_TIMEOUT = 20      # per call; the page gives the bridge 45s in total (cfg.vision.bridgeTimeout)
BUDGET = 42             # seconds from request start to give up, so the page never times out first
slow_until = {}         # model -> epoch until which we try the other model first (after a timeout)


def shrink(b, ct, max_side=768):
    """Downscale to <=768px JPEG: fewer bytes, and Gemini bills/tiles big images, so it answers faster."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(b))
        if max(im.size) <= max_side and len(b) < 160 * 1024:
            return b, ct
        im = im.convert("RGB"); im.thumbnail((max_side, max_side))
        out = io.BytesIO(); im.save(out, "JPEG", quality=80)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return b, ct


def gemini(text, imgs, deadline, reverse=False):
    last = None
    order = GEMINI_MODELS[::-1] if reverse else list(GEMINI_MODELS)
    now = time.time()
    order.sort(key=lambda m: slow_until.get(m, 0) > now)  # a model that just timed out goes last
    for model in order:
        left = deadline - time.time()
        if left < 4:
            break
        parts = [{"text": text}] + [{"inline_data": {"mime_type": ct, "data": base64.b64encode(b).decode()}} for b, ct in imgs]
        body = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0, "maxOutputTokens": 800, "responseMimeType": "application/json"}}).encode()
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GKEY}", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=min(MODEL_TIMEOUT, left)) as r:
                d = json.loads(r.read())
            out = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            v = first_json(out); v["_model"] = model; return v
        except urllib.error.HTTPError as e:
            last = f"{model}: HTTP {e.code} {e.read().decode()[:80]}"
        except Exception as e:
            last = f"{model}: {e}"
            if "timed out" in str(e):
                slow_until[model] = time.time() + 90
        log("gemini " + last)
    raise RuntimeError(last or "gemini failed")


def openrouter(text, imgs, deadline):
    last = None
    for model in OR_MODELS:
        if deadline - time.time() < 6:
            break
        content = [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": f"data:{ct};base64," + base64.b64encode(b).decode()}} for b, ct in imgs]
        body = json.dumps({"model": model, "temperature": 0, "max_tokens": 1500, "reasoning": {"effort": "low"}, "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={"Authorization": "Bearer " + ORKEY, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=max(5, deadline - time.time())) as r:
                d = json.loads(r.read())
            if d.get("error"):
                raise RuntimeError(str(d["error"].get("message"))[:80])
            msg = d["choices"][0]["message"]
            c = msg.get("content") or ""
            if not re.search(r"\{[\s\S]*\}", c) and msg.get("reasoning"):
                c = msg["reasoning"]
            v = first_json(c); v["_model"] = model; return v
        except Exception as e:
            last = f"{model}: {e}"; log("openrouter " + last)
    raise RuntimeError(last or "openrouter failed")


UID_RE = re.compile(r"/u/([^/]+)/")


def own_photos(urls):
    """The mobile card scrape can pick up other users' preview thumbnails; a profile's own photos all live
    under one /u/<uid>/ folder, so keep the biggest folder group (ties keep everything, the size filter sorts it out)."""
    groups = {}
    for u in urls:
        m = UID_RE.search(u)
        groups.setdefault(m.group(1) if m else u, []).append(u)
    best = max(groups.values(), key=len)
    if len(best) > 1 and len(best) < len(urls):
        log(f"dropping {len(urls) - len(best)} photo(s) from other profiles' folders")
        return best
    return urls


def judge(req):
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    def get(u):
        try:
            return fetch_photo(u)
        except Exception as e:
            log(f"photo fetch failed: {str(e)[:60]}"); return None
    with ThreadPoolExecutor(max_workers=9) as ex:
        imgs = [x for x in ex.map(get, own_photos(req["urls"][:9])) if x]
    t1 = time.time()
    real = [x for x in imgs if len(x[0]) >= TINY]
    if imgs and not real:
        return {"error": f"no usable photos ({len(imgs)} tiny thumbnails, profile has no real pictures)"}  # never judge junk: a like from placeholders is worse than a pass
    imgs = [shrink(b, ct) for b, ct in real]
    if not imgs:
        return {"error": "no photos could be fetched"}
    deadline = t0 + BUDGET
    kb = sum(len(b) for b, _ in imgs) // 1024
    try:
        v = gemini(req["text"], imgs, deadline)
        if empty_verdict(v) and deadline - time.time() > 8:  # all zeros = the model saw nothing usable; one more try on the other model
            log(f"empty verdict from {v.get('_model')} on {kb}KB, retrying on the other model")
            v2 = gemini(req["text"], imgs, deadline, reverse=True)
            if not empty_verdict(v2):
                v = v2
        v["_timing"] = f"fetch {t1 - t0:.1f}s ({kb}KB) model {time.time() - t1:.1f}s"
        return v
    except Exception as e:
        if deadline - time.time() < 10:
            return {"error": f"gemini too slow ({str(e)[:60]})"}
        log(f"gemini exhausted ({str(e)[:60]}), trying openrouter")
    try:
        return openrouter(req["text"], imgs, deadline)
    except Exception as e:
        return {"error": f"all models failed: {str(e)[:80]}"}


def ensure_running(tab):
    state = tab.js("JSON.stringify({loaded: !!window.__swiper, running: !!(window.__swiper && document.querySelector('#swiper-panel .sw-run.on')), url: location.href})")
    st = json.loads(state or "{}")
    if not st.get("loaded") or not st.get("running"):
        log(f"lane not running ({st}), injecting + starting")
        subprocess.run([sys.executable, os.path.join(HERE, "dia_inject.py"), "--local", "--start"], capture_output=True, text=True, timeout=120)
        return False
    return True


def main():
    tab = None; last_alive = 0
    while True:
        try:
            if tab is None:
                tab = Tab(); log("attached to the Tinder tab")
            if time.time() - last_alive > 30:
                ensure_running(tab); last_alive = time.time()
                # one status line per check so nobody needs a second CDP client (a second client kicks this one off the page)
                log("status " + str(tab.js("localStorage.getItem('swiper.stats') + ' | ' + ((document.querySelector('#swiper-panel .sw-status')||{}).innerText||'')"))[:200])
            reqs = json.loads(tab.js("JSON.stringify(window.__swiperBridge ? window.__swiperBridge.take() : [])") or "[]")
            for r in reqs:
                t = time.time(); v = judge(r)
                tab.js("window.__swiperBridge && window.__swiperBridge.deliver(%s, %s)" % (json.dumps(r["id"]), json.dumps(v)))
                log(f"{r['id']} [{len(r['urls'])} photos] -> {('ERR ' + v['error']) if 'error' in v else (v.get('_model') + ' ' + v.get('_timing', '') + ' ' + json.dumps({k: v.get(k) for k in ('body', 'in_shape', 'face', 'full_body_visible', 'swimwear', 'dyed_hair', 'facial_piercings', 'alt_style', 'glutes', 'gym_selfie')}))} ({time.time() - t:.1f}s)")
            time.sleep(0.4 if reqs else 0.8)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"bridge error: {str(e)[:120]}; reconnecting in 5s"); tab = None; time.sleep(5)


if __name__ == "__main__":
    main()
