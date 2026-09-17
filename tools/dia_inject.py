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
    if not js("!!window.__swiper"):
        js(LOADER)
        time.sleep(4)
url = js("location.href")
loaded = js("!!window.__swiper")
ver = js("window.__swiper && window.__swiper.version")
logged_in = js("!!localStorage.getItem('TinderWeb/APIToken')")
print(json.dumps({"url": url, "swiper_loaded": loaded, "version": ver, "logged_in": logged_in}))
