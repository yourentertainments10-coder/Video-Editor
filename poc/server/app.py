"""Secure loopback API and static UI for the local video editor."""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from poc.ingest.normalize import normalize_asset
from poc.ingest.proxy import make_proxy, make_thumbnails
from poc.project_assets import AssetImportError, ProjectAssetLibrary
from poc.render.editor import EditorRenderer, RenderCancelled
from poc.render.export import EXPORT_PROFILES, get_profile
from poc.timeline import EditorSettings, EditorTimeline, Layer, MediaAsset, TimeBase, Track
from poc.timeline.validate import validate_editor_timeline


def validate_bind_host(host: str) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The editor server may bind only to 127.0.0.1 or localhost")


def _project_root(data_dir: Path, project_id: str) -> Path:
    if not re.fullmatch(r"p_[0-9a-f]{8}", project_id):
        raise HTTPException(404, "project not found")
    root = (data_dir / "projects" / project_id).resolve()
    if not root.is_dir() or not (root / "project.json").is_file():
        raise HTTPException(404, "project not found")
    return root


def _library(root: Path) -> ProjectAssetLibrary:
    return ProjectAssetLibrary.load(root)


def _asset_info(item, cache_dir: Path) -> dict[str, Any]:
    media = item.media_asset
    try:
        normalized = normalize_asset(media)
        width, height = normalized.width, normalized.height
        fps = normalized.fps
        has_audio = normalized.has_audio
        rotation = normalized.rotation_degrees
        hdr = normalized.is_hdr
    except Exception:
        width = item.metadata.get("width")
        height = item.metadata.get("height")
        fps = item.metadata.get("fps")
        has_audio = bool(item.metadata.get("has_audio", False))
        rotation = 0
        hdr = False
    if width and height:
        thumbnail_count = 10
    else:
        thumbnail_count = 0
    return {
        "asset_id": item.asset_id, "kind": item.kind,
        "file_name": Path(item.source_path).name, "duration_ms": item.duration_ms,
        "width": width, "height": height, "fps": fps, "has_audio": has_audio,
        "rotation": rotation, "hdr": hdr, "thumbnail_count": thumbnail_count,
    }


def create_app(*, data_dir: Path, token: str, allowed_hosts: tuple[str, ...],
               dev_mode: bool = False) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    ui_dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    app = FastAPI(title="Offline Video Editor", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.data_dir = data_dir
    app.state.jobs: dict[str, dict[str, Any]] = {}
    app.state.job_lock = threading.RLock()
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editor-render")

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in {value.lower() for value in allowed_hosts}:
            return Response("Forbidden host", status_code=403)
        origin = request.headers.get("origin")
        dev_preflight = (
            dev_mode and request.method == "OPTIONS"
            and origin == "http://localhost:5173"
        )
        if (request.url.path.startswith("/api/") and not dev_preflight
                and request.headers.get("x-editor-token") != token):
            return Response("Unauthorized", status_code=401)
        if dev_preflight:
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers": "X-Editor-Token, Range, Content-Type",
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            })
        response = await call_next(request)
        if dev_mode and origin == "http://localhost:5173":
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "X-Editor-Token, Range, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        return response

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/projects", status_code=201)
    def create_project(body: dict[str, Any]):
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(422, "name must be a non-empty string")
        projects = data_dir / "projects"
        projects.mkdir(parents=True, exist_ok=True)
        project_id = f"p_{uuid.uuid4().hex[:8]}"
        root = projects / project_id
        root.mkdir()
        library = ProjectAssetLibrary.create(root, project_id=project_id)
        timeline = EditorTimeline(
            project_id=project_id, timeline_id=f"t_{uuid.uuid4().hex[:8]}",
            duration_ticks=1000, time_base=TimeBase(ticks_per_second=1000),
            settings=EditorSettings(fps_num=30, fps_den=1, width=1920, height=1080),
            layers=[Layer(layer_id="visual", name="Visual", order=0),
                    Layer(layer_id="audio", name="Audio", order=1)],
            tracks=[Track(track_id="video", kind="video", layer_id="visual", name="Video"),
                    Track(track_id="overlay", kind="overlay", layer_id="visual", name="Overlay"),
                    Track(track_id="voice", kind="audio", layer_id="audio", name="Voice"),
                    Track(track_id="music", kind="music", layer_id="audio", name="Music")],
        )
        timeline.save(root / "timeline.json")
        (root / "meta.json").write_text(json.dumps({"name": name.strip()}, ensure_ascii=False), encoding="utf-8")
        (root / "exports").mkdir()
        return {"project_id": project_id, "name": name.strip()}

    @app.get("/api/projects")
    def list_projects():
        result = []
        for root in sorted((data_dir / "projects").glob("p_*")):
            if not (root / "project.json").is_file():
                continue
            try:
                name = json.loads((root / "meta.json").read_text(encoding="utf-8"))["name"]
            except Exception:
                name = root.name
            result.append({"project_id": root.name, "name": name})
        return {"projects": result}

    @app.post("/api/projects/{pid}/assets")
    def import_assets(pid: str, body: dict[str, Any]):
        root = _project_root(data_dir, pid)
        paths = body.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise HTTPException(422, "paths must be a list of local paths")
        sources = []
        for value in paths:
            if "://" in value:
                raise HTTPException(422, "network paths are not allowed")
            source = Path(value).expanduser()
            if not source.is_file():
                raise HTTPException(422, f"media file does not exist: {value}")
            sources.append(source.resolve())
        library = _library(root)
        original = library.project
        try:
            for source in sources:
                library.import_asset(source)
        except (AssetImportError, OSError, ValueError) as exc:
            library.project = original
            library.save()
            raise HTTPException(422, str(exc)) from exc
        cache = data_dir / "cache"
        return {"assets": [_asset_info(item, cache) for item in library.project.assets]}

    @app.get("/api/projects/{pid}/assets")
    def list_assets(pid: str):
        library = _library(_project_root(data_dir, pid))
        cache = data_dir / "cache"
        return {"assets": [_asset_info(item, cache) for item in library.project.assets]}

    @app.get("/api/projects/{pid}/assets/{aid}/proxy")
    def asset_proxy(pid: str, aid: str, range_header: str | None = Header(default=None, alias="Range")):
        root = _project_root(data_dir, pid)
        library = _library(root)
        try:
            asset = library.asset(aid).media_asset
            path = make_proxy(asset, data_dir / "cache")
        except (AssetImportError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "asset not found or cannot be proxied") from exc
        size = path.stat().st_size
        headers = {"Accept-Ranges": "bytes", "Content-Type": "video/mp4"}
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            if match.group(1):
                start = int(match.group(1)); end = int(match.group(2) or size - 1)
            else:
                suffix = int(match.group(2)); start = max(0, size - suffix); end = size - 1
            if start >= size or end < start:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            length = end - start + 1
            headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
            def blocks():
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            return StreamingResponse(blocks(), status_code=206, headers=headers, media_type="video/mp4")
        return FileResponse(path, media_type="video/mp4", headers=headers)

    @app.get("/api/projects/{pid}/assets/{aid}/thumbnails/{n}")
    def asset_thumbnail(pid: str, aid: str, n: int):
        if n < 0 or n >= 10:
            raise HTTPException(404, "thumbnail not found")
        library = _library(_project_root(data_dir, pid))
        try:
            asset = library.asset(aid).media_asset
            if asset.kind == "image" and asset.duration_ms is None:
                asset = asset.model_copy(update={"duration_ms": 5000})
            path = make_thumbnails(asset, data_dir / "cache", count=10, width=320)[n]
        except (AssetImportError, FileNotFoundError, ValueError, RuntimeError) as exc:
            raise HTTPException(404, "thumbnail not found") from exc
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/projects/{pid}/timeline")
    def get_timeline(pid: str):
        root = _project_root(data_dir, pid)
        timeline = EditorTimeline.load(root / "timeline.json")
        return {"revision": timeline.revision, "timeline": timeline.model_dump(mode="json")}

    @app.put("/api/projects/{pid}/timeline")
    def put_timeline(pid: str, body: dict[str, Any]):
        root = _project_root(data_dir, pid)
        current = EditorTimeline.load(root / "timeline.json")
        base = body.get("base_revision")
        if base != current.revision:
            raise HTTPException(409, "timeline revision conflict")
        raw = body.get("timeline")
        if not isinstance(raw, dict):
            raise HTTPException(422, "timeline must be an object")
        try:
            library = _library(root)
            project_assets = {asset.asset_id: asset for asset in library.project.assets}
            raw = dict(raw)
            raw["project_id"] = pid
            raw["revision"] = current.revision + 1
            raw["parent_revision"] = current.revision
            # Library owns media metadata and paths; user text assets remain timeline-only.
            given_assets = raw.get("assets", [])
            given_text = [item for item in given_assets if isinstance(item, dict) and item.get("kind") == "text"]
            ids = {item.get("asset_id") for item in given_assets if isinstance(item, dict)}
            for clip in raw.get("clips", []):
                asset_id = clip.get("asset_id") if isinstance(clip, dict) else None
                if asset_id in project_assets and asset_id not in ids:
                    given_assets.append(project_assets[asset_id].media_asset.model_dump(mode="json"))
            raw["assets"] = [asset.media_asset.model_dump(mode="json") for asset in library.project.assets]
            raw["assets"].extend(given_text)
            timeline = EditorTimeline.model_validate(raw)
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        violations = validate_editor_timeline(timeline)
        timeline.save(root / "timeline.json")
        library.project = library.project.model_copy(update={"timeline": timeline})
        library.save()
        return {"revision": timeline.revision, "violations": [item.code for item in violations]}

    def start_job(pid: str, profile: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        root = _project_root(data_dir, pid)
        record = {"job_id": job_id, "project_id": pid, "status": "queued", "progress": 0.0,
                  "message": "Queued", "output": None, "report": None, "error": None,
                  "cancel": threading.Event()}
        with app.state.job_lock:
            app.state.jobs[job_id] = record

        def work():
            partial_output = None
            try:
                with app.state.job_lock:
                    if record["cancel"].is_set():
                        record["status"] = "cancelled"
                        record["message"] = "Cancelled"
                        return
                    record["status"] = "running"
                    record["message"] = "Preparing render"
                timeline = EditorTimeline.load(root / "timeline.json")
                library = _library(root)
                selected = get_profile(profile)
                timeline = timeline.model_copy(update={"settings": EditorSettings(
                    fps_num=selected.fps, fps_den=1, width=selected.width, height=selected.height)})
                output = root / "exports" / f"{profile}_{job_id}.mp4"
                partial_output = output.with_name(output.stem + ".partial.mp4")
                plan = EditorRenderer().compile(timeline, library)
                def progress(value: float, message: str):
                    with app.state.job_lock:
                        record["progress"] = float(value)
                        record["message"] = message
                result = EditorRenderer().render(plan, out=output, progress=progress,
                                                  cancel=record["cancel"].is_set)
                if record["cancel"].is_set():
                    output.unlink(missing_ok=True)
                    record.update(status="cancelled", message="Cancelled", output=None)
                    return
                record.update(status="done", progress=1.0, message="Render complete",
                               output=str(result.out_path), report={"ok": result.report.ok,
                               "problems": result.report.problems, "measured": result.report.measured})
            except RenderCancelled:
                if partial_output:
                    partial_output.unlink(missing_ok=True)
                record.update(status="cancelled", message="Cancelled", output=None)
            except Exception as exc:
                if partial_output:
                    partial_output.unlink(missing_ok=True)
                record.update(status="failed", message="Render failed", error=f"{type(exc).__name__}: {exc}")

        app.state.executor.submit(work)
        return job_id

    @app.post("/api/projects/{pid}/render", status_code=202)
    def render_project(pid: str, body: dict[str, Any]):
        root = _project_root(data_dir, pid)
        profile = body.get("profile")
        if not isinstance(profile, str) or profile not in EXPORT_PROFILES:
            raise HTTPException(422, "unknown export profile")
        if not (root / "timeline.json").is_file():
            raise HTTPException(404, "timeline not found")
        return {"job_id": start_job(pid, profile)}

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        with app.state.job_lock:
            record = app.state.jobs.get(jid)
            if record is None:
                raise HTTPException(404, "job not found")
            return {key: record[key] for key in ("job_id", "status", "progress", "message", "output", "report", "error")}

    @app.post("/api/jobs/{jid}/cancel", status_code=202)
    def cancel_job(jid: str):
        with app.state.job_lock:
            record = app.state.jobs.get(jid)
            if record is None:
                raise HTTPException(404, "job not found")
            if record["status"] in {"queued", "running"}:
                record["cancel"].set()
        return {"ok": True}

    @app.post("/api/dialog/open-files")
    def open_files():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            try:
                selected = filedialog.askopenfilenames(title="Add media")
            finally:
                root.destroy()
            return {"paths": list(selected)}
        except Exception as exc:
            raise HTTPException(503, f"native file picker unavailable: {exc}") from exc

    @app.post("/api/reveal")
    def reveal_export(body: dict[str, Any]):
        value = body.get("path")
        if not isinstance(value, str):
            raise HTTPException(422, "path must be a string")
        target = Path(value).resolve()
        projects = (data_dir / "projects").resolve()
        try:
            relative = target.relative_to(projects)
        except ValueError as exc:
            raise HTTPException(403, "only project exports may be revealed") from exc
        if (len(relative.parts) != 3 or relative.parts[1] != "exports" or not target.is_file()
                or not (projects / relative.parts[0] / "project.json").is_file()):
            raise HTTPException(403, "only existing export files may be revealed")
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        return {"ok": True}

    @app.get("/")
    def index():
        index_path = ui_dist / "index.html"
        if not index_path.is_file():
            raise HTTPException(503, "UI is not built; run npm run build in ui/")
        content = index_path.read_text(encoding="utf-8")
        tag = f'<meta name="editor-token" content="{html.escape(token, quote=True)}">'
        if "</head>" in content:
            content = content.replace("</head>", tag + "</head>", 1)
        else:
            content = tag + content
        return HTMLResponse(content)

    if ui_dist.is_dir():
        app.mount("/static", StaticFiles(directory=ui_dist, check_dir=True), name="ui-static")
    return app
