#!/usr/bin/env python3
"""swiper gallery (slybox): every card swiper liked, with its photos and plain-English grades, split into matched / no match yet.

- every 60s: drain __swiper.galleryDrain() over CDP, download the photos (tinder links expire)
- every 10 min: read /v2/matches through the page, mark who matched; matches the bot never logged get added and graded
- every 15 min: rebuild ~/swiper/gallery/view (matched/, no-match-yet/) and rclone sync it to MEGA (remote swipergallery)
- serves the judge page on 127.0.0.1:8766 under /<token>/ (Caddy routes https://35-211-205-184.sslip.io/<token>/ here)
data: ~/swiper/gallery/items.json + img/<key>/<n>.jpg; token: ~/.swiper/gallery.token
"""
import hashlib, json, os, re, shutil, subprocess, threading, time, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import websocket

CDP = "http://127.0.0.1:9223"
HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, "swiper", "gallery")
IMG = os.path.join(DATA, "img")
VIEW = os.path.join(DATA, "view")
ITEMS = os.path.join(DATA, "items.json")
TOKEN = open(os.path.join(HOME, ".swiper", "gallery.token")).read().strip()
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
REMOTE = "swipergallery:swiper-gallery"
KEEP_NOMATCH_DAYS = 10    # no-match photos are ~0.5 GB/day at 3,000 swipes; matched ones are kept forever
MAX_PHOTOS = 3
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

lock = threading.Lock()
items = {}


def log(m):
    with open(os.path.join(HOME, ".swiper", "gallery.log"), "a") as f: f.write(time.strftime("%m-%d %H:%M:%S ") + m + "\n")


def load():
    global items
    try: items = json.load(open(ITEMS))
    except (OSError, ValueError): items = {}


def save():
    tmp = ITEMS + ".tmp"
    with open(tmp, "w") as f: json.dump(items, f)
    os.replace(tmp, ITEMS)


def page_js(expr, timeout=120):
    """evaluate an expression in the tinder tab; returns the value (strings stay strings)"""
    tabs = json.load(urllib.request.urlopen(CDP + "/json/list", timeout=5))
    tab = next((t for t in tabs if t.get("type") == "page" and "tinder.com" in t["url"]), None)
    if not tab: return None
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], suppress_origin=True, timeout=timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1: return m.get("result", {}).get("result", {}).get("value")
    finally: ws.close()


def key_for(e):
    if e.get("uid"): return e["uid"]
    raw = "%s|%s|%s" % (e.get("name"), e.get("age"), (e.get("photos") or [""])[0].split("?")[0])
    return "k" + hashlib.sha1(raw.encode()).hexdigest()[:15]


def age_from(bd):
    try:
        b = datetime.fromisoformat(bd.replace("Z", "+00:00")); n = datetime.now(timezone.utc)
        return n.year - b.year - ((n.month, n.day) < (b.month, b.day))
    except Exception: return 0


def download(key, urls):
    d = os.path.join(IMG, key); os.makedirs(d, exist_ok=True); got = 0
    for i, u in enumerate(urls[:MAX_PHOTOS], 1):
        p = os.path.join(d, "%d.jpg" % i)
        if os.path.exists(p): got += 1; continue
        try:
            body = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30).read()
            if len(body) > 2000:
                with open(p, "wb") as f: f.write(body)
                got += 1
        except Exception as e: log("photo %s/%d: %r" % (key, i, e)[:200])
    return got


def drain():
    raw = page_js("window.__swiper&&__swiper.galleryDrain?JSON.stringify(__swiper.galleryDrain()):'[]'")
    new = json.loads(raw or "[]")
    for e in new:
        k = key_for(e)
        n = download(k, e.get("photos") or [])
        with lock:
            it = items.get(k, {})
            it.update({"key": k, "uid": e.get("uid") or it.get("uid", ""), "name": e.get("name"), "age": e.get("age"), "why": e.get("why"),
                       "looks": e.get("looks"), "verdict": e.get("verdict"), "bio": e.get("bio", ""), "liked_at": e.get("t") or int(time.time() * 1000),
                       "source": "bot", "photos": n})
            it.setdefault("matched", False)
            items[k] = it
    if new:
        with lock: save()
        log("drained %d likes" % len(new))


MATCHES_JS = """(async()=>{let tok=localStorage.getItem("TinderWeb/APIToken");try{tok=JSON.parse(tok)}catch(e){}
const h={"X-Auth-Token":tok,"platform":"web"};const out=[];
for(const msg of [0,1]){let pt="";for(let i=0;i<10;i++){
 const r=await fetch("https://api.gotinder.com/v2/matches?count=60&message="+msg+"&locale=en"+(pt?"&page_token="+pt:""),{headers:h});
 if(!r.ok)break;const j=await r.json();const d=(j&&j.data)||{};
 (d.matches||[]).forEach(m=>{const p=m.person||{};out.push({uid:p._id,name:p.name,bd:p.birth_date,at:m.created_date,msgs:m.message_count||0,
  photos:(p.photos||[]).map(ph=>{const b=(ph.processedFiles||[]).filter(f=>f.width>=480&&f.width<=800)[0];return (b&&b.url)||ph.url;}).filter(Boolean)});});
 if(!d.next_page_token)break;pt=d.next_page_token;}}
return JSON.stringify(out);})()"""


def sync_matches():
    ms = json.loads(page_js(MATCHES_JS) or "[]")
    by_uid = {it.get("uid"): k for k, it in items.items() if it.get("uid")}
    by_name = {(it.get("name"), it.get("age")): k for k, it in items.items()}
    fresh = 0
    for m in ms:
        if not m.get("uid"): continue
        age = age_from(m.get("bd") or "")
        k = by_uid.get(m["uid"]) or by_name.get((m.get("name"), age))
        if not k:   # matched but never went through the gallery (before 1.10.0, or liked by hand)
            k = m["uid"]
            n = download(k, m.get("photos") or [])
            with lock: items[k] = {"key": k, "uid": m["uid"], "name": m.get("name"), "age": age, "source": "match", "photos": n, "liked_at": 0, "matched": False}
        with lock:
            it = items[k]
            if not it.get("matched"): fresh += 1
            it.update({"matched": True, "matched_at": m.get("at"), "msgs": m.get("msgs", 0), "uid": m["uid"]})
    with lock: save()
    if fresh: log("matches: %d new (%d total from tinder)" % (fresh, len(ms)))


def grade_missing(limit=5):
    """matches the bot never judged: grade them in the page with the same judge + words as a live card"""
    with lock: todo = [k for k, it in items.items() if not it.get("verdict") and it.get("photos") and not it.get("grade_failed")][:limit]
    for k in todo:
        urls = ["https://35-211-205-184.sslip.io/%s/img/%s/%d.jpg" % (TOKEN, k, i) for i in range(1, items[k]["photos"] + 1)]
        expr = "__swiper.judge({photos:%s,bio:''}).then(v=>JSON.stringify({v:v,looks:__swiper.describe(v)})).catch(e=>JSON.stringify({error:String(e&&e.message||e)}))" % json.dumps(urls)
        try: r = json.loads(page_js(expr, timeout=180) or "{}")
        except Exception as e: r = {"error": repr(e)}
        with lock:
            if r.get("v"): items[k]["verdict"] = r["v"]; items[k]["looks"] = r.get("looks", "")
            else: items[k]["grade_failed"] = True; log("grade %s: %s" % (k, str(r.get("error"))[:150]))
            save()


def folder_name(it):
    name = "%s %s" % (it.get("name") or "?", it.get("age") or "")
    looks = it.get("looks") or ""
    s = (name + (" - " + looks if looks else "")).strip()
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)[:120].strip()
    return s + " [" + it["key"][-6:] + "]"


def rebuild_view_and_sync():
    now = time.time() * 1000
    with lock:
        for it in items.values():   # retention: drop old no-match photos, keep the record
            if not it.get("matched") and it.get("liked_at") and now - it["liked_at"] > KEEP_NOMATCH_DAYS * 86400000 and it.get("photos"):
                shutil.rmtree(os.path.join(IMG, it["key"]), ignore_errors=True); it["photos"] = 0; it["pruned"] = True
        save(); snap = list(items.values())
    tmp = VIEW + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    for it in snap:
        if not it.get("photos"): continue
        d = os.path.join(tmp, "matched" if it.get("matched") else "no-match-yet", folder_name(it)); os.makedirs(d, exist_ok=True)
        for i in range(1, it["photos"] + 1):
            src = os.path.join(IMG, it["key"], "%d.jpg" % i)
            if os.path.exists(src): os.symlink(src, os.path.join(d, "%d.jpg" % i))
        with open(os.path.join(d, "info.txt"), "w") as f:
            f.write("%s %s\nliked because: %s\nlooks: %s\nmatched: %s\nyour call: %s\n" % (it.get("name"), it.get("age"), it.get("why") or "-", it.get("looks") or "-",
                    "yes" if it.get("matched") else "not yet", (it.get("judge") or {}).get("verdict", "-")))
    shutil.rmtree(VIEW, ignore_errors=True); os.replace(tmp, VIEW)
    r = subprocess.run(["rclone", "sync", "-L", VIEW, REMOTE, "--transfers", "4", "--retries", "2"], capture_output=True, text=True, timeout=3000)
    if r.returncode: log("rclone sync failed: " + r.stderr[-300:])


def worker():
    last_m = last_v = 0
    while True:
        try: drain()
        except Exception as e: log("drain: %r" % e)
        if time.time() - last_m > 600:
            last_m = time.time()
            try: sync_matches(); grade_missing()
            except Exception as e: log("matches: %r" % e)
        if time.time() - last_v > 900:
            last_v = time.time()
            try: rebuild_view_and_sync()
            except Exception as e: log("view/sync: %r" % e)
        time.sleep(60)


def stats():
    with lock: snap = list(items.values())
    bot = [it for it in snap if it.get("source") == "bot"]
    traits = {}
    for it in bot:
        for t in [x.strip() for x in (it.get("looks") or "").split(",") if x.strip()]:
            s = traits.setdefault(t, [0, 0]); s[0] += 1; s[1] += 1 if it.get("matched") else 0
    reasons = {}
    for it in bot:
        r = (it.get("why") or "?").split(",")[0].strip()
        s = reasons.setdefault(r, [0, 0, 0, 0]); s[0] += 1; s[1] += 1 if it.get("matched") else 0
        j = (it.get("judge") or {}).get("verdict")
        if j in ("yes", "no"): s[2] += 1; s[3] += 1 if j == "yes" else 0
    judged = [it for it in snap if (it.get("judge") or {}).get("verdict") in ("yes", "no")]
    return {"liked": len(bot), "matched": sum(1 for it in snap if it.get("matched")), "matched_from_bot": sum(1 for it in bot if it.get("matched")),
            "judged": len(judged), "you_yes": sum(1 for it in judged if it["judge"]["verdict"] == "yes"),
            "traits": sorted(([t, s[0], s[1]] for t, s in traits.items()), key=lambda x: -x[1]),
            "reasons": sorted(([r] + s for r, s in reasons.items()), key=lambda x: -x[1])}


def listing(tab, off, lim):
    with lock: snap = list(items.values())
    if tab == "matched": rows = [it for it in snap if it.get("matched")]; rows.sort(key=lambda it: it.get("matched_at") or "", reverse=True)
    elif tab == "nomatch": rows = [it for it in snap if not it.get("matched") and it.get("photos")]; rows.sort(key=lambda it: it.get("liked_at") or 0, reverse=True)
    else: rows = [it for it in snap if it.get("photos") and not (it.get("judge") or {}).get("verdict")]; rows.sort(key=lambda it: (not it.get("matched"), -(it.get("liked_at") or 0)))
    keep = ("key", "name", "age", "why", "looks", "matched", "photos", "judge", "source", "liked_at", "matched_at", "bio")
    return {"total": len(rows), "rows": [{k: it.get(k) for k in keep} for it in rows[off:off + lim]]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype != "image/jpeg" else "private, max-age=86400")
        self.send_header("X-Robots-Tag", "noindex"); self.end_headers(); self.wfile.write(body)

    def path_in(self):
        pre = "/" + TOKEN + "/"
        p = self.path.split("?")[0]
        return p[len(pre):] if p.startswith(pre) else None

    def do_GET(self):
        p = self.path_in()
        if p is None: return self.send(404, "not found", "text/plain")
        q = dict(x.split("=", 1) for x in (self.path.split("?", 1)[1].split("&") if "?" in self.path else []) if "=" in x)
        if p in ("", "index.html"): return self.send(200, open(PAGE, "rb").read(), "text/html; charset=utf-8")
        if p == "api/stats": return self.send(200, stats())
        if p == "api/items": return self.send(200, listing(q.get("tab", "judge"), int(q.get("off", 0)), min(200, int(q.get("lim", 40)))))
        m = re.fullmatch(r"img/([A-Za-z0-9]+)/(\d)\.jpg", p)
        if m:
            f = os.path.join(IMG, m.group(1), m.group(2) + ".jpg")
            if os.path.exists(f): return self.send(200, open(f, "rb").read(), "image/jpeg")
        return self.send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path_in() != "api/judge": return self.send(404, "not found", "text/plain")
        try: b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError: return self.send(400, {"error": "bad json"})
        k, v = b.get("key"), b.get("verdict")
        if v not in ("yes", "no", "skip"): return self.send(400, {"error": "verdict must be yes, no or skip"})
        tags = [str(t)[:40] for t in (b.get("tags") or [])][:8]
        with lock:
            if k not in items: return self.send(404, {"error": "unknown card"})
            items[k]["judge"] = {"verdict": v, "tags": tags, "t": int(time.time() * 1000)}
            save()
        self.send(200, {"ok": True})


if __name__ == "__main__":
    os.makedirs(IMG, exist_ok=True); load()
    threading.Thread(target=worker, daemon=True).start()
    log("gallery up, %d cards" % len(items))
    ThreadingHTTPServer(("127.0.0.1", 8766), H).serve_forever()
