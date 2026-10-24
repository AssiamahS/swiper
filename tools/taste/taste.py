#!/usr/bin/env python3
"""swiper's own judge: CLIP on the slybox CPU, no outside AI.

Each photo is compared to short text prompts ("a woman in a bikini" vs "a woman in a t-shirt").
Yes/no traits = how strongly the pictures lean to the first prompt; 1-10 scores = a small linear map
from those leanings, fitted on cards a vision model already graded (calib.json). Output = the same
verdict JSON the Gemini prompt returns, so swiper's applyVerdict needs no changes.

  python taste.py serve            # HTTP 127.0.0.1:8767, POST /judge {"b64"|"urls"|"files": [...]}, POST /learn {..., "verdict"}
  python taste.py calibrate        # refit calib.json from gallery + evalref grades
  python taste.py eval             # leave-one-out: how often our verdict = the stored verdict
"""
import base64, io, json, os, pickle, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import open_clip, torch
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
GALLERY = os.path.expanduser("~/swiper/gallery")
CALIB = os.path.join(HERE, "calib.json")
LEARN = os.path.join(HERE, "learn.pkl")   # [(photo embeddings float16, verdict)] from cards another vision model graded; no photos kept
REFIT_EVERY = 100
TOKEN = open(os.path.expanduser("~/.swiper/taste.token")).read().strip() if os.path.exists(os.path.expanduser("~/.swiper/taste.token")) else ""
MAX_PHOTOS = 6
torch.set_num_threads(2)

# trait -> (prompts that mean yes, prompts that mean no)
PROMPTS = {
    "woman": (["a photo of a woman", "a photo of a girl"], ["a photo of a man", "a photo of a guy"]),
    "bikini": (["a photo of a woman wearing a bikini", "a woman in a swimsuit at the beach"], ["a photo of a woman wearing a t-shirt", "a woman wearing a sweater", "a woman wearing a dress"]),
    "full_body": (["a full body photo of a woman standing, head to knees", "a woman's whole body from head to legs"], ["a close-up selfie of a woman's face", "a headshot of a woman"]),
    "pretty": (["a photo of a very beautiful woman with a pretty face", "a gorgeous attractive model"], ["a photo of a plain unattractive woman", "an average looking woman"]),
    "bust": (["a woman with large breasts", "a woman with a big bust and cleavage"], ["a woman with a flat chest", "a woman with small breasts"]),
    "butt": (["a woman with a big round butt", "a woman with wide hips and a big booty"], ["a woman with a flat butt", "a woman with narrow hips"]),
    "curvy": (["a curvy woman with an hourglass figure"], ["a woman with a straight boyish figure"]),
    "slim": (["a slim fit woman with a flat stomach", "an athletic toned woman"], ["an overweight woman", "a chubby woman with a belly"]),
    "plus": (["a plus-size obese woman", "a very fat woman"], ["a slim woman", "a woman of average weight"]),
    "sexy": (["a woman in a revealing sexy outfit showing skin", "a woman in lingerie", "a woman in a tight low-cut top"], ["a woman in modest baggy clothes", "a woman in a winter coat", "a woman in a loose sweatshirt"]),
    "dyed": (["a woman with dyed pink, blue, green or purple hair"], ["a woman with natural black, brown or blonde hair"]),
    "piercing": (["a woman with a nose ring and face piercings", "a woman with a septum piercing"], ["a woman with no piercings on her face"]),
    "alt": (["an emo goth alternative girl with heavy eyeliner", "an alt girl with tattoos and grunge style"], ["a woman in casual mainstream fashion"]),
    "gym": (["a mirror selfie at the gym in workout clothes", "a woman taking a mirror selfie of her body"], ["a photo of a woman at a restaurant", "a candid photo of a woman outdoors"]),
    "group": (["a group photo of several friends"], ["a photo of one person alone"]),
    "quality": (["a sharp, well lit, high quality photo"], ["a blurry, grainy, dark, low quality photo"]),
}
TRAITS = list(PROMPTS)
SCORES = ["face", "bust", "glutes", "curves", "sexy_vibe", "feminine", "photo_quality"]
# which leanings feed each 1-10 score, and its fallback map before calibration
SCORE_FROM = {"face": ["pretty", "quality"], "bust": ["bust", "sexy"], "glutes": ["butt", "curvy"], "curves": ["curvy", "butt", "bust"],
              "sexy_vibe": ["sexy", "bikini"], "feminine": ["woman", "pretty"], "photo_quality": ["quality"]}
# piercing/alt/gym lean high on almost every card (eval 10/23: in-shape, pierce-free women scored .7-.85; the first live hour called
# nearly every card a gym selfie and liked it for that), so they only count when extreme until fit() has real yes/no cards;
# "slim" reads ~0 even for fit women, so out-of-shape is judged from the plus-size leaning instead
BOOL_DEFAULT = {"bikini": .5, "dyed": .6, "piercing": .95, "alt": .98, "gym": .97, "group": .6, "full_body": .5, "plus": .8, "woman": .5}

ARCH, WEIGHTS = "ViT-B-32", "laion2b_s34b_b79k"
VISUAL, TEXT = os.path.join(HERE, "visual.pt"), os.path.join(HERE, "text.pt")
PROMPT_KEY = json.dumps(PROMPTS, sort_keys=True)


def prep():
    """one-off, service stopped: the full model (text + image halves, ~1.2 GB peak) -> image half only (visual.pt) + prompt vectors (text.pt)"""
    full, _, _ = open_clip.create_model_and_transforms(ARCH, pretrained=WEIGHTS)
    full.eval()
    tok = open_clip.get_tokenizer(ARCH)
    txt = {}
    with torch.no_grad():
        for t, (pos, neg) in PROMPTS.items():
            e = full.encode_text(tok(pos + neg)); e = e / e.norm(dim=-1, keepdim=True)
            txt[t] = (e[:len(pos)].mean(0), e[len(pos):].mean(0))
    torch.save(full.visual.state_dict(), VISUAL)
    torch.save({"key": PROMPT_KEY, "txt": txt}, TEXT)


def load_visual():
    """serve only the image half (~350 MB): the whole CLIP model pushed the 2 GB box (Chromium too) into swap"""
    if not os.path.exists(VISUAL) or torch.load(TEXT)["key"] != PROMPT_KEY:
        prep()   # first run, or the prompts changed
    c = open_clip.get_model_config(ARCH)
    vis = open_clip.model._build_vision_tower(c["embed_dim"], c["vision_cfg"])
    vis.load_state_dict(torch.load(VISUAL))
    vis.eval()
    pre = open_clip.image_transform(c["vision_cfg"]["image_size"], is_train=False)
    return vis, pre, torch.load(TEXT)["txt"]


visual, preprocess, TXT = load_visual()


def embed(imgs):
    with torch.no_grad():
        x = torch.stack([preprocess(im.convert("RGB")) for im in imgs])
        e = visual(x)
        return e / e.norm(dim=-1, keepdim=True)


def leanings(emb):
    """per photo, per trait: 0..1, how much the photo looks like the 'yes' prompts"""
    out = {}
    for t, (p, n) in TXT.items():
        s = torch.stack([emb @ (p / p.norm()), emb @ (n / n.norm())], dim=1) * 100
        out[t] = s.softmax(dim=1)[:, 0].numpy()
    return out


def card_features(L):
    """one vector per card: max and mean of each trait over the photos"""
    return np.array([f(L[t]) for t in TRAITS for f in (np.max, np.mean)])


def load_calib():
    try: return json.load(open(CALIB))
    except Exception: return {}


def verdict(L, calib):
    feats = card_features(L)
    mx = {t: float(L[t].max()) for t in TRAITS}
    # body photos = shots that are mostly full body; face/bust/butt read from those when there are any
    fb = [i for i, p in enumerate(L["full_body"]) if p >= .5]
    th = {**BOOL_DEFAULT, **calib.get("thresholds", {})}
    v = {}
    for s in SCORES:
        w = calib.get("scores", {}).get(s)
        if w: x = float(np.dot(w["w"], feats) + w["b"])
        else: x = 1 + 9 * float(np.mean([mx[t] for t in SCORE_FROM[s]]))
        v[s] = int(round(min(10, max(1, x))))
    v["is_woman"] = mx["woman"] >= th["woman"]
    v["swimwear"] = mx["bikini"] >= th["bikini"]
    v["full_body_visible"] = bool(fb)
    v["dyed_hair"] = float(np.mean(L["dyed"])) >= th["dyed"]
    v["facial_piercings"] = float(np.mean(L["piercing"])) >= th["piercing"]
    v["alt_style"] = float(np.mean(L["alt"])) >= th["alt"]
    # a like-trigger, and CLIP can't tell a gym body shot from any mirror pic: off until fit() learned a cutoff from graded cards
    v["gym_selfie"] = "gym" in calib.get("thresholds", {}) and mx["gym"] >= th["gym"]
    v["group_photo"] = float(L["group"][0]) >= th["group"]
    v["grainy"] = v["photo_quality"] <= 3
    plus = float(np.mean(L["plus"][fb] if fb else L["plus"]))
    v["in_shape"] = (plus < th["plus"]) if fb else None
    v["body"] = "plus" if plus >= th["plus"] else "curvy" if v["curves"] >= 7 else "athletic" if v["gym_selfie"] else "slim"
    v["body_confidence"] = 0.8 if fb else 0.3
    v["body_photos"] = [i + 1 for i in fb]
    v["notes"] = "local clip"
    return v


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=15).read()))


LOCK = threading.Lock()   # one CLIP pass at a time: two in parallel on the 2 GB box pushes Chromium into swap


def judge(imgs):
    t0 = time.time()
    with LOCK: L = leanings(embed(imgs[:MAX_PHOTOS]))
    v = verdict(L, load_calib())
    v["_model"] = "local/clip-b32"
    v["_timing"] = f"{len(imgs[:MAX_PHOTOS])} photos {time.time() - t0:.1f}s"
    return v


# ---------------------------------------------------------------- calibration from graded cards
def photos_in(d):
    """n.jpg files in photo order (skips stray dotfiles a Mac tar leaves behind)"""
    fs = [f for f in os.listdir(d) if f.split(".")[0].isdigit()]
    return [os.path.join(d, f) for f in sorted(fs, key=lambda f: int(f.split(".")[0]))]


def labeled():
    """(photo files, stored verdict) for every card some vision model graded"""
    out = []
    try: items = json.load(open(os.path.join(GALLERY, "items.json")))
    except Exception: items = {}
    for k, it in items.items():
        d = os.path.join(GALLERY, "img", k)
        # never learn from our own grades (the gallery stores whatever verdict liked the card, mostly ours since 10/23)
        if it.get("verdict") and not str(it["verdict"].get("_model", "")).startswith("local/") and os.path.isdir(d):
            out.append((photos_in(d), it["verdict"]))
    refs = os.path.join(HERE, "evalref", "refs.json")
    if os.path.exists(refs):
        for k, v in json.load(open(refs)).items():
            d = os.path.join(HERE, "evalref", "img", k)
            out.append((photos_in(d), v))
    return out


def num(x, dflt=5.0):
    try: return float(x)
    except (TypeError, ValueError): return dflt


def fit(rows):
    """ridge map features -> each score; thresholds that best match the stored yes/no traits"""
    X = np.array([card_features(L) for L, _ in rows])
    cal = {"scores": {}, "thresholds": {}, "n": len(rows)}
    for s in SCORES:
        y = np.array([num(v.get(s)) for _, v in rows])
        A = np.hstack([X, np.ones((len(X), 1))])
        w = np.linalg.solve(A.T @ A + 1.0 * np.eye(A.shape[1]), A.T @ y)
        cal["scores"][s] = {"w": w[:-1].tolist(), "b": float(w[-1])}
    pairs = {"bikini": (lambda v: v.get("swimwear") is True, "max"), "woman": (lambda v: v.get("is_woman") is True, "max"),
             "dyed": (lambda v: bool(v.get("dyed_hair")), "mean"), "piercing": (lambda v: bool(v.get("facial_piercings")), "mean"),
             "alt": (lambda v: bool(v.get("alt_style")), "mean"), "gym": (lambda v: bool(v.get("gym_selfie")), "max"),
             "plus": (lambda v: v.get("body") == "plus" or v.get("in_shape") is False, "mean")}
    for t, (label, agg) in pairs.items():
        ys = [(float(getattr(np, agg)(L[t])), label(v)) for L, v in rows]
        if sum(y for _, y in ys) < 5 or sum(not y for _, y in ys) < 5: continue   # need both kinds of card before moving the default
        best = max(np.arange(.3, .99, .02), key=lambda c: sum((p >= c) == y for p, y in ys))
        cal["thresholds"][t] = round(float(best), 2)
    return cal


def load_pkl(path, dflt):
    try: return pickle.load(open(path, "rb"))
    except Exception: return dflt


def cache_leanings():
    """every graded card as (leanings, verdict): gallery/evalref photos embedded once (embeds.pkl) + the learn log"""
    path, rows = os.path.join(HERE, "embeds.pkl"), []
    done = load_pkl(path, {})
    for files, v in labeled():
        if files[0] not in done: done[files[0]] = embed([Image.open(f) for f in files[:MAX_PHOTOS]]).numpy().astype(np.float16)
        rows.append((done[files[0]], v))
    pickle.dump(done, open(path, "wb"))
    rows += load_pkl(LEARN, [])
    return [(leanings(torch.from_numpy(e.astype(np.float32))), v) for e, v in rows]


def learn(imgs, v):
    """keep the embeddings + the other model's verdict; refit every REFIT_EVERY new cards"""
    with LOCK: e = embed(imgs[:MAX_PHOTOS]).numpy().astype(np.float16)
    log = load_pkl(LEARN, [])
    log.append((e, {k: v.get(k) for k in SCORES + ["is_woman", "swimwear", "dyed_hair", "facial_piercings", "alt_style", "gym_selfie", "in_shape", "body", "_model"]}))
    log = log[-5000:]
    pickle.dump(log, open(LEARN + ".tmp", "wb")); os.replace(LEARN + ".tmp", LEARN)
    if len(log) % REFIT_EVERY == 0:
        json.dump(fit(cache_leanings()), open(CALIB, "w"))
    return len(log)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._send(200, {"ok": True, "calibrated_on": load_calib().get("n", 0), "learned": len(load_pkl(LEARN, []))})

    def do_POST(self):
        if TOKEN and self.headers.get("X-Key") != TOKEN: return self._send(401, {"error": "bad key"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            imgs = ([Image.open(io.BytesIO(base64.b64decode(b))) for b in body.get("b64", [])[:MAX_PHOTOS]]   # the worker sends the photos it already fetched
                    + [fetch(u) for u in body.get("urls", [])[:MAX_PHOTOS]] + [Image.open(f) for f in body.get("files", [])[:MAX_PHOTOS]])
            if not imgs: return self._send(400, {"error": "no photos"})
            if self.path.endswith("/learn"):
                if not body.get("verdict") or body["verdict"].get("error"): return self._send(400, {"error": "no verdict"})
                return self._send(200, {"ok": True, "learned": learn(imgs, body["verdict"])})
            self._send(200, judge(imgs))
        except Exception as e:
            self._send(500, {"error": str(e)[:200]})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        ThreadingHTTPServer(("127.0.0.1", 8767), H).serve_forever()
    elif cmd == "calibrate":
        rows = cache_leanings()
        json.dump(fit(rows), open(CALIB, "w"))
        print("calibrated on", len(rows), "cards")
    elif cmd == "eval":
        rows = cache_leanings()
        out = []
        for i, (L, v) in enumerate(rows):   # leave-one-out: fit on the others, grade this one
            out.append({"ref": v, "ours": verdict(L, fit(rows[:i] + rows[i + 1:]))})
        json.dump(out, open(os.path.join(HERE, "eval_out.json"), "w"))
        print("wrote", len(out))
