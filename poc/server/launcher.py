"""Desktop launcher for the offline editor (source checkout and installed app).

* Binds a random loopback port, opens the browser, serves the UI.
* Only one copy runs per user: a second launch just opens the running editor.
* ``--check`` runs a self-test (UI files, fonts, FFmpeg with x264/AAC/libass, data
  folder) and prints a JSON report; the installer build runs it before packaging.
* In the installed app there is no console: output goes to ``<data>/logs/editor.log``
  and FFmpeg child processes are started without console windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from poc.resources import data_dir as default_data_dir
from poc.resources import fonts_dir, hide_child_consoles, is_frozen, ui_dist_dir

LOG_LIMIT_BYTES = 5 * 1024 * 1024


class InstanceLock:
    """Non-blocking, per-user single-instance lock released by the OS on exit."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.lock_path = self.folder / "instance.lock"
        self.info_path = self.folder / "instance.json"
        self._handle = None

    def acquire(self) -> bool:
        self.folder.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def publish(self, url: str) -> None:
        tmp = self.info_path.with_suffix(".json.partial")
        tmp.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, self.info_path)

    def running_url(self) -> str | None:
        try:
            url = json.loads(self.info_path.read_text(encoding="utf-8")).get("url")
        except (OSError, ValueError):
            return None
        if isinstance(url, str) and url.startswith(("http://127.0.0.1:", "http://localhost:")):
            return url
        return None

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self.info_path.unlink(missing_ok=True)
            if sys.platform == "win32":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None


def _redirect_output(folder: Path) -> Path:
    """The windowed app has no stdout/stderr; send them to a log file instead."""
    logs = folder / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "editor.log"
    if log_path.exists() and log_path.stat().st_size > LOG_LIMIT_BYTES:
        os.replace(log_path, logs / "editor.previous.log")
    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    return log_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def self_check(folder: Path) -> dict:
    """Check everything the editor needs at runtime. Never raises."""
    results: dict[str, dict] = {}

    def record(name: str, fn) -> None:
        try:
            detail = fn()
            results[name] = {"ok": True, "detail": detail}
        except Exception as exc:  # noqa: BLE001 - report every failure
            results[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def ui():
        index = ui_dist_dir() / "index.html"
        if not index.is_file():
            raise FileNotFoundError(f"{index} is missing (run: npm run build)")
        return str(index)

    def fonts():
        folder_ = fonts_dir()
        entries = json.loads((folder_ / "fonts.json").read_text(encoding="utf-8"))
        for entry in entries:
            path = folder_ / entry["file"]
            expected = entry.get("sha256")
            if not path.is_file():
                raise FileNotFoundError(str(path))
            if expected and _sha256(path) != expected:
                raise ValueError(f"checksum mismatch: {path.name}")
        return f"{len(entries)} fonts"

    def ffmpeg():
        from poc.ingest.normalize import FFMPEG_EXE
        filters = subprocess.run([FFMPEG_EXE, "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=60)
        if filters.returncode or " ass " not in filters.stdout:
            raise RuntimeError("FFmpeg has no libass 'ass' filter")
        probe = subprocess.run(
            [FFMPEG_EXE, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=0.3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
             "-c:v", "libx264", "-c:a", "aac", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60)
        if probe.returncode:
            raise RuntimeError(f"FFmpeg x264/AAC encode failed: {probe.stderr.strip()[:300]}")
        return FFMPEG_EXE

    def data():
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".write-test-", delete=True) as handle:
            handle.write(b"ok")
        return str(folder)

    def libraries():
        import av  # noqa: F401
        import cv2  # noqa: F401
        import librosa  # noqa: F401
        import numpy  # noqa: F401
        return "av, cv2, librosa, numpy"

    record("ui", ui)
    record("fonts", fonts)
    record("ffmpeg", ffmpeg)
    record("data_dir", data)
    record("libraries", libraries)
    return {"ok": all(item["ok"] for item in results.values()), "frozen": is_frozen(),
            "checks": results}


def _free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="OfflineVideoEditor")
    parser.add_argument("--check", action="store_true", help="run the self-test and exit")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: random)")
    parser.add_argument("--data-dir", type=Path, default=None, help="projects/cache folder")
    args = parser.parse_args(argv)

    folder = (args.data_dir or default_data_dir()).resolve()
    if is_frozen():
        hide_child_consoles()
        # librosa's numba JIT cache must not be written inside the program folder.
        os.environ.setdefault("NUMBA_CACHE_DIR", str(folder / "cache" / "numba"))
        if sys.stdout is None or sys.stderr is None or not args.check:
            _redirect_output(folder)

    if args.check:
        report = self_check(folder)
        text = json.dumps(report, indent=2)
        print(text, flush=True)
        if is_frozen():
            (folder / "logs").mkdir(parents=True, exist_ok=True)
            (folder / "logs" / "self_check.json").write_text(text, encoding="utf-8")
        return 0 if report["ok"] else 1

    lock = InstanceLock(folder)
    if not lock.acquire():
        url = lock.running_url()
        print(f"Offline video editor is already running: {url}", flush=True)
        if url and not args.no_browser:
            webbrowser.open(url)
        return 0

    import uvicorn
    from poc.server.app import create_app, validate_bind_host

    host = "127.0.0.1"
    validate_bind_host(host)
    port = args.port or _free_port(host)
    token = secrets.token_urlsafe(32)
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}")
    url = f"http://127.0.0.1:{port}/"
    app = create_app(data_dir=folder, token=token, allowed_hosts=allowed,
                     dev_mode=os.environ.get("POC_DEV_MODE") == "1")
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))

    def announce() -> None:
        for _ in range(600):  # wait up to 60 s for the server to accept connections
            if server.started or server.should_exit:
                break
            threading.Event().wait(0.1)
        if not server.started:
            return
        lock.publish(url)
        print(f"Offline video editor: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run()
    finally:
        lock.release()
    return 0
