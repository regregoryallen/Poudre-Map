"""Local static server for web/ — with HTTP range support.

Python's stock http.server answers every request with 200 and the whole file.
PMTiles reads the archive through Range requests and needs 206 Partial
Content, so the stock server silently breaks the map locally even though the
exact same files work fine behind Caddy in production.

    python src/devserve.py            # → http://localhost:8137
    python src/devserve.py --port 9000 --mount /poudremap

--mount serves the site under a subpath, which is worth using before deploying:
it exercises the same relative-URL resolution the real deployment uses.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler plus single-range support."""

    def send_head(self):  # noqa: C901 — mirrors the stdlib method's shape
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        size = os.fstat(f.fileno()).st_size
        m = RANGE_RE.match(rng.strip())
        if not m:
            f.close()
            self.send_error(400, "Malformed Range header")
            return None

        start_s, end_s = m.groups()
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:
            # Suffix form: bytes=-N means the last N bytes.
            if not end_s:
                f.close()
                self.send_error(400, "Malformed Range header")
                return None
            start = max(0, size - int(end_s))
            end = size - 1

        if start >= size:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        end = min(end, size - 1)

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        f.seek(start)
        self._remaining = end - start + 1
        return _Bounded(f, self._remaining)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


class _Bounded:
    """File wrapper that stops after n bytes, for copyfile()."""

    def __init__(self, fh, n: int):
        self._fh = fh
        self._n = n

    def read(self, size=-1):
        if self._n <= 0:
            return b""
        if size < 0 or size > self._n:
            size = self._n
        data = self._fh.read(size)
        self._n -= len(data)
        return data

    def close(self):
        self._fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--mount", default="/", help="subpath to serve under, "
                                                 "e.g. /poudremap")
    args = ap.parse_args()

    if not (WEB / "poudre.pmtiles").exists():
        print("web/poudre.pmtiles missing — run src/tiles.py first")
        return 1

    mount = "/" + args.mount.strip("/")
    if mount != "/":
        # Serve web/ under the mount path by handing the handler a directory
        # tree that mirrors it.
        class Mounted(RangeHandler):
            def translate_path(self, path):
                if path.startswith(mount):
                    path = path[len(mount):] or "/"
                elif path == "/":
                    return str(WEB)
                return super().translate_path(path)

        handler = functools.partial(Mounted, directory=str(WEB))
        url = f"http://localhost:{args.port}{mount}/"
    else:
        handler = functools.partial(RangeHandler, directory=str(WEB))
        url = f"http://localhost:{args.port}/"

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"serving {WEB.relative_to(ROOT)} at {url}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
