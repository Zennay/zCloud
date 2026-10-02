"""Harmless loopback-only fixture for future LightUp adapter tests.

This is not a vulnerable production service and intentionally binds only to
127.0.0.1. It exists so future assessment adapters can be integration-tested
without touching external systems.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "LightUpFixture/0.1"

    def do_GET(self):
        body = b'{"service":"lightup-fixture","ok":true}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-LightUp-Fixture", "true")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 18080), FixtureHandler)
    print("LightUp fixture listening on http://127.0.0.1:18080")
    server.serve_forever()


if __name__ == "__main__":
    main()
