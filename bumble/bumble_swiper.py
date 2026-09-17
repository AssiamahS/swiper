#!/usr/bin/env python3
"""Bumble auto-swiper for a real iPhone driven through the MobAI bridge.

The phone stays a normal phone: Bumble only ever sees real touches synthesized by
XCUITest on the device. This script (on the Mac) looks at each card through
MobAI's screenshot + UI tree, asks a vision model the same questions the Tinder
Swiper asks, and swipes with human pacing.

    python3 bumble_swiper.py --once      # judge the current card, do not swipe
    python3 bumble_swiper.py --dry       # full loop, decisions logged, no swipes
    python3 bumble_swiper.py             # run

Config: bumble/config.json (created with defaults on first run). State: bumble/state.json.
Log:    bumble/log.txt
"""
import argparse, base64, datetime as dt, hashlib, json, os, random, re, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOG_PATH = os.path.join(HERE, "log.txt")
MOBAI = "http://127.0.0.1:8686"
BUMBLE = "com.moxco.bumble"

DEFAULTS = {
    "device_id": "00008130-000C6CA63C9A001C",
    "speed": 3,                  # 1 fast .. 5 slow (seconds between cards, see SPEED)
    "like_ratio": 0.6,           # like probability when nothing else decides
    "max_per_session": 60,
    "max_per_day": 100,
    "break_every": [12, 30],     # cards between micro-breaks
    "break_len": [25, 110],      # seconds
    "sleep_len": [120, 240],     # minutes after the session cap
    "hours": {"enabled": False, "start": 18, "end": 23},
    "screens_per_card": [1, 3],  # how far down the profile to look (screenshots)
    "filters": {"max_distance": 0, "min_age": 0, "max_age": 0, "nope_words": [], "like_words": []},
    "vision": {
        "provider": "gemini",
        "gemini_model": "gemini-3.1-flash-lite",
        "reject_bodies": ["plus"],
        "min_body_conf": 0.5,
        "unsure": "ratio",       # like | nope | ratio
        "require_full_body": True,
        "min_quality": 5,
        "swimwear_auto_like": True,
        "curves_auto_like": 8,
        "like_bodies": [],          # e.g. ["slim","athletic"]: like when body is in this list and quality >= like_min_quality
        "like_min_quality": 7,
        "min_feminine": 6,
        "min_face": 6,
        "bust_auto_like": 7,
        "sexy_auto_like": 7,
        "like_face": 8,
        "nope_dyed_hair": True,
    },
}
SPEED = {0: (0.3, 0.8), 1: (1, 3), 2: (4, 9), 3: (6, 15), 4: (10, 25), 5: (20, 45)}

PROMPT = (
    "You are rating dating-app profile screenshots for a personal swipe filter. Look at ALL images "
    "(they are scrolled views of one profile; ignore app chrome, buttons and text boxes) and return ONLY a JSON object, no prose:\n"
    '{"body":"slim|athletic|average|curvy|plus","body_confidence":0-1,"full_body_visible":true|false,'
    '"swimwear":true|false,"curves":0-10,"photo_quality":0-10,"grainy":true|false,"group_photo":true|false,"is_woman":true|false,"feminine":0-10,"face":0-10,"dyed_hair":true|false,"bust":0-10,"sexy_vibe":0-10,"in_shape":true|false,"facial_piercings":true|false,"alt_style":true|false,"notes":"short"}\n'
    "bust: how large/prominent her chest is (0-10). sexy_vibe: how provocative, flirty or slutty the vibe is (tongue out, suggestive poses, revealing outfits, lingerie; 0 = wholesome, 10 = very provocative). "
    "face: how attractive the face and expression are for a dating profile (10 = objectively beautiful, cute or sexy expression like a sorority girl; 0 = unattractive or making ugly faces). dyed_hair: true for unnatural or split-dyed hair (pink, blue, green, purple, bright yellow, bright red, two-tone split dye); natural blonde, highlights, balayage and auburn = false. facial_piercings: true for nose rings, septum, lip, eyebrow or face piercings (ear piercings = false). alt_style: true for emo/goth/punk/alt styling, heavy dark makeup, chains, harnesses. in_shape: true if she looks fit or slim-to-average with a toned or curvy figure, false if overweight. "
    "Judge across ALL images, not just the first. full_body_visible: true only if at least one image shows her from head to at least mid-thigh. swimwear: true if ANY image shows a bikini, swimsuit or lingerie. "
    "body: overall body size of the profile owner using the clearest full-body photo (plus = visibly heavy/plus-size). "
    "curves: how pronounced hips/glutes/hourglass figure are. photo_quality: 10 = sharp, well lit, high-res; "
    "0 = blurry, grainy, dark, pixelated, heavy filters. grainy = true if most photos are low quality. "
    "group_photo = true if you cannot tell which person is the profile owner. "
    "is_woman: is the profile owner a woman (false for men, boys, or if you cannot tell). feminine: 0 = reads as a man/boy, 10 = unmistakably a woman."
)


# ----------------------------------------------------------------- utils
def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


def log(msg):
    line = f"{dt.datetime.now().strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def rnd(a, b):
    return a + random.random() * (b - a)


def skew(a, b):
    return a + (b - a) * random.random() ** 1.6


def keychain(service):
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    return r.stdout.strip()


# ----------------------------------------------------------------- MobAI bridge
class Phone:
    def __init__(self, device_id):
        self.id = device_id

    def dsl(self, steps, timeout=90):
        body = json.dumps({"version": "0.2", "alerts": "dismiss", "steps": steps}).encode()
        req = urllib.request.Request(f"{MOBAI}/api/v1/devices/{self.id}/dsl/execute", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        if not d.get("success"):
            raise RuntimeError(str(d.get("error") or d)[:300])
        return d

    def ensure_bridge(self):
        with urllib.request.urlopen(f"{MOBAI}/api/v1/devices", timeout=10) as r:
            devs = json.load(r)
        me = next((d for d in devs if d["id"] == self.id), None)
        if not me:
            raise RuntimeError("phone not connected to MobAI (USB unplugged?)")
        if not me.get("bridgeRunning"):
            req = urllib.request.Request(f"{MOBAI}/api/v1/devices/{self.id}/bridge/start", data=b"{}",
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=120).read()
            except Exception as e:
                raise RuntimeError(f"bridge not running and start failed: {e}")
            time.sleep(3)

    def observe(self, screenshot=True):
        d = self.dsl([{"action": "wait_for", "stable": True, "timeout_ms": 1200},
                      {"action": "observe", "include": ["ui_tree", "screenshot"] if screenshot else ["ui_tree"], "compact": True}])
        for s in d["step_results"]:
            if s["action"] == "observe":
                n = s["result"]["observations"]["native"]
                return n.get("ui_tree", ""), n.get("screenshot")
        raise RuntimeError("no observe result")

    def scroll_card(self):
        self.dsl([{"action": "scroll", "direction": "down", "amount": "page",
                   "predicate": {"accessibility_id": "bumble.grid_profile.content.scroll"}}])

    def swipe_card(self, like):
        # finger starts somewhere on the card, arcs to the edge; duration and path vary
        # long, mostly horizontal travel so Bumble reads it as a swipe, never as a scroll
        x0 = int(rnd(90, 140)) if like else int(rnd(290, 340)); y0 = int(rnd(380, 560))
        x1 = 428 if like else 2
        ym = int(y0 + rnd(-15, 15)); y1 = int(y0 + rnd(-35, 35))
        d1 = int(rnd(80, 150)); d2 = int(rnd(100, 200))
        self.dsl([{"action": "drag_path", "points": [
            {"x": x0, "y": y0, "duration_ms": int(rnd(0, 60))},
            {"x": int((x0 + x1) / 2), "y": ym, "duration_ms": d1},
            {"x": x1, "y": y1, "duration_ms": d2}]}])

    def open_bumble(self):
        self.dsl([{"action": "open_app", "bundle_id": BUMBLE},
                  {"action": "wait_for", "stable": True, "timeout_ms": 8000}])

    def tap_text(self, pattern):
        try:
            self.dsl([{"action": "tap", "predicate": {"text_regex": pattern}}])
            return True
        except Exception:
            return False


# ----------------------------------------------------------------- card parsing
def parse_card(tree):
    def grab(pat):
        m = re.search(pat, tree)
        return m.group(1).strip() if m else ""
    p = {"name": "", "age": 0, "bio": "", "height": "", "distance": None, "texts": []}
    na = grab(r'StaticText "([^"]+)" #bumble\.grid_profile\.name_label')
    m = re.match(r"(.+?),\s*(\d{2})$", na)
    if m:
        p["name"], p["age"] = m.group(1), int(m.group(2))
    else:
        p["name"] = na
    p["bio"] = grab(r'StaticText "([^"]*)" #bumble\.grid_profile\.about\.text')
    p["height"] = grab(r'Other "Height, ([^"]+)"')
    p["gender"] = grab(r'Other "Gender, ([^"]+)"')
    d = re.search(r'"(\d+)\s*(miles?|km)\s*away"', tree, re.I)
    if d:
        p["distance"] = int(d.group(1))
    p["texts"] = re.findall(r'(?:StaticText|Other) "([^"]{3,140})"', tree)
    return p


def screen_kind(tree):
    t = tree.lower()
    if "grid_profile.name_label" in tree:
        return "card"
    if "bumble.encounters.top_card" in tree:
        return "card_scrolled"
    if re.search(r"it.s a match|you matched|start the chat|say hello", t):
        return "match"
    if re.search(r"out of (swipes|likes)|swipe limit|no more swipes|used all your|daily limit|unlimited swipes", t):
        return "limit"
    if re.search(r"caught up|seen everyone|no one new|expand your|widen|come back later|check back", t):
        return "empty"
    if re.search(r"verify your (identity|account)|account (is )?(suspended|blocked|banned)|unusual activity|temporarily blocked", t):
        return "verify"
    if re.search(r"premium|boost|superswipe|spotlight|upgrade|subscribe|extend", t):
        return "upsell"
    return "other"


# ----------------------------------------------------------------- vision
def gemini_judge(cfg, shots, profile):
    key = keychain("gemini")
    if not key:
        raise RuntimeError("no gemini key in keychain (security add-generic-password -s gemini -a swiper -w KEY)")
    parts = [{"text": PROMPT + ("\nProfile text: " + (profile.get("bio") or "")[:300] if profile.get("bio") else "")}]
    for s in shots:
        with open(s, "rb") as f:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}})
    model = cfg["vision"]["gemini_model"]
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0, "maxOutputTokens": 800, "responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                                 data=body, headers={"Content-Type": "application/json"}, method="POST")
    d = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read()); break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 + attempt * 3); continue
            raise
    if "error" in d:
        raise RuntimeError("gemini: " + d["error"].get("message", "")[:160])
    text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("gemini: no json")
    return json.loads(m.group(0))


def apply_verdict(cfg, v):
    V = cfg["vision"]

    def num(x):
        try:
            return None if x in (None, "") else float(x)
        except (TypeError, ValueError):
            return None
    q, conf, fem, face = num(v.get("photo_quality")), num(v.get("body_confidence")), num(v.get("feminine")), num(v.get("face"))
    body = str(v.get("body", "")).lower()
    if v.get("is_woman") is False or (fem is not None and fem < V.get("min_feminine", 6)):
        return ("nope", "not a woman")
    if body in [b.lower() for b in V["reject_bodies"]] and (conf is None or conf >= V["min_body_conf"]):
        return ("nope", f"body {body}")
    if v.get("in_shape") is False:
        return ("nope", "out of shape")
    if V.get("nope_dyed_hair", True) and v.get("dyed_hair") is True:
        return ("nope", "dyed hair")
    if V.get("nope_piercings", True) and v.get("facial_piercings") is True:
        return ("nope", "face piercings")
    if V.get("nope_alt", True) and v.get("alt_style") is True:
        return ("nope", "alt style")
    if V["swimwear_auto_like"] and v.get("swimwear") is True:
        return ("like", f"bikini, {body}")
    if face is not None and face < V.get("min_face", 0):
        return ("nope", f"face {face}")
    if v.get("grainy") is True or (q is not None and q < V["min_quality"]):
        return ("nope", f"grainy/quality {q}")
    if V.get("require_full_body") and v.get("full_body_visible") is not True:
        return ("nope", "no full-body photo")
    feats = []
    if face is not None and face >= V.get("like_face", 11):
        feats.append(f"face {face}")
    for key, lim in (("curves", "curves_auto_like"), ("bust", "bust_auto_like"), ("sexy_vibe", "sexy_auto_like")):
        n = num(v.get(key))
        if n is not None and n >= V.get(lim, 11):
            feats.append(f"{key} {n}")
    if body in [b.lower() for b in V.get("like_bodies", [])] and (q is None or q >= V.get("like_min_quality", 7)):
        feats.append(body)
    if feats:
        return ("like", ", ".join(feats))
    return ("nope", f"nothing stands out (face {face})")


def text_decision(cfg, p):
    F = cfg["filters"]
    if F["max_distance"] and p["distance"] is not None and p["distance"] > F["max_distance"]:
        return ("nope", f"distance {p['distance']}")
    if F["min_age"] and p["age"] and p["age"] < F["min_age"]:
        return ("nope", f"age {p['age']}")
    if F["max_age"] and p["age"] and p["age"] > F["max_age"]:
        return ("nope", f"age {p['age']}")
    if p.get("gender") and p["gender"].lower() not in ("woman", "female", "women"):
        return ("nope", f"gender {p['gender']}")
    blob = " ".join(p["texts"] + [p["bio"]]).lower()
    for w in F["nope_words"]:
        if w.lower() in blob:
            return ("nope", f'word "{w}"')
    for w in F["like_words"]:
        if w.lower() in blob:
            return ("like", f'word "{w}"')
    return None


# ----------------------------------------------------------------- engine
def within_hours(cfg):
    h = cfg["hours"]
    if not h["enabled"]:
        return True
    now = dt.datetime.now().hour
    return h["start"] <= now < h["end"] if h["start"] <= h["end"] else (now >= h["start"] or now < h["end"])


def today():
    return dt.date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="judge the current card only, no swipe")
    ap.add_argument("--dry", action="store_true", help="loop but never swipe")
    ap.add_argument("--max", type=int, default=0, help="stop after N cards")
    a = ap.parse_args()

    cfg = merge(DEFAULTS, load(CFG_PATH, {}))
    save(CFG_PATH, cfg)
    state = load(STATE_PATH, {})
    if state.get("day") != today():
        state = {"day": today(), "likes": 0, "nopes": 0, "judged": 0, "matches": 0}
    phone = Phone(cfg["device_id"])
    phone.ensure_bridge()
    phone.open_bumble()

    if state.get("limit_until", 0) > time.time():
        until = state["limit_until"]; log(f"still out of likes; sleeping until {dt.datetime.fromtimestamp(until).strftime('%a %H:%M')}")
        while time.time() < until:
            time.sleep(min(300, until - time.time()))
        phone.open_bumble()
    session = 0; since_break = 0; next_break = random.randint(*cfg["break_every"])
    last_key = ""; last_at = 0; last_decision = None; stuck = 0; other = 0; scrolled = 0
    log(f"start speed={cfg['speed']} ratio={cfg['like_ratio']} vision={cfg['vision']['provider']} dry={a.dry} once={a.once}")

    while True:
        if a.max and session >= a.max:
            log(f"--max {a.max} reached"); break
        if not within_hours(cfg):
            time.sleep(60); continue
        if state["likes"] + state["nopes"] >= cfg["max_per_day"]:
            log("daily cap reached, stopping"); break
        if since_break >= next_break:
            b = rnd(*cfg["break_len"]); log(f"micro-break {b:.0f}s"); time.sleep(b)
            since_break = 0; next_break = random.randint(*cfg["break_every"])
        if session and session % cfg["max_per_session"] == 0:
            z = rnd(*cfg["sleep_len"]); log(f"session cap, sleeping {z:.0f} min"); time.sleep(z * 60)

        try:
            tree, shot = phone.observe()
        except Exception as e:
            log(f"observe failed: {e}"); time.sleep(5); phone.ensure_bridge(); continue
        kind = screen_kind(tree)
        if kind == "match":
            state["matches"] += 1; save(STATE_PATH, state); log("MATCH (Bumble: she messages first, closing)")
            phone.tap_text(r"(?i)keep swiping|continue|not now|close|later|got it|okay|ok") or phone.dsl([{"action": "tap", "coords": {"x": 215, "y": 900}}])
            time.sleep(rnd(1.5, 3)); continue
        if kind in ("limit", "empty", "verify", "upsell", "other"):
            texts = re.findall(r'(?:StaticText|Button) "([^"]{2,90})"', tree)[:12]
            log(f"screen={kind} shot={shot} text={texts}")
        if kind == "limit":
            # Bumble's budget refills 24h after the moment you hit the wall (rolling), so sleep until then
            until = time.time() + 24 * 3600 + 120
            state["limit_until"] = until; save(STATE_PATH, state)
            phone.tap_text(r"(?i)no thanks|not now|maybe later|close|dismiss|got it")
            log(f"out of likes; sleeping until {dt.datetime.fromtimestamp(until).strftime('%a %H:%M')}")
            while time.time() < until:
                time.sleep(min(300, until - time.time()))
            phone.open_bumble(); continue
        if kind == "empty":
            log("no more people nearby (widen distance in Filters); checking again in 30 min"); time.sleep(1800); phone.open_bumble(); continue
        if kind == "verify":
            log("verification/block dialog, stopping"); break
        if kind == "upsell":
            log("upsell dialog, closing"); phone.tap_text(r"(?i)no thanks|not now|maybe later|close|skip|dismiss|continue"); time.sleep(rnd(1, 2.5)); continue
        if kind == "card_scrolled":
            scrolled += 1
            if scrolled > 2:
                # a card with no name after scrolling back up = an ad or a promo card: pass on it
                log("ad/promo card, passing"); phone.swipe_card(False); scrolled = 0; time.sleep(1.5); continue
            try:
                phone.dsl([{"action": "scroll", "direction": "up", "amount": "full",
                            "predicate": {"accessibility_id": "bumble.grid_profile.content.scroll"}}])
            except Exception:
                phone.dsl([{"action": "swipe", "direction": "down", "distance": "full"}])
            time.sleep(1); continue
        scrolled = 0
        if kind != "card":
            app = re.search(r'Application "([^"]+)"', tree)
            app = app.group(1) if app else ""
            if app and app != "Bumble":
                # you are using the phone: stay out of the way, come back after 5 quiet minutes
                other += 1
                if other == 1: log(f"phone in use ({app}), waiting")
                if other >= 10: log("5 min in another app, reopening Bumble"); phone.open_bumble(); other = 0
                time.sleep(30); continue
            other += 1
            if other >= 3:
                log("not on a card, reopening Bumble"); phone.open_bumble(); other = 0
            time.sleep(2); continue
        other = 0

        p = parse_card(tree)
        key = hashlib.md5(f"{p['name']}|{p['age']}|{p['bio']}".encode()).hexdigest()[:10]
        if key == last_key:
            if last_decision and time.time() - last_at > 8 and stuck < 2 and not (a.dry or a.once):
                stuck += 1; last_at = time.time(); log(f"card did not advance, retrying {last_decision}")
                phone.swipe_card(last_decision == "like"); time.sleep(2)
            elif stuck >= 2:
                log("card stuck after retries, stopping"); break
            else:
                time.sleep(2)
            continue
        last_key = key; last_at = time.time(); last_decision = None; stuck = 0

        dec = text_decision(cfg, p)
        shots = [shot]
        if not dec:
            for _ in range(random.randint(*cfg["screens_per_card"]) - 1):
                time.sleep(rnd(0.7, 2.0))
                try:
                    phone.scroll_card(); _, s2 = phone.observe(); shots.append(s2)
                except Exception as e:
                    log(f"scroll failed: {e}"); break
            try:
                v = gemini_judge(cfg, shots, p)
                state["judged"] += 1
                log(f"{p['name']} {p['age']} -> " + json.dumps({k: v.get(k) for k in ("body", "body_confidence", "photo_quality", "swimwear", "curves", "face", "bust", "sexy_vibe", "dyed_hair", "feminine")}))
                dec = apply_verdict(cfg, v)
            except Exception as e:
                log(f"vision failed ({e}), using ratio")
        if not dec:
            dec = ("like" if random.random() < cfg["like_ratio"] else "nope", "ratio")

        lo, hi = SPEED.get(cfg["speed"], SPEED[3])
        time.sleep(skew(lo, hi))
        if a.once:
            log(f"[once] would {dec[0].upper()} {p['name']} ({dec[1]})"); break
        if a.dry:
            log(f"[dry] would {dec[0].upper()} {p['name']} ({dec[1]})"); time.sleep(3); continue
        phone.swipe_card(dec[0] == "like")
        last_decision = dec[0]; last_at = time.time()
        state["likes" if dec[0] == "like" else "nopes"] += 1; save(STATE_PATH, state)
        session += 1; since_break += 1
        log(f"{dec[0].upper()} {p['name']} {p['age']} ({dec[1]})  today {state['likes']}L/{state['nopes']}N")
        time.sleep(rnd(1.0, 1.8))
        try:
            t2, _ = phone.observe(screenshot=False)
            if screen_kind(t2) == "card" and parse_card(t2)["name"] == p["name"] and p["name"]:
                log("  deck did not advance yet")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped by user")
