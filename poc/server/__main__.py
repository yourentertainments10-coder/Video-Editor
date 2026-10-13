"""Start the offline editor on a random loopback port."""
from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
import os
from pathlib import Path

import uvicorn

from poc.server.app import create_app, validate_bind_host


def main() -> None:
    host = "127.0.0.1"
    validate_bind_host(host)
    with socket.socket() as sock:
        sock.bind((host, 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    data_dir = Path.home() / "AppData" / "Local" / "OfflineVideoEditor"
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}")
    url = f"http://127.0.0.1:{port}/"
    print(f"Offline video editor: {url}", flush=True)
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(data_dir=data_dir, token=token, allowed_hosts=allowed,
                           dev_mode=os.environ.get("POC_DEV_MODE") == "1"),
                host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
