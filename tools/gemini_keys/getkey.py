# usage: getkey.py <suffix>  -> opens that key's details on the AI Studio keys page, appends the full key to keychain gemini-keys
import re, subprocess, sys, time
sys.path.insert(0, "/Users/djsly/swiper/tools/gemini_keys")
import dia
suf = sys.argv[1]; ws = dia.page(); E = lambda js: dia.ev(ws, js)
E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); if(!d) return; var c=[].slice.call(d.querySelectorAll('button')).find(function(x){return x.innerText.trim()==='close'}); c&&c.click()})()"); time.sleep(1)
E("(function(){var b=[].slice.call(document.querySelectorAll('button')).find(function(x){return x.innerText.trim()==='...%s'}); b&&b.click()})()" % suf); time.sleep(3)
t = E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); return d? d.innerText : ''})()") or ""
k = [x for x in re.findall(r"AQ\.[0-9A-Za-z_.-]{20,}|AIza[0-9A-Za-z_-]{30,}", t) if x.endswith(suf)]
if not k: print("key not found for", suf); sys.exit(1)
cur = subprocess.run(["security","find-generic-password","-s","gemini-keys","-w"],capture_output=True,text=True).stdout.strip()
keys = [x for x in cur.split(",") if x]
if k[0] not in keys: keys.append(k[0])
subprocess.run(["security","add-generic-password","-a","djsly","-s","gemini-keys","-w",",".join(keys),"-U"], check=True)
print(f"...{suf}: len {len(k[0])}, gemini-keys now {len(keys)}")
E("(function(){var d=document.querySelector('mat-dialog-container,[role=dialog]'); if(!d) return; var c=[].slice.call(d.querySelectorAll('button')).find(function(x){return x.innerText.trim()==='close'}); c&&c.click()})()")
