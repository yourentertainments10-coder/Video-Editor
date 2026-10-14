"""Polish round: the page can tell when the running server is older than the page."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from poc.server.app import API_LEVEL, create_app

ROOT = Path(__file__).resolve().parents[1]


def test_health_reports_the_api_level(tmp_path):
    app = create_app(data_dir=tmp_path / "d", token="t", allowed_hosts=("testserver",))
    with TestClient(app) as c:
        body = c.get("/api/health", headers={"X-Editor-Token": "t"}).json()
    assert body == {"ok": True, "api_level": API_LEVEL}


def test_ui_and_server_api_levels_match():
    source = (ROOT / "ui" / "src" / "api.ts").read_text(encoding="utf-8")
    ui_level = int(re.search(r"export const API_LEVEL = (\d+);", source).group(1))
    assert ui_level == API_LEVEL, "bump API_LEVEL in both poc/server/app.py and ui/src/api.ts"


def test_every_ui_endpoint_exists_on_the_server(tmp_path):
    """Endpoints the page calls must be routes of this server (a 404 here means a missing route)."""
    app = create_app(data_dir=tmp_path / "d", token="t", allowed_hosts=("testserver",))
    routes = {getattr(r, "path", "") for r in app.routes}
    ui = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "src").rglob("*.ts*"))
    called = set(re.findall(r"`(/api/[^`]+)`", ui)) | set(re.findall(r"'(/api/[^']+)'", ui))
    normalised = {re.sub(r"\$\{[^}]+\}", "{x}", c).split("?")[0] for c in called}
    server = {re.sub(r"\{[^}]+\}", "{x}", r) for r in routes}
    missing = sorted(n for n in normalised if n not in server)
    assert not missing, missing
