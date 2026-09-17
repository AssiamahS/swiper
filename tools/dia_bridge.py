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
CDP = "http://127.0.0.1:9223"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
OR_MODELS = ["nex-agi/nex-n2.5-pro:free", "inclusionai/ling-3.0-flash-vl:free", "dots-studio/dots-3-note-preview:free"]


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
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                return m.get("result", {}).get("result", {}).get("value")


def fetch_photo(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tinder.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), (r.headers.get("Content-Type") or "image/jpeg").split(";")[0]


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


def gemini(text, imgs):
    last = None
    for model in GEMINI_MODELS:
        parts = [{"text": text}] + [{"inline_data": {"mime_type": ct, "data": base64.b64encode(b).decode()}} for b, ct in imgs]
        body = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0, "maxOutputTokens": 800, "responseMimeType": "application/json"}}).encode()
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GKEY}", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            out = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            v = first_json(out); v["_model"] = model; return v
        except urllib.error.HTTPError as e:
            last = f"{model}: HTTP {e.code} {e.read().decode()[:80]}"
        except Exception as e:
            last = f"{model}: {e}"
        log("gemini " + last)
    raise RuntimeError(last or "gemini failed")


def openrouter(text, imgs):
    last = None
    for model in OR_MODELS:
        content = [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": f"data:{ct};base64," + base64.b64encode(b).decode()}} for b, ct in imgs]
        body = json.dumps({"model": model, "temperature": 0, "max_tokens": 1500, "reasoning": {"effort": "low"}, "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={"Authorization": "Bearer " + ORKEY, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
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


def judge(req):
    imgs = []
    for u in req["urls"][:9]:
        try:
            imgs.append(fetch_photo(u))
        except Exception as e:
            log(f"photo fetch failed: {str(e)[:60]}")
    if not imgs:
        return {"error": "no photos could be fetched"}
    try:
        return gemini(req["text"], imgs)
    except Exception as e:
        log(f"gemini exhausted ({str(e)[:60]}), trying openrouter")
    try:
        return openrouter(req["text"], imgs)
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
            reqs = json.loads(tab.js("JSON.stringify(window.__swiperBridge ? window.__swiperBridge.take() : [])") or "[]")
            for r in reqs:
                t = time.time(); v = judge(r)
                tab.js("window.__swiperBridge && window.__swiperBridge.deliver(%s, %s)" % (json.dumps(r["id"]), json.dumps(v)))
                log(f"{r['id']} [{len(r['urls'])} photos] -> {('ERR ' + v['error']) if 'error' in v else (v.get('_model') + ' ' + json.dumps({k: v.get(k) for k in ('body', 'in_shape', 'face', 'full_body_visible', 'swimwear', 'dyed_hair', 'facial_piercings', 'alt_style', 'glutes', 'gym_selfie')}))} ({time.time() - t:.1f}s)")
            time.sleep(0.4 if reqs else 0.8)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"bridge error: {str(e)[:120]}; reconnecting in 5s"); tab = None; time.sleep(5)


if __name__ == "__main__":
    main()
