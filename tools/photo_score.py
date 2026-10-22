#!/usr/bin/env python3
"""Score your own Tinder photos the way Tinder's on-device RSRR model would (TheDonAyo teardown, 2026-10-20).

    python3 tools/photo_score.py                 # scores every image in ~/swiper/me/
    python3 tools/photo_score.py --dir ~/Pictures/tinder
    python3 tools/photo_score.py --tinder        # pulls the live profile photos from the Tinder tab in Dia (CDP :9223, must be logged in)

What the teardown found (mn.bin TFLite outputs rsrrMale/rsrrFemale/nudity/underage/face/fullBody; 800 synthetic photos):
  men: mirror selfie x4-x15, shirtless +123%, sunglasses +50% (8/8 up), outdoors up, heavier/older = no change, darker skin = lower.
Plus the public data (swipestats/photofeeler): solo front-facing lead with the face clear, 4-6 photos, smile (+47%),
one group shot max (not first), full-body second, no fish/kids/club, nothing older than ~6 months.

Judge = Gemini (keychain `gemini`), JSON mode. Prints a ranked table, a suggested order, and what to reshoot.
"""
import argparse, base64, json, os, subprocess, sys, urllib.request, pathlib, time

RUBRIC = """You are scoring a MAN's dating-app photos the way Tinder's internal right-swipe-received-ratio (RSRR) model and public
match-rate data would. Return ONLY JSON: {"photos":[{"i":<index>,"rsrr":<0-100 predicted right-swipe share>,"face_clear":<bool>,
"full_body":<bool>,"mirror_selfie":<bool>,"outdoors":<bool>,"sunglasses":<bool>,"shirtless":<bool>,"smile":<bool>,"group":<bool>,
"lighting":<0-10>,"sharpness":<0-10>,"style":<0-10>,"looks_recent":<bool>,"red_flags":[<short strings>],"fix":"<one concrete reshoot instruction>"}],
"order":[<indices best lead first>],"lead_reason":"<why that lead>","reshoot":[<3 specific photos to take next, with setting/outfit/time of day>]}
Weights: a clear, well-lit, front-facing face with a genuine smile is the lead. A full-body shot goes second. Mirror selfies and outdoor shots
score high in Tinder's model but a bathroom/gym shirtless mirror selfie reads as a red flag to women, so score it high on rsrr and name the risk.
Sunglasses help men in Tinder's model but never on the lead photo. Group photos: at most one, never first, he must be obvious. Penalise blur,
dim indoor light, fish, kids, club shots, hats hiding the face, and anything that looks older than 6 months."""

def key():
    try: return subprocess.check_output(['security', 'find-generic-password', '-s', 'gemini', '-w'], text=True).strip()
    except subprocess.CalledProcessError: sys.exit('no gemini key in the keychain (service `gemini`)')

def b64(path):
    data = pathlib.Path(path).read_bytes()
    mime = 'image/png' if data[:4] == b'\x89PNG' else 'image/webp' if data[8:12] == b'WEBP' else 'image/jpeg'
    return mime, base64.b64encode(data).decode()

def tinder_photos(out):
    """Pull the signed CDN urls of the logged-in profile from the Tinder tab in Dia and save them to `out`."""
    import websocket
    cdp = os.environ.get('SWIPER_CDP', 'http://127.0.0.1:9223')
    tabs = json.load(urllib.request.urlopen(cdp + '/json/list'))
    tab = next((t for t in tabs if 'tinder.com' in t['url'] and t.get('type') == 'page'), None)
    if not tab: sys.exit('no tinder.com tab in Dia: open https://tinder.com/app/profile and log in first')
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True)
    def js(expr, i=[0]):
        i[0] += 1; ws.send(json.dumps({'id': i[0], 'method': 'Runtime.evaluate', 'params': {'expression': expr, 'returnByValue': True, 'awaitPromise': True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == i[0]: return m.get('result', {}).get('result', {}).get('value')
    tok = js("(function(){for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(/apitoken/i.test(k)){var v=localStorage.getItem(k);try{v=JSON.parse(v)}catch(e){};if(typeof v==='string'&&v.length>10)return v}}return null})()")
    if not tok: sys.exit('Tinder tab is not logged in (no API token in localStorage)')
    prof = js("fetch('https://api.gotinder.com/v2/profile?include=user',{headers:{'X-Auth-Token':'%s','platform':'web'}}).then(r=>r.json()).then(j=>JSON.stringify(j))" % tok)
    u = json.loads(prof)['data']['user']
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, p in enumerate(u.get('photos', []), 1):
        url = p.get('url'); dst = out / f'tinder_{n:02d}.jpg'
        urllib.request.urlretrieve(url, dst); paths.append(dst)
    print(f'{u.get("name")} · bio: {u.get("bio")!r} · {len(paths)} photos saved to {out}')
    return paths

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--dir', default=os.path.expanduser('~/swiper/me')); ap.add_argument('--tinder', action='store_true')
    ap.add_argument('--model', default='gemini-3.5-flash-lite'); a = ap.parse_args()
    d = pathlib.Path(a.dir)
    paths = tinder_photos(d) if a.tinder else sorted(p for p in d.glob('*') if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.heic'))
    if not paths: sys.exit(f'no photos in {d}: drop your Tinder pictures there (AirDrop -> {d}) or run with --tinder')
    parts = [{'text': RUBRIC + f'\nThere are {len(paths)} photos, index 0..{len(paths)-1} in order.'}]
    for p in paths:
        if p.suffix.lower() == '.heic':
            jpg = p.with_suffix('.jpg'); subprocess.run(['sips', '-s', 'format', 'jpeg', str(p), '--out', str(jpg)], capture_output=True); p = jpg
        mime, data = b64(p); parts.append({'inline_data': {'mime_type': mime, 'data': data}})
    body = json.dumps({'contents': [{'parts': parts}], 'generationConfig': {'response_mime_type': 'application/json', 'temperature': 0.2}}).encode()
    req = urllib.request.Request(f'https://generativelanguage.googleapis.com/v1beta/models/{a.model}:generateContent?key={key()}', data=body, headers={'Content-Type': 'application/json'})
    for attempt in range(3):
        try: r = json.load(urllib.request.urlopen(req, timeout=120)); break
        except Exception as e:
            if attempt == 2: sys.exit(f'gemini failed: {e}')
            time.sleep(3 * (attempt + 1))
    txt = r['candidates'][0]['content']['parts'][0]['text']; j = json.loads(txt)
    print(f'\n{"#":>2}  {"rsrr":>4}  face full mirr out  sun shrt smil grp  light sharp style  file')
    for ph in sorted(j['photos'], key=lambda x: -x['rsrr']):
        i = ph['i']; f = lambda k: ' x ' if ph.get(k) else ' . '
        print(f'{i:>2}  {ph["rsrr"]:>4}  {f("face_clear")}{f("full_body")}{f("mirror_selfie")}{f("outdoors")}{f("sunglasses")}{f("shirtless")}{f("smile")}{f("group")}  {ph.get("lighting"):>5} {ph.get("sharpness"):>5} {ph.get("style"):>5}  {paths[i].name}')
        if ph.get('red_flags'): print(f'      flags: {", ".join(ph["red_flags"])}')
        print(f'      fix:   {ph.get("fix")}')
    print('\nORDER:', ' -> '.join(paths[i].name for i in j.get('order', [])), '\n  ', j.get('lead_reason'))
    print('\nRESHOOT:'); [print('  -', s) for s in j.get('reshoot', [])]
    (d / 'score.json').write_text(json.dumps(j, indent=1))

if __name__ == '__main__': main()
