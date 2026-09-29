# usage: finishkey.py <keyname>  -> in the open "Create a new key" dialog (project preselected), create the key, then store it via the list
import re, subprocess, sys, time
sys.path.insert(0, "/Users/djsly/swiper/tools/gemini_keys")
import dia
name = sys.argv[1]; ws = dia.page(); E = lambda js: dia.ev(ws, js)
for _ in range(20):
    sel = E("(function(){var s=document.querySelector('mat-dialog-container mat-select,[role=dialog] mat-select'); return s? s.innerText.trim() : 'none'})()")
    if sel and sel not in ("Loading...", ""): break
    time.sleep(3)
print("project selected:", sel)
E("(function(){var i=document.querySelector('mat-dialog-container input,[role=dialog] input'); i.focus(); i.value=%r; i.dispatchEvent(new Event('input',{bubbles:true}))})()" % name); time.sleep(1)
E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); var b=[].slice.call(d.querySelectorAll('button')).find(function(x){return /Create key/.test(x.innerText)}); b&&b.click()})()"); time.sleep(8)
dia.send(ws, "Page.navigate", {"url": "https://aistudio.google.com/api-keys"}); time.sleep(10)
rows = E("[].slice.call(document.querySelectorAll('tr,[role=row]')).slice(1).map(function(r){return r.innerText.replace(/\\s+/g,' ').slice(0,90)})") or []
row = next((r for r in rows if (" %s " % name) in (" " + r + " ")), None)
print("row:", row)
if row:
    suf = row.split()[0].lstrip(".")
    print(subprocess.run([sys.executable, "/Users/djsly/swiper/tools/gemini_keys/getkey.py", suf], capture_output=True, text=True).stdout.strip())
