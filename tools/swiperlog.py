#!/usr/bin/python3
"""Read what the phone's swiper is doing, from the worker's live feed.

  swiperlog.py              last 100 lines (log lines + likes/nopes)
  swiperlog.py -n 500       last 500
  swiperlog.py --why        decisions only, with the verdict scores behind each
  swiperlog.py -f           follow (poll every 10s)
"""
import argparse, json, subprocess, time, urllib.request
from datetime import datetime

URL = "https://swiper-judge.sylvesterassiamahpm.workers.dev/live"
KEY = subprocess.run(["security", "find-generic-password", "-s", "swiper-judge-key", "-w"], capture_output=True, text=True).stdout.strip()
SCORES = ("face", "feminine", "body", "curves", "bust", "glutes", "sexy_vibe", "photo_quality", "full_body_visible", "swimwear", "_model")


def fetch(since=0, limit=100, kind=None):
    q = f"?since={since}&limit={limit}" + (f"&kind={kind}" if kind else "")
    req = urllib.request.Request(URL + q, headers={"X-Key": KEY, "User-Agent": "swiperlog"})
    return json.load(urllib.request.urlopen(req, timeout=20))["rows"]


def show(r, why):
    t = datetime.fromtimestamp(r["ts"] / 1000).strftime("%m-%d %H:%M:%S")
    tag = {"like": "LIKE", "nope": "NOPE"}.get(r["kind"], "    ")
    print(f"{t} {r['dev']:<6} {r['v']:<6} {tag} {r['msg']}")
    if why and r.get("data") and r["data"].get("verdict"):
        v = r["data"]["verdict"]
        print("        " + "  ".join(f"{k}={v[k]}" for k in SCORES if k in v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("-f", action="store_true")
    ap.add_argument("--why", action="store_true", help="likes/nopes only, with scores")
    a = ap.parse_args()
    rows = fetch(limit=a.n)
    if a.why: rows = [r for r in rows if r["kind"] in ("like", "nope")]
    for r in rows: show(r, a.why)
    last = max([r["id"] for r in rows] or [0])
    while a.f:
        time.sleep(10)
        for r in fetch(since=last, limit=500):
            last = r["id"]
            if not a.why or r["kind"] in ("like", "nope"): show(r, a.why)


if __name__ == "__main__":
    main()
