#!/usr/bin/env python3
"""Tiny local image proxy for the Tinder lane running in a desktop browser.

Tinder's photo CDN (CloudFront) serves the signed .webp URLs without CORS headers, so the page
script cannot read the bytes to send them to the vision model. Chrome treats http://127.0.0.1 as a
secure origin, so the page fetches  http://127.0.0.1:8802/img?u=<encoded url>  instead and this
process fetches the photo and returns it with Access-Control-Allow-Origin: *.
Only gotinder.com / tinder.com hosts are allowed.
"""
import http.server, socketserver, urllib.parse, urllib.request, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8802
ALLOWED = ("gotinder.com", "tinder.com")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        u = (q.get("u") or [""])[0]
        host = urllib.parse.urlparse(u).hostname or ""
        if p.path != "/img" or not any(host.endswith(a) for a in ALLOWED):
            self.send_response(403); self._cors(); self.end_headers(); return
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tinder.com/"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(); ct = r.headers.get("Content-Type", "image/jpeg")
        except Exception as e:
            self.send_response(502); self._cors(); self.end_headers(); self.wfile.write(str(e).encode()[:200]); return
        self.send_response(200); self._cors()
        self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    S(("127.0.0.1", PORT), H).serve_forever()
