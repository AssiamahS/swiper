import json, os, sys, time, urllib.request, websocket
CDP="http://127.0.0.1:9223"; TF=os.path.expanduser("~/.swiper-keys-worker-target")
def browser():
    v=json.load(urllib.request.urlopen(CDP+"/json/version")); return websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
def send(ws, method, params, i=[0]):
    i[0]+=1; ws.send(json.dumps({"id":i[0],"method":method,"params":params}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==i[0]: return m
def page():
    tid=open(TF).read().strip() if os.path.exists(TF) else None
    tabs=json.load(urllib.request.urlopen(CDP+"/json"))
    t=next((t for t in tabs if t.get("id")==tid),None)
    if not t:
        b=browser(); tid=send(b,"Target.createTarget",{"url":"about:blank","newWindow":True,"background":True})["result"]["targetId"]; open(TF,"w").write(tid); time.sleep(1)
        t=next(t for t in json.load(urllib.request.urlopen(CDP+"/json")) if t.get("id")==tid)
    return websocket.create_connection(t["webSocketDebuggerUrl"], suppress_origin=True, timeout=60)
def ev(ws, expr):
    r=send(ws,"Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":False}); return r.get("result",{}).get("result",{}).get("value")
if __name__=="__main__":
    ws=page(); cmd=sys.argv[1]
    if cmd=="nav": send(ws,"Page.navigate",{"url":sys.argv[2]}); time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else 6); print(ev(ws,"location.href+' | '+document.title"))
    elif cmd=="ev": print(json.dumps(ev(ws, sys.argv[2]))[:4000])
