#!/usr/bin/env python3
"""
Serve KeriWasm via hio's HTTP server with COOP/COEP/CORS headers.
"""

import mimetypes
import os
import time

import falcon

from hio.base import tyming
from hio.core import http
from hio.core.http import serving


class HeaderMiddleware:
    """Add COOP/COEP/CORS headers to every response."""

    def process_response(self, req, resp, resource, req_succeeded):
        resp.set_header("Cross-Origin-Opener-Policy", "same-origin")
        resp.set_header("Cross-Origin-Embedder-Policy", "require-corp")
        resp.set_header("Access-Control-Allow-Origin", "*")


def run(host="", port=8000):
    """
    Run a hio-based static server for KeriWasm.
    """
    mimetypes.add_type("application/wasm", ".wasm")
    mimetypes.add_type("application/toml", ".toml")

    tymist = tyming.Tymist(tyme=0.0)
    app = falcon.App(middleware=[HeaderMiddleware()])

    static_dir = os.path.dirname(os.path.abspath(__file__))
    sink = serving.StaticSink(staticDirPath=static_dir)
    # Serve from the repo root so /static/* maps to ./static/*
    sink.StaticSinkBasePath = "/"
    app.add_sink(sink, prefix=sink.DefaultStaticSinkBasePath)

    server = http.Server(
        name="keriwasm",
        host=host,
        port=port,
        tymeout=0.5,
        app=app,
        tymth=tymist.tymen(),
    )
    server.reopen()

    try:
        while True:
            server.service()
            time.sleep(0.0625)
            tymist.tick(tock=0.0625)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    run()
