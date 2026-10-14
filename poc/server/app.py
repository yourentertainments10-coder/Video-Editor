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
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from poc.director import AssetRef, parse_brief, selected_video_ids
from poc.director.apply import apply_style
from poc.director.footage import Footage, clip_tags, replace_sources, summarise_footage
from poc.director.revise import apply_operations, parse_instructions
from poc.director.style import FAMILIES, LOOKS, STYLES, accent_cuts, plan_style, set_transitions
from poc.perceive.shots import TAG_LABELS, ShotMap, analyze_shots
from poc.director.brief import MAX_TITLE_CHARS
from poc.resources import ui_dist_dir
from poc.decide import CutterError, GridError, cut
from poc.editor_bridge import legacy_to_editor
from poc.ingest.normalize import normalize_asset
from poc.ingest.proxy import make_audio_preview, make_proxy, make_thumbnails
from poc.perceive.footage import analyze_footage
from poc.perceive.music import analyze_music
from poc.project_assets import AssetImportError, ProjectAssetLibrary
from poc.render.editor import EditorRenderError, EditorRenderer, RenderCancelled
from poc.render.export import EXPORT_PROFILES, get_profile
from poc.timeline import (
    EditorSettings, EditorTimeline, Layer, MediaAsset, TimeBase, Track,
    serialize_effect_catalogue, serialize_transition_catalogue,
)
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


# Bump together with API_LEVEL in ui/src/api.ts whenever endpoints change, so a page built
# for a newer server can tell the user to restart an old one (instead of failing with 404).
CHAT_LIMIT = 300                 # messages kept per project
CHAT_MAX_BYTES = 2 * 1024 * 1024
API_LEVEL = 6   # 1: U1-U4, 2: Local Director briefs (LD-1), 3: revisions (LD-2), 4: chat history, 5: footage, 6: styles


def _library(root: Path) -> ProjectAssetLibrary:
    return ProjectAssetLibrary.load(root)


def _range_file_response(path: Path, range_header: str | None, media_type: str):
    headers = {"Accept-Ranges": "bytes", "Content-Type": media_type}
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=headers)
    size = path.stat().st_size
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

    return StreamingResponse(blocks(), status_code=206, headers=headers, media_type=media_type)


def _asset_info(item, probe_cache: dict[str, dict[str, Any]], probe_lock: threading.Lock) -> dict[str, Any]:
    cache_key = f"{item.kind}:{item.content_hash}"
    with probe_lock:
        cached = probe_cache.get(cache_key)
    if cached is None:
        media = item.media_asset
        try:
            normalized = normalize_asset(media)
            probed = {
                "width": normalized.width, "height": normalized.height,
                "fps": normalized.fps, "has_audio": normalized.has_audio,
                "rotation": normalized.rotation_degrees, "hdr": normalized.is_hdr,
            }
        except Exception:
            probed = {
                "width": item.metadata.get("width"), "height": item.metadata.get("height"),
                "fps": item.metadata.get("fps"),
                "has_audio": bool(item.metadata.get("has_audio", False)),
                "rotation": 0, "hdr": False,
            }
        with probe_lock:
            cached = probe_cache.setdefault(cache_key, probed)
    return {
        "asset_id": item.asset_id, "kind": item.kind,
        "file_name": Path(item.source_path).name, "duration_ms": item.duration_ms,
        **cached, "thumbnail_count": 10 if cached.get("width") and cached.get("height") else 0,
    }


def create_app(*, data_dir: Path, token: str, allowed_hosts: tuple[str, ...],
               dev_mode: bool = False) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    ui_dist = ui_dist_dir()
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            application.state.executor.shutdown(wait=True, cancel_futures=True)
            application.state.footage_executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Offline Video Editor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.data_dir = data_dir
    app.state.jobs: dict[str, dict[str, Any]] = {}
    app.state.job_lock = threading.RLock()
    app.state.timeline_lock = threading.RLock()
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editor-render")
    app.state.asset_probe_cache = {}
    app.state.asset_probe_lock = threading.Lock()
    app.state.footage: dict[str, tuple[str, Any]] = {}     # content hash -> ("pending"|"ready"|"failed", data)
    app.state.footage_lock = threading.Lock()
    app.state.footage_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="editor-footage")

    def look_at_footage(library: ProjectAssetLibrary) -> tuple[Footage, int, dict[str, dict[str, Any]]]:
        """What each video of a project shows (poc.perceive.shots). Files not looked at yet are
        queued in the background; returns (ready footage, files still pending, status per asset)."""
        ready: Footage = {}
        pending = 0
        status: dict[str, dict[str, Any]] = {}
        for item in library.project.assets:
            if item.kind != "video":
                continue
            key = item.content_hash
            with app.state.footage_lock:
                state = app.state.footage.get(key)
                if state is None:
                    state = ("pending", None)
                    app.state.footage[key] = state
                    app.state.footage_executor.submit(_watch, key, Path(item.source_path))
            if state[0] == "ready":
                ready[item.asset_id] = (item.media_asset, state[1])
                status[item.asset_id] = {"status": "ready", "labels": summarise_footage(state[1]),
                                         "faces": state[1].faces_measured}
            elif state[0] == "failed":
                status[item.asset_id] = {"status": "failed", "error": state[1]}
            else:
                pending += 1
                status[item.asset_id] = {"status": "pending"}
        return ready, pending, status

    def _watch(key: str, source: Path) -> None:
        try:
            shots: ShotMap = analyze_shots(source, cache_dir=data_dir / "analysis-cache", content_hash=key)
            state = ("ready", shots)
        except Exception as exc:  # noqa: BLE001 - shown to the user as "could not look at this file"
            state = ("failed", f"{type(exc).__name__}: {exc}"[:300])
        with app.state.footage_lock:
            app.state.footage[key] = state

    app.state.media_paths: dict[tuple, tuple] = {}
    app.state.media_paths_lock = threading.Lock()

    def cached_media(kind: str, pid: str, aid: str, build) -> Path:
        """Path of an asset's proxy/audio preview, without re-probing it on every request.

        The browser sends many Range requests per file while previewing; the full lookup
        (load project, probe the source, hash the cache key) is only redone when project.json
        changes or the cached file is gone.
        """
        root = _project_root(data_dir, pid)
        stat = (root / "project.json").stat()
        key = (kind, pid, aid)
        stamp = (stat.st_mtime_ns, stat.st_size)
        with app.state.media_paths_lock:
            hit = app.state.media_paths.get(key)
        if hit and hit[0] == stamp and hit[1].is_file():
            return hit[1]
        library = _library(root)
        try:
            path = build(library.asset(aid).media_asset, data_dir / "cache")
        except (AssetImportError, FileNotFoundError, ValueError) as exc:
            message = "asset not found or cannot be proxied" if kind == "proxy" else "asset has no audio preview"
            raise HTTPException(404, message) from exc
        with app.state.media_paths_lock:
            app.state.media_paths[key] = (stamp, path)
        return path

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
        if request.url.path.startswith("/api/") and not dev_preflight:
            header_ok = secrets.compare_digest(request.headers.get("x-editor-token", ""), token)
            cookie_media = (
                request.method == "GET"
                and re.fullmatch(r"/api/projects/p_[0-9a-f]{8}/assets/[^/]+/(?:proxy|audio|thumbnails/\d+)", request.url.path)
            )
            cookie_ok = cookie_media and secrets.compare_digest(
                request.cookies.get("editor_session", ""), token
            )
            if not (header_ok or cookie_ok):
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
        return {"ok": True, "api_level": API_LEVEL}

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
        look_at_footage(library)                      # start looking at new videos right away
        return {"assets": [_asset_info(item, app.state.asset_probe_cache, app.state.asset_probe_lock) for item in library.project.assets]}

    @app.get("/api/projects/{pid}/assets")
    def list_assets(pid: str):
        library = _library(_project_root(data_dir, pid))
        return {"assets": [_asset_info(item, app.state.asset_probe_cache, app.state.asset_probe_lock) for item in library.project.assets]}

    @app.get("/api/projects/{pid}/assets/{aid}/proxy")
    def asset_proxy(pid: str, aid: str, range_header: str | None = Header(default=None, alias="Range")):
        path = cached_media("proxy", pid, aid, lambda asset, cache: make_proxy(asset, cache))
        return _range_file_response(path, range_header, "video/mp4")

    @app.get("/api/projects/{pid}/assets/{aid}/audio")
    def asset_audio(pid: str, aid: str, range_header: str | None = Header(default=None, alias="Range")):
        path = cached_media("audio", pid, aid, lambda asset, cache: make_audio_preview(asset, cache))
        return _range_file_response(path, range_header, "audio/mp4")

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
            raw = dict(raw)
            raw["project_id"] = pid
            raw["revision"] = current.revision + 1
            raw["parent_revision"] = current.revision
            # Library owns media metadata and paths; user text assets remain timeline-only.
            given_assets = raw.get("assets", [])
            given_text = [item for item in given_assets if isinstance(item, dict) and item.get("kind") == "text"]
            raw["assets"] = [asset.media_asset.model_dump(mode="json") for asset in library.project.assets]
            raw["assets"].extend(given_text)
            timeline = EditorTimeline.model_validate(raw)
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        violations = validate_editor_timeline(timeline)
        with app.state.timeline_lock:
            latest = EditorTimeline.load(root / "timeline.json")
            if latest.revision != base:
                raise HTTPException(409, "timeline revision conflict")
            timeline = timeline.model_copy(update={
                "revision": latest.revision + 1, "parent_revision": latest.revision,
            })
            timeline.save(root / "timeline.json")
            library.project = library.project.model_copy(update={"timeline": timeline})
            library.save()
        return {
            "revision": timeline.revision,
            "violations": [item.code for item in violations],
            "issues": [
                {"code": item.code, "message": item.detail,
                 **({"clip_id": item.clip_id} if item.clip_id else {})}
                for item in violations
            ],
        }

    @app.get("/api/catalogue")
    def catalogue():
        return {
            "transitions": json.loads(serialize_transition_catalogue()),
            "effects": json.loads(serialize_effect_catalogue()),
        }

    @app.post("/api/projects/{pid}/check")
    def check_timeline(pid: str):
        root = _project_root(data_dir, pid)
        timeline = EditorTimeline.load(root / "timeline.json")
        library = _library(root)
        try:
            EditorRenderer().compile(timeline, library)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    @app.post("/api/projects/{pid}/brief")
    def read_brief(pid: str, body: dict[str, Any]):
        """Local Director: understand a written brief (offline, rule-based). Changes nothing."""
        text = body.get("text")
        if not isinstance(text, str) or len(text) > 1000:
            raise HTTPException(422, "text must be a string of at most 1000 characters")
        library = _library(_project_root(data_dir, pid))
        refs = [AssetRef(asset_id=item.asset_id, file_name=Path(item.source_path).name, kind=item.kind)
                for item in library.project.assets]
        result = parse_brief(text, refs)
        payload = result.model_dump()
        payload["video_asset_ids"] = selected_video_ids(result.brief, refs)
        return payload

    @app.get("/api/projects/{pid}/footage")
    def footage_status(pid: str):
        """What the Director has seen in each video (faces, shot size, light, shake); starts
        looking at files it has not seen yet."""
        _, pending, status = look_at_footage(_library(_project_root(data_dir, pid)))
        return {"assets": status, "pending": pending}

    @app.get("/api/projects/{pid}/chat")
    def get_chat(pid: str):
        """The Director conversation of a project (kept beside project.json)."""
        path = _project_root(data_dir, pid) / "chat.json"
        try:
            messages = json.loads(path.read_text(encoding="utf-8")).get("messages", [])
        except (OSError, ValueError, AttributeError):
            messages = []
        return {"messages": messages if isinstance(messages, list) else []}

    @app.put("/api/projects/{pid}/chat")
    def put_chat(pid: str, body: dict[str, Any]):
        messages = body.get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise HTTPException(422, "messages must be a list of objects")
        messages = messages[-CHAT_LIMIT:]
        text = json.dumps({"messages": messages}, ensure_ascii=False)
        if len(text.encode("utf-8")) > CHAT_MAX_BYTES:
            raise HTTPException(413, "conversation is too large to save")
        root = _project_root(data_dir, pid)
        partial = root / f"chat.json.{secrets.token_hex(4)}.partial"
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, root / "chat.json")
        return {"saved": len(messages)}

    @app.post("/api/projects/{pid}/revise")
    def revise(pid: str, body: dict[str, Any]):
        """Local Director: turn a written change into a PROPOSED timeline. Saves nothing;
        the UI applies it through the normal edit path (autosave + undo)."""
        text = body.get("text")
        if not isinstance(text, str) or len(text) > 500:
            raise HTTPException(422, "text must be a string of at most 500 characters")
        try:
            current = EditorTimeline.model_validate(body.get("timeline"))
        except ValidationError as exc:
            raise HTTPException(422, f"invalid timeline: {exc.errors()[:1]}") from exc
        footage, pending, _ = look_at_footage(_library(_project_root(data_dir, pid)))
        result = parse_instructions(text, current, footage, pending)
        payload = result.model_dump(exclude={"operations"})
        payload["operations"] = [op.model_dump(exclude_none=True) for op in result.operations]
        payload["notes"], payload["timeline"] = [], None
        if result.operations:
            try:
                proposed, notes = apply_operations(current, result.operations, footage)
                EditorRenderer().compile(proposed, _library(_project_root(data_dir, pid)))
            except (ValueError, EditorRenderError) as exc:
                payload["questions"].append(f"Can't do that: {exc}")
                payload["operations"] = []
                return payload
            payload["notes"] = notes
            payload["timeline"] = proposed.model_dump(mode="json")
        return payload

    @app.post("/api/projects/{pid}/auto-edit", status_code=202)
    def auto_edit(pid: str, body: dict[str, Any]):
        root = _project_root(data_dir, pid)
        current = EditorTimeline.load(root / "timeline.json")
        base_revision = body.get("base_revision")
        if not isinstance(base_revision, int) or isinstance(base_revision, bool):
            raise HTTPException(422, "base_revision must be an integer")
        if base_revision != current.revision:
            raise HTTPException(409, "timeline revision conflict")

        mood = body.get("mood")
        duration_s = body.get("duration_s")
        music_asset_id = body.get("music_asset_id")
        video_asset_ids = body.get("video_asset_ids")
        if mood not in {"energetic", "calm"}:
            raise HTTPException(422, "mood must be 'energetic' or 'calm'")
        if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)) or duration_s <= 0:
            raise HTTPException(422, "duration_s must be greater than zero")
        if not isinstance(music_asset_id, str) or not music_asset_id:
            raise HTTPException(422, "music_asset_id is required")
        transition_policy = body.get("transition_policy", "auto")
        if transition_policy not in ("auto", "cuts_only", "soft"):
            raise HTTPException(422, "transition_policy must be 'auto', 'cuts_only' or 'soft'")
        title = body.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS):
            raise HTTPException(422, f"title must be 1-{MAX_TITLE_CHARS} characters")
        title = title.strip() if title else None
        style = body.get("style", "clean")
        if style not in STYLES:
            raise HTTPException(422, f"style must be one of: {', '.join(STYLES)}")
        look = body.get("look")
        if look is not None and look not in LOOKS:
            raise HTTPException(422, f"look must be one of: {', '.join(sorted(LOOKS))}")
        transition_family = body.get("transition_family")
        if transition_family is not None and transition_family not in FAMILIES:
            raise HTTPException(422, f"transition_family must be one of: {', '.join(FAMILIES)}")
        shots = body.get("shots") or {}
        if not isinstance(shots, dict):
            raise HTTPException(422, "shots must be {prefer: [...], avoid: [...]}")
        prefer_tags, avoid_tags = shots.get("prefer") or [], shots.get("avoid") or []
        if not all(isinstance(t, str) and t in TAG_LABELS for t in [*prefer_tags, *avoid_tags]):
            raise HTTPException(422, f"shot tags must be among: {', '.join(TAG_LABELS)}")
        if video_asset_ids is not None and (
            not isinstance(video_asset_ids, list)
            or not all(isinstance(asset_id, str) for asset_id in video_asset_ids)
        ):
            raise HTTPException(422, "video_asset_ids must be a list of asset ids")

        library = _library(root)
        try:
            music_asset = library.asset(music_asset_id)
        except AssetImportError as exc:
            raise HTTPException(422, f"music asset not found: {music_asset_id}") from exc
        if music_asset.kind not in {"audio", "music"}:
            raise HTTPException(422, "music_asset_id must identify an audio asset")

        if video_asset_ids is None:
            video_assets = [asset for asset in library.project.assets if asset.kind == "video"]
        else:
            video_assets = []
            for asset_id in video_asset_ids:
                try:
                    asset = library.asset(asset_id)
                except AssetImportError as exc:
                    raise HTTPException(422, f"video asset not found: {asset_id}") from exc
                if asset.kind != "video":
                    raise HTTPException(422, f"asset {asset_id!r} is not a video")
                if asset not in video_assets:
                    video_assets.append(asset)
        if not video_assets:
            raise HTTPException(422, "auto-edit requires at least one video asset")

        job_id = uuid.uuid4().hex[:12]
        record = {
            "job_id": job_id, "project_id": pid, "status": "queued", "progress": 0.0,
            "message": "Queued", "output": None, "report": None, "error": None,
            "encoder": None, "timings": None,
            "cancel": threading.Event(), "kind": "auto_edit",
        }
        with app.state.job_lock:
            app.state.jobs[job_id] = record

        def work_auto_edit():
            def update(status: str, progress: float, message: str):
                with app.state.job_lock:
                    record.update(status=status, progress=progress, message=message)

            try:
                if record["cancel"].is_set():
                    update("cancelled", 0.0, "Cancelled")
                    return
                update("running", 0.02, "Analysing music")
                music_map = analyze_music(
                    music_asset.source_path, cache_dir=data_dir / "analysis-cache",
                )
                if record["cancel"].is_set():
                    update("cancelled", 0.1, "Cancelled")
                    return

                footage_maps = {}
                source_to_asset_id = {}
                for index, asset in enumerate(video_assets):
                    update("running", 0.1 + 0.45 * index / len(video_assets), "Analysing footage")
                    source = Path(asset.source_path).resolve()
                    footage_maps[str(source)] = analyze_footage(
                        source, cache_dir=data_dir / "analysis-cache",
                    )
                    source_to_asset_id[source] = asset.asset_id
                    if record["cancel"].is_set():
                        update("cancelled", 0.55, "Cancelled")
                        return

                update("running", 0.6, "Cutting")
                legacy = cut(
                    music_map, music_asset.source_path, footage_maps, mood, float(duration_s),
                )
                if record["cancel"].is_set():
                    update("cancelled", 0.75, "Cancelled")
                    return

                update("running", 0.85, "Saving")
                refreshed_library = _library(root)
                editor_timeline = legacy_to_editor(
                    legacy,
                    library=refreshed_library,
                    source_to_asset_id=source_to_asset_id,
                    music_asset_id=music_asset_id,
                    settings=current.settings,
                    timeline_id=current.timeline_id,
                )
                editor_timeline = apply_style(editor_timeline, transition_policy=transition_policy,
                                              title=title)
                if prefer_tags or avoid_tags:
                    update("running", 0.75, "Looking at your footage")
                    footage: Footage = {}
                    for asset in video_assets:
                        footage[asset.asset_id] = (asset.media_asset, analyze_shots(
                            Path(asset.source_path), cache_dir=data_dir / "analysis-cache",
                            content_hash=asset.content_hash))
                        if record["cancel"].is_set():
                            update("cancelled", 0.8, "Cancelled")
                            return
                    targets = []
                    for clip in (c for c in editor_timeline.clips if c.track_id == "video"):
                        tags = clip_tags(clip, footage)
                        if tags is not None and (not all(tags.has(t) for t in prefer_tags)
                                                 or any(tags.has(t) for t in avoid_tags)):
                            targets.append(clip.clip_id)
                    if targets:
                        editor_timeline, _, _ = replace_sources(editor_timeline, targets, list(prefer_tags),
                                                                list(avoid_tags), footage)
                        # fades depend on the footage around each cut: work them out again
                        editor_timeline = apply_style(editor_timeline, transition_policy=transition_policy)
                if style != "clean" or look:
                    update("running", 0.8, "Adding transitions and effects")
                    seen, _, _ = look_at_footage(_library(root))
                    editor_timeline, _ = plan_style(editor_timeline, mood=mood, style=style, look=look, footage=seen,
                                                    transitions=transition_policy == "auto")
                if transition_family and transition_policy == "auto":
                    cuts = [c.index for c in accent_cuts(editor_timeline, mood, "bold" if style == "bold" else "auto")]
                    editor_timeline, _ = set_transitions(editor_timeline, cuts, FAMILIES[transition_family],
                                                         320 if mood == "energetic" else 650, clear_others=True)
                with app.state.timeline_lock:
                    latest = EditorTimeline.load(root / "timeline.json")
                    if latest.revision != base_revision:
                        raise RuntimeError(
                            f"timeline conflict: revision changed from {base_revision} to {latest.revision}"
                        )
                    editor_timeline = editor_timeline.model_copy(update={
                        "revision": latest.revision + 1,
                        "parent_revision": latest.revision,
                    })
                    editor_timeline.save(root / "timeline.json")
                    refreshed_library.project = refreshed_library.project.model_copy(
                        update={"timeline": editor_timeline},
                    )
                    refreshed_library.save()
                update("done", 1.0, "Auto-edit complete")
            except (CutterError, GridError, Exception) as exc:
                with app.state.job_lock:
                    if record["cancel"].is_set():
                        record.update(status="cancelled", message="Cancelled")
                    else:
                        record.update(
                            status="failed", message="Auto-edit failed",
                            error=f"{type(exc).__name__}: {exc}",
                        )

        app.state.executor.submit(work_auto_edit)
        return {"job_id": job_id}

    def start_job(pid: str, profile: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        root = _project_root(data_dir, pid)
        record = {"job_id": job_id, "project_id": pid, "status": "queued", "progress": 0.0,
                  "message": "Queued", "output": None, "report": None, "error": None,
                  "encoder": None, "timings": None,
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
                                                  cancel=record["cancel"].is_set, profile=selected)
                if record["cancel"].is_set():
                    output.unlink(missing_ok=True)
                    with app.state.job_lock:
                        record.update(status="cancelled", message="Cancelled", output=None)
                    return
                with app.state.job_lock:
                    record.update(status="done", progress=1.0, message="Render complete",
                                  output=str(result.out_path), report={"ok": result.report.ok,
                                  "problems": result.report.problems, "measured": result.report.measured},
                                  encoder=result.encoder, timings=result.timings)
            except RenderCancelled:
                if partial_output:
                    partial_output.unlink(missing_ok=True)
                with app.state.job_lock:
                    record.update(status="cancelled", message="Cancelled", output=None)
            except Exception as exc:
                if partial_output:
                    partial_output.unlink(missing_ok=True)
                with app.state.job_lock:
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
            return {key: record[key] for key in ("job_id", "status", "progress", "message", "output", "report", "error", "encoder", "timings")}

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

    @app.get("/media/projects/{pid}/exports/{filename}")
    def preview_export(pid: str, filename: str, request: Request,
                       range_header: str | None = Header(default=None, alias="Range")):
        if not secrets.compare_digest(request.cookies.get("editor_session", ""), token):
            raise HTTPException(401, "unauthorized")
        root = _project_root(data_dir, pid)
        if Path(filename).name != filename or not filename.lower().endswith(".mp4"):
            raise HTTPException(404, "export not found")
        exports = (root / "exports").resolve()
        target = (exports / filename).resolve()
        try:
            target.relative_to(exports)
        except ValueError as exc:
            raise HTTPException(404, "export not found") from exc
        if not target.is_file():
            raise HTTPException(404, "export not found")
        return _range_file_response(target, range_header, "video/mp4")

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
        response = HTMLResponse(content, headers={
            "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            "Cache-Control": "no-store",
        })
        response.set_cookie("editor_session", token, httponly=True, samesite="strict", path="/")
        return response

    if ui_dist.is_dir():
        app.mount("/static", StaticFiles(directory=ui_dist, check_dir=True), name="ui-static")
    return app
