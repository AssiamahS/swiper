# usage: mkproj.py <name>  -> with the "Create a new project" dialog open (or opens it), creates the project and a key in it
import re, subprocess, sys, time
sys.path.insert(0, "/Users/djsly/swiper/tools/gemini_keys")
import dia
name = sys.argv[1]; ws = dia.page(); E = lambda js: dia.ev(ws, js)
DLG = "[].slice.call(document.querySelectorAll('mat-dialog-container,[role=dialog]'))"
def open_create_project():
    if E(DLG + ".some(function(d){return /Create a new project/.test(d.innerText)})"): return
    if not E(DLG + ".some(function(d){return /Create a new key/.test(d.innerText)})"):
        E("(function(){var b=[].slice.call(document.querySelectorAll('button')).find(function(x){return /Create API key/.test(x.innerText)}); b&&b.click()})()"); time.sleep(3)
    E("document.querySelector('mat-dialog-container mat-select,[role=dialog] mat-select').click()"); time.sleep(2)
    E("(function(){var o=[].slice.call(document.querySelectorAll('mat-option,[role=option]')).find(function(x){return /Create project/.test(x.innerText)}); o&&o.click()})()"); time.sleep(3)
open_create_project()
E("(function(){var d=%s.find(function(d){return /Create a new project/.test(d.innerText)}); var i=d.querySelector('input'); i.focus(); i.value=%r; i.dispatchEvent(new Event('input',{bubbles:true}))})()" % (DLG, name)); time.sleep(1)
E("(function(){var d=%s.find(function(d){return /Create a new project/.test(d.innerText)}); var b=[].slice.call(d.querySelectorAll('button')).find(function(x){return /Create project/.test(x.innerText)}); b&&b.click()})()" % DLG)
for _ in range(20):
    time.sleep(3)
    if not E(DLG + ".some(function(d){return /Create a new project/.test(d.innerText)})"): break
still = E(DLG + ".map(function(d){return d.innerText.replace(/\\s+/g,' ').slice(0,160)})")
print("after create project:", still)
