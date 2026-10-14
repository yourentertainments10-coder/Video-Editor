"""U5: resource paths, data folder, launcher (single instance, self-check) and build files."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from poc import resources
from poc.server import launcher

ROOT = Path(__file__).resolve().parents[1]


# ---------------- resources ----------------

def test_resources_point_into_the_checkout_when_not_frozen():
    assert resources.is_frozen() is False
    assert resources.resource_root() == ROOT
    assert resources.ui_dist_dir() == ROOT / "ui" / "dist"
    assert resources.fonts_dir() == ROOT / "assets" / "fonts"
    assert (resources.fonts_dir() / "fonts.json").is_file()


def test_resources_point_into_the_bundle_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.is_frozen() is True
    assert resources.resource_root() == tmp_path.resolve()
    assert resources.ui_dist_dir() == tmp_path.resolve() / "ui" / "dist"
    assert resources.fonts_dir() == tmp_path.resolve() / "assets" / "fonts"


def test_server_and_text_use_the_resource_helpers():
    app_src = (ROOT / "poc" / "server" / "app.py").read_text(encoding="utf-8")
    text_src = (ROOT / "poc" / "render" / "text.py").read_text(encoding="utf-8")
    assert "ui_dist_dir()" in app_src and "parents[2]" not in app_src
    assert "fonts_dir()" in text_src and "parents[2]" not in text_src


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv(resources.DATA_DIR_ENV, str(tmp_path / "custom"))
    assert resources.data_dir() == (tmp_path / "custom").resolve()


def test_data_dir_on_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(resources.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert resources.data_dir() == (tmp_path / "OfflineVideoEditor").resolve()


def test_data_dir_elsewhere_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv(resources.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert resources.data_dir() == (tmp_path / "OfflineVideoEditor").resolve()


# ---------------- no console windows for ffmpeg on Windows ----------------

def _fake_popen():
    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
    return FakePopen


def test_child_processes_get_no_window_flag_on_windows():
    fake = _fake_popen()
    assert resources.hide_child_consoles(fake, platform="win32") is True
    assert fake(["ffmpeg"]).kwargs["creationflags"] == resources.CREATE_NO_WINDOW
    assert fake(["ffmpeg"], creationflags=0x10).kwargs["creationflags"] == 0x10   # explicit kept
    assert resources.hide_child_consoles(fake, platform="win32") is True          # idempotent
    assert fake(["ffmpeg"]).kwargs["creationflags"] == resources.CREATE_NO_WINDOW


def test_child_processes_untouched_elsewhere():
    fake = _fake_popen()
    assert resources.hide_child_consoles(fake, platform="linux") is False
    assert "creationflags" not in fake(["ffmpeg"]).kwargs


# ---------------- single instance ----------------

def test_second_instance_finds_the_running_one(tmp_path):
    first = launcher.InstanceLock(tmp_path)
    second = launcher.InstanceLock(tmp_path)
    assert first.acquire() is True
    try:
        first.publish("http://127.0.0.1:51234/")
        assert second.acquire() is False
        assert second.running_url() == "http://127.0.0.1:51234/"
    finally:
        first.release()
    assert not (tmp_path / "instance.json").exists()
    assert second.acquire() is True
    second.release()


def test_running_url_only_trusts_loopback(tmp_path):
    lock = launcher.InstanceLock(tmp_path)
    (tmp_path / "instance.json").write_text(json.dumps({"url": "http://evil.example/"}), encoding="utf-8")
    assert lock.running_url() is None
    (tmp_path / "instance.json").write_text("not json", encoding="utf-8")
    assert lock.running_url() is None


# ---------------- self-check ----------------

def test_self_check_reports_every_part(tmp_path):
    report = launcher.self_check(tmp_path / "data")
    checks = report["checks"]
    assert set(checks) == {"ui", "fonts", "ffmpeg", "data_dir", "libraries", "director", "footage", "effects"}
    for name in ("fonts", "ffmpeg", "data_dir", "libraries", "director", "footage", "effects"):
        assert checks[name]["ok"] is True, checks[name]
    assert checks["ui"]["ok"] is (ROOT / "ui" / "dist" / "index.html").is_file()
    assert report["ok"] is all(item["ok"] for item in checks.values())
    assert report["frozen"] is False


def test_self_check_detects_a_broken_font(monkeypatch, tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "fonts.json").write_text(json.dumps([{"file": "X.ttf", "family": "X", "sha256": "0" * 64}]),
                                      encoding="utf-8")
    (fonts / "X.ttf").write_bytes(b"not the right font")
    monkeypatch.setattr(launcher, "fonts_dir", lambda: fonts)
    report = launcher.self_check(tmp_path / "data")
    assert report["ok"] is False
    assert "checksum" in report["checks"]["fonts"]["detail"]


def test_check_command_prints_json_and_exit_code(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "poc.server", "--check", "--data-dir", str(tmp_path / "d")],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (0 if report["ok"] else 1)
    assert report["checks"]["ffmpeg"]["ok"] is True


# ---------------- build files ----------------

def test_pyinstaller_spec_bundles_ui_fonts_and_ffmpeg():
    spec = (ROOT / "packaging" / "OfflineVideoEditor.spec").read_text(encoding="utf-8")
    assert '"ui/dist"' in spec and '"assets/fonts"' in spec
    assert 'collect_data_files("imageio_ffmpeg"' in spec
    assert '"poc" / "director" / "lexicon.json"' in spec
    assert 'collect_data_files("cv2", subdir="data")' in spec
    assert "console=False" in spec
    assert (ROOT / "packaging" / "entry.py").is_file()
    assert (ROOT / "packaging" / "app.ico").is_file()


def test_installer_is_per_user_and_keeps_projects():
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert r"DefaultDirName={localappdata}\Programs\OfflineVideoEditor" in iss
    assert r"..\build\dist\OfflineVideoEditor\*" in iss
    # user data lives in %LOCALAPPDATA%\OfflineVideoEditor, never under {app}
    assert "OfflineVideoEditor\\projects" not in iss


def test_build_script_runs_tests_and_self_check_before_packaging():
    script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    order = [script.index(step) for step in
             ('"UI: tests"', '"Python tests"', '"Offline policy check"', '"PyInstaller"',
              '"--check"', '"Inno Setup"')]
    assert order == sorted(order)
