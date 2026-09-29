#!/usr/bin/python3
"""iMessage the user when the swiper lane stalls (launchd com.sly.swiper-alert, every 5 min).
Reads the worker's /health heartbeat: lastAt = the page last asked the brain, lastOkAt = a brain last answered."""
import json, os, subprocess, time, urllib.request

URL = "https://swiper-judge.sylvesterassiamahpm.workers.dev/health"
TO = "sly.assiamah@icloud.com"
STATE = os.path.expanduser("~/.swiper-alert.json")
STALL = 15 * 60


def key():
    return subprocess.run(["security", "find-generic-password", "-s", "swiper-judge-key", "-w"], capture_output=True, text=True).stdout.strip()


def imessage(text):
    script = 'on run argv\ntell application "Messages" to send (item 1 of argv) to participant "%s" of (1st account whose service type = iMessage)\nend run' % TO
    r = subprocess.run(["osascript", "-e", script, text], capture_output=True, text=True)
    print(time.strftime("%H:%M:%S"), "sent" if r.returncode == 0 else "SEND FAILED " + r.stderr.strip(), "|", text, flush=True)


def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    req = urllib.request.Request(URL, headers={"X-Key": key(), "User-Agent": "swiper-alert"})
    h = json.load(urllib.request.urlopen(req, timeout=20))
    now = h.get("now", time.time() * 1000) / 1000
    last, ok = (h.get("lastAt") or 0) / 1000, (h.get("lastOkAt") or 0) / 1000
    if now - last > STALL:
        problem = "swiper stopped at %s: no cards for %d min. iPad asleep or the Tinder tab reloaded. Open Tinder in Safari, Share, run Swiper, Start." % (time.strftime("%-I:%M %p", time.localtime(last)), (now - last) // 60)
    elif now - ok > STALL:
        problem = "swiper is holding: every vision brain failed for %d min (Google quota or overload). It resumes by itself; add a Gmail if this repeats." % ((now - ok) // 60)
    else:
        problem = None
    if problem and st.get("alerted") != problem.split(":")[0]:
        imessage(problem); st["alerted"] = problem.split(":")[0]
    elif not problem and st.get("alerted"):
        imessage("swiper is running again."); st["alerted"] = None
    json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    main()
