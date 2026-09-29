# usage: shutdown.py <project-id> <authuser>  -> Cloud Console "Shut down" (30-day pending delete) for a project Claude created
import sys, time
sys.path.insert(0, "/Users/djsly/swiper/tools/gemini_keys")
import dia
pid, au = sys.argv[1], sys.argv[2]
ws = dia.page(); E = lambda js: dia.ev(ws, js)
dia.send(ws, "Page.navigate", {"url": f"https://console.cloud.google.com/iam-admin/settings?project={pid}&authuser={au}"}); time.sleep(14)
E("(function(){var b=[].slice.call(document.querySelectorAll('button')).find(function(x){return /^Shut down$/.test(x.innerText.trim())}); b&&b.click()})()"); time.sleep(3)
E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); var i=d&&d.querySelector('input'); if(!i) return; i.focus(); i.value=%r; i.dispatchEvent(new Event('input',{bubbles:true}))})()" % pid); time.sleep(2)
E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); var b=d&&[].slice.call(d.querySelectorAll('button')).find(function(x){return /shut down anyway|^shut down$/i.test(x.innerText.trim()) && !x.disabled}); b&&b.click()})()"); time.sleep(8)
t = E("document.body.innerText.replace(/\\s+/g,' ')") or ""
print(pid, "->", "SHUT DOWN" if ("pending deletion" in t.lower() or "scheduled for deletion" in t.lower() or "shut down" in t.lower() and "restore" in t.lower()) else "check: " + t[t.lower().find('shut'):][:160])
