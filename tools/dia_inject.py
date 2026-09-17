#!/usr/bin/env python3
"""Load swiper.js into the Tinder tab of the already-running Dia (CDP :9223).

    python3 tools/dia_inject.py          # navigate the Tinder tab to /app/recs and inject
    python3 tools/dia_inject.py --status # just report tab url + whether swiper is loaded
"""
import json, sys, time, urllib.request, websocket

CDP = "http://127.0.0.1:9223"
LOADER = "(function(){var u='https://raw.githubusercontent.com/AssiamahS/swiper/main/swiper.js?t='+Date.now();var x=new XMLHttpRequest();x.onreadystatechange=function(){if(x.readyState===4){if(x.status===200){try{(0,eval)(x.responseText);}catch(e){console.error('swiper',e)}}}};x.open('GET',u);x.send();})();"

tabs = json.load(urllib.request.urlopen(CDP + "/json/list"))
tab = next((t for t in tabs if "tinder.com" in t["url"] and t.get("type") == "page"), None)
if not tab:
    r = json.load(urllib.request.urlopen(urllib.request.Request(CDP + "/json/new?https://tinder.com/app/recs", method="PUT")))
    tab = r
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], suppress_origin=True)
_id = 0
def call(method, **params):
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id:
            return m.get("result", m)
def js(expr):
    r = call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
    return r.get("result", {}).get("value")

if "--status" not in sys.argv:
    if "/app/recs" not in tab["url"]:
        call("Page.navigate", url="https://tinder.com/app/recs")
        time.sleep(6)
    if "--local" in sys.argv:
        # push the working copy straight in (no GitHub cache): tear down any loaded instance first
        js("(function(){try{window.__swiper&&window.__swiper.stop()}catch(e){};var p=document.getElementById('swiper-panel');p&&p.remove();delete window.__swiper;})()")
        js(open(__import__('os').path.join(__import__('os').path.dirname(__file__), '..', 'swiper.js')).read())
        time.sleep(1)
    elif not js("!!window.__swiper"):
        js(LOADER)
        time.sleep(4)
if "--start" in sys.argv:
    import subprocess
    key = subprocess.run(["security", "find-generic-password", "-s", "gemini", "-w"], capture_output=True, text=True).stdout.strip()
    orkey = subprocess.run(["security", "find-generic-password", "-s", "openrouter", "-w"], capture_output=True, text=True).stdout.strip()
    js("""(function(){var c=__swiper.cfg; c.vision.provider='gemini'; c.vision.geminiKey=%s; c.vision.key=%s; c.vision.geminiModel='gemini-3.5-flash-lite, gemini-3.1-flash-lite'; c.vision.onFail='wait'; c.vision.proxy='http://127.0.0.1:8802/img?u='; c.vision.enabled=true;
      c.speed=1; c.likeRatio=0; c.vision.likeBodies='slim, athletic'; c.vision.likeMinQuality=7; c.vision.curvesAutoLike=7; c.vision.bustAutoLike=7; c.vision.sexyAutoLike=7; c.vision.unsure='nope'; c.vision.requireFullBody=true; c.vision.maxPhotos=6; c.photosToView=[2,3]; c.vision.minFace=6; c.vision.likeFace=8; c.vision.nopeDyedHair=true; c.filters.nopeWords='liberal, leftist, feminist, socialist, antifa, blm, communist, progressive'; c.maxPerSession=100000; c.maxPerDay=100000; c.breakEvery=[100000,100001]; c.hours.enabled=false;
      c.openProfileChance=0; c.photosToView=[1,2]; localStorage.setItem('swiper.cfg', JSON.stringify(c)); __swiper.start(); return true;})()""" % (json.dumps(key), json.dumps(orkey)))
    time.sleep(2)
url = js("location.href")
loaded = js("!!window.__swiper")
ver = js("window.__swiper && window.__swiper.version")
logged_in = js("!!localStorage.getItem('TinderWeb/APIToken')")
running = js("!!(window.__swiper && document.querySelector('#swiper-panel .sw-run.on'))")
status = js("(document.querySelector('#swiper-panel .sw-status')||{}).textContent")
print(json.dumps({"url": url, "swiper_loaded": loaded, "version": ver, "logged_in": logged_in, "running": running, "status": status}))
