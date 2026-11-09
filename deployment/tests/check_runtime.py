#!/usr/bin/env python3
"""Check the shipped nginx image with synthetic transport fixtures, without Rust downloads.

This checks HTTP serving only. Use check_http.py against the complete image to
verify that the actual built web assets are served.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from check_http import check


def run(image):
    container = None
    with tempfile.TemporaryDirectory(prefix="vectorcraft-http-") as temp:
        root = Path(temp)
        # The fixtures exercise MIME types/compression, not application behavior.
        (root / "index.html").write_text(
            '<!DOCTYPE html><title>VectorCraft HTTP fixture</title>'
            '<link rel="modulepreload" href="./app.js">'
            '<link rel="preload" href="./app.wasm" as="fetch">'
        )
        (root / "app.js").write_bytes(b" " * 4096)
        # Valid WASM header and a custom section containing 2048 zero bytes.
        (root / "app.wasm").write_bytes(b"\0asm\x01\0\0\0\x00\x81\x10\x00" + b"\0" * 2048)
        # The container runs as UID 101; tempfile's default mode is owner-only.
        root.chmod(0o755)
        try:
            container = subprocess.check_output([
                "docker", "run", "--rm", "--detach", "--read-only",
                "--name", "vectorcraft-http-" + str(os.getpid()),
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,noexec,size=16m,mode=1777",
                "--publish", "127.0.0.1::8080",
                "--mount", "type=bind,source=" + str(root) + ",target=/usr/share/nginx/html,readonly",
                image,
            ], text=True).strip()
            address = subprocess.check_output([
                "docker", "port", container, "8080/tcp",
            ], text=True).strip()
            url = "http://" + address + "/"
            for _ in range(50):
                try:
                    with urlopen(url + "healthz", timeout=1):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            check(url)
            print("Runtime fixture checks passed; application WASM build is a separate check.")
        finally:
            if container:
                subprocess.run(["docker", "stop", "--time", "5", container],
                               check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "triton/vectorcraft:web-server-check")
