"""Tiny workload for Chaos-Pilot experiments.

Set HEALTHY=false to build a deliberately bad revision whose health check
fails, for the Bad Deployment scenario.
"""
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

HEALTHY = os.getenv("HEALTHY", "true").lower() == "true"
VERSION = os.getenv("APP_VERSION", "dev")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" and not HEALTHY:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"unhealthy\n")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"ok {VERSION}\n".encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 80), Handler).serve_forever()
