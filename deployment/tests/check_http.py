#!/usr/bin/env python3
"""Check a running web image, including actual Trunk assets and CORS responses."""

import gzip
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def check(base):
    def request(path, method="GET", extra=None, expected=200):
        req = Request(urljoin(base.rstrip("/") + "/", path), method=method, headers={
            "Origin": "https://unrelated.example.invalid", **(extra or {}),
        })
        try:
            response = urlopen(req, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == expected, (path, response.status)
            assert response.headers.get_all("Access-Control-Allow-Origin") == ["*"], response.headers
            assert response.headers.get("Access-Control-Allow-Credentials") in (None, "false")
            return response.headers, response.read()

    headers, html = request("")
    assert "text/html" in headers["Content-Type"]
    assert b"VectorCraft" in html
    _, health = request("healthz")
    assert health == b"ok\n"
    request("", method="HEAD")
    for path in ("", "healthz", "missing.wasm"):
        headers, body = request(path, method="OPTIONS", extra={
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization, x-any-client",
        }, expected=204)
        assert not body
        assert "GET" in headers["Access-Control-Allow-Methods"]
        assert "*" in headers["Access-Control-Allow-Headers"]
        assert "authorization" in headers["Access-Control-Allow-Headers"].lower()
    request("missing.wasm", expected=404)
    headers, _ = request("index.html")
    assert "no-cache" in headers["Cache-Control"]
    text = html.decode()
    scripts = set(re.findall(r'''["']([^"']+\.js)["']''', text))
    wasm = set(re.findall(r'''["']([^"']+\.wasm)["']''', text))
    assert scripts and wasm, "index.html must reference the built JS and WASM"
    for path in scripts | wasm:
        headers, body = request(path, extra={"Accept-Encoding": "gzip"})
        assert headers.get("Content-Encoding") == "gzip", (path, "compression disabled")
        body = gzip.decompress(body)
        if path.endswith(".wasm"):
            assert headers["Content-Type"] == "application/wasm"
            assert body.startswith(b"\0asm"), "WASM request returned something else"
        else:
            assert "javascript" in headers["Content-Type"]
    print("PASS: web UI, JS/WASM, MIME types, compression, health, CORS, preflight and 404")


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/")
