# PyInstaller spec for the Offline Video Editor (one-folder build).
# Build with packaging/build_windows.ps1, not by hand.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
UI_DIST = ROOT / "ui" / "dist"
FONTS = ROOT / "assets" / "fonts"
if not (UI_DIST / "index.html").is_file():
    raise SystemExit("ui/dist is missing: run 'npm run build' in ui/ first")

datas = [
    (str(UI_DIST), "ui/dist"),
    (str(FONTS), "assets/fonts"),
    (str(ROOT / "poc" / "director" / "lexicon.json"), "poc/director"),   # Local Director data
]
datas += collect_data_files("imageio_ffmpeg", include_py_files=False)  # bundled ffmpeg binary
datas += collect_data_files("librosa")

hiddenimports = (
    collect_submodules("poc")
    + collect_submodules("uvicorn")
    + ["soundfile", "scipy.signal", "sklearn.utils._typedefs"]
)

icon = ROOT / "packaging" / "app.ico"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OfflineVideoEditor",
    console=False,                # windowed: logs go to %LOCALAPPDATA%\OfflineVideoEditor\logs
    icon=str(icon) if sys.platform == "win32" and icon.is_file() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="OfflineVideoEditor", upx=False)
