"""A stand-in for a notebook server, for the tests of ``srim/app/notebooks.py``.

Run as ``fake_notebook_server.py serve PORT`` (answer HTTP 200 on 127.0.0.1 at PORT),
``... exit`` (print 30 lines and exit with status 1), ``... silent`` (never listen; sleep
until it is stopped) or ``echo TOKEN_FILE`` (print a URL with the token in it, as marimo does,
and exit with status 1).
"""

import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind looks up the host's name (socket.getfqdn), which can take
        # longer than the tests wait on a CI runner (macOS); the name is not needed here
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class _Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(argv: list[str]) -> int:
    mode = argv[0]
    if mode == "serve":
        _Server(("127.0.0.1", int(argv[1])), _Answer).serve_forever()
    elif mode == "exit":
        for number in range(1, 31):
            print(f"line {number}", flush=True)
        return 1
    elif mode == "silent":
        time.sleep(3600)
    elif mode == "echo":
        with open(argv[1], encoding="utf-8") as handle:
            token = handle.read().strip()
        print(f"URL: http://127.0.0.1:1/?access_token={token}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
