"""Local, external-source asset library for editable timeline projects."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import cv2
import imageio_ffmpeg
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from poc.execute.render import MediaProbeError, probe
from poc.perceive.cache import sha256_file
from poc.timeline.schema import EditorTimeline, MediaAsset
from poc.timeline.validate import Violation, validate_editor_timeline

__all__ = ["AssetImportError", "AssetLimits", "ProjectAsset", "ProjectDocument", "ProjectAssetLibrary"]

AssetKind = Literal["video", "image", "audio", "music"]
_EXTENSION_KIND: dict[str, AssetKind] = {
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".bmp": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".flac": "audio", ".aac": "audio",
}


class AssetImportError(RuntimeError):
    """An import, metadata, duplicate, or relink operation failed."""


class AssetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    max_duration_ms: int = Field(default=4 * 60 * 60 * 1000, gt=0)
    max_dimension: int = Field(default=16384, gt=0)


class ProjectAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    kind: AssetKind
    content_hash: str
    source_path: str
    size_bytes: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    mime_type: str
    metadata: dict[str, int | float | bool | str | None]
    imported_at: str
    duplicate: bool = Field(default=False, exclude=True)

    @property
    def media_asset(self) -> MediaAsset:
        return MediaAsset(
            asset_id=self.asset_id,
            kind=self.kind,
            uri=self.source_path,
            duration_ms=self.duration_ms,
            mime_type=self.mime_type,
            metadata={**self.metadata, "content_hash": self.content_hash},
        )


class ProjectDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_schema_version: Literal["1.0"] = "1.0"
    project_id: str
    assets: list[ProjectAsset] = Field(default_factory=list)
    timeline: EditorTimeline | None = None


class ProjectAssetLibrary:
    """Project metadata store; original media remain at their external paths."""

    MANIFEST_NAME = "project.json"

    def __init__(self, root: Path, project: ProjectDocument, limits: AssetLimits):
        self.root = root.resolve()
        self.project = project
        self.limits = limits

    @classmethod
    def create(
        cls, root: str | Path, *, project_id: str, limits: AssetLimits | None = None
    ) -> "ProjectAssetLibrary":
        project_root = Path(root).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        manifest = project_root / cls.MANIFEST_NAME
        if manifest.exists():
            raise AssetImportError(f"project manifest already exists: {manifest}")
        library = cls(project_root, ProjectDocument(project_id=project_id), limits or AssetLimits())
        library.save()
        return library

    @classmethod
    def load(
        cls, root: str | Path, *, limits: AssetLimits | None = None
    ) -> "ProjectAssetLibrary":
        project_root = Path(root).resolve()
        manifest = project_root / cls.MANIFEST_NAME
        if not manifest.is_file():
            raise AssetImportError(f"project manifest does not exist: {manifest}")
        try:
            project = ProjectDocument.model_validate_json(manifest.read_text(encoding="utf-8"))
        except Exception as error:
            raise AssetImportError(f"project manifest is invalid: {manifest}: {error}") from error
        return cls(project_root, project, limits or AssetLimits())

    @staticmethod
    def hash_file(path: str | Path) -> str:
        return sha256_file(path)

    def _validate_dimensions(self, path: Path, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise AssetImportError(f"media has invalid dimensions {width}x{height}: {path}")
        if width > self.limits.max_dimension or height > self.limits.max_dimension:
            raise AssetImportError(
                f"media dimensions {width}x{height} exceed configured limit "
                f"{self.limits.max_dimension}px: {path}"
            )

    def _probe_asset(
        self, path: Path, kind: AssetKind
    ) -> tuple[int | None, dict[str, int | float | bool | str | None]]:
        if kind == "image":
            try:
                encoded = np.fromfile(path, dtype=np.uint8)
                image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
            except (OSError, cv2.error) as error:
                raise AssetImportError(f"cannot read image {path}: {error}") from error
            if image is None:
                raise AssetImportError(f"unsupported or corrupt image: {path}")
            height, width = image.shape[:2]
            self._validate_dimensions(path, width, height)
            channels = image.shape[2] if image.ndim == 3 else 1
            return None, {"width": width, "height": height, "channels": channels}

        try:
            info = probe(path, imageio_ffmpeg.get_ffmpeg_exe())
        except (OSError, MediaProbeError) as error:
            raise AssetImportError(f"cannot probe media {path}: {error}") from error
        if kind == "video" and not info.has_video:
            raise AssetImportError(f"video file contains no video stream: {path}")
        if kind in {"audio", "music"} and not info.has_audio:
            raise AssetImportError(f"audio file contains no audio stream: {path}")
        if info.duration_ms <= 0:
            raise AssetImportError(f"media has an invalid or empty duration: {path}")
        if info.duration_ms > self.limits.max_duration_ms:
            raise AssetImportError(
                f"media duration {info.duration_ms} ms exceeds configured limit "
                f"{self.limits.max_duration_ms} ms: {path}"
            )
        metadata: dict[str, int | float | bool | str | None] = {
            "has_video": info.has_video, "has_audio": info.has_audio, "fps": info.fps,
        }
        if kind == "video":
            capture = cv2.VideoCapture(str(path))
            try:
                if not capture.isOpened():
                    raise AssetImportError(f"cannot decode video metadata: {path}")
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            finally:
                capture.release()
            self._validate_dimensions(path, width, height)
            metadata.update({"width": width, "height": height})
        return info.duration_ms, metadata

    def import_asset(self, source_path: str | Path) -> ProjectAsset:
        path = Path(source_path).expanduser().resolve(strict=False)
        if not path.is_file():
            raise AssetImportError(f"source file does not exist: {path}")
        kind = _EXTENSION_KIND.get(path.suffix.lower())
        if kind is None:
            supported = ", ".join(sorted(_EXTENSION_KIND))
            raise AssetImportError(f"unsupported media type {path.suffix or '<no extension>'!r}; supported: {supported}")
        try:
            size_bytes = path.stat().st_size
        except OSError as error:
            raise AssetImportError(f"cannot stat source file {path}: {error}") from error
        if size_bytes <= 0:
            raise AssetImportError(f"source file is empty: {path}")
        if size_bytes > self.limits.max_file_bytes:
            raise AssetImportError(
                f"file size {size_bytes} bytes exceeds configured limit "
                f"{self.limits.max_file_bytes} bytes: {path}"
            )
        try:
            content_hash = sha256_file(path)
            duration_ms, metadata = self._probe_asset(path, kind)
        except AssetImportError:
            raise
        except OSError as error:
            raise AssetImportError(f"cannot read source file {path}: {error}") from error

        existing = next(
            (item for item in self.project.assets if item.kind == kind and item.content_hash == content_hash),
            None,
        )
        if existing is not None:
            return existing.model_copy(update={"duplicate": True})

        stable_digest = hashlib.sha256(f"{kind}\0{content_hash}".encode("ascii")).hexdigest()
        asset = ProjectAsset(
            asset_id=f"asset_{stable_digest[:32]}", kind=kind, content_hash=content_hash,
            source_path=str(path), size_bytes=size_bytes, duration_ms=duration_ms,
            mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            metadata=metadata, imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.project = self.project.model_copy(update={"assets": [*self.project.assets, asset]})
        try:
            self.save()
        except OSError as error:
            self.project = self.project.model_copy(update={"assets": self.project.assets[:-1]})
            raise AssetImportError(f"could not persist imported asset record: {error}") from error
        return asset

    def asset(self, asset_id: str) -> ProjectAsset:
        for asset in self.project.assets:
            if asset.asset_id == asset_id:
                return asset
        raise AssetImportError(f"unknown project asset id: {asset_id}")

    def missing_sources(self) -> list[str]:
        return [item.asset_id for item in self.project.assets if not Path(item.source_path).is_file()]

    def resolve_source(self, asset_id: str, *, verify_hash: bool = True) -> Path:
        asset = self.asset(asset_id)
        path = Path(asset.source_path)
        if not path.is_file():
            raise AssetImportError(f"asset source is missing: {path}; relink asset {asset_id!r}")
        if verify_hash and sha256_file(path) != asset.content_hash:
            raise AssetImportError(f"asset source content changed: {path}; relink or restore the original")
        return path

    def relink(self, asset_id: str, new_source_path: str | Path) -> ProjectAsset:
        existing = self.asset(asset_id)
        path = Path(new_source_path).expanduser().resolve(strict=False)
        if not path.is_file():
            raise AssetImportError(f"relink target does not exist: {path}")
        kind = _EXTENSION_KIND.get(path.suffix.lower())
        if kind != existing.kind:
            raise AssetImportError(f"relink target media kind {kind!r} does not match {existing.kind!r}: {path}")
        try:
            size_bytes = path.stat().st_size
            content_hash = sha256_file(path)
        except OSError as error:
            raise AssetImportError(f"cannot read relink target {path}: {error}") from error
        if content_hash != existing.content_hash:
            raise AssetImportError(f"relink target hash does not match asset {asset_id!r}: {path}")
        if size_bytes > self.limits.max_file_bytes:
            raise AssetImportError(f"relink target exceeds configured size limit: {path}")
        duration_ms, metadata = self._probe_asset(path, kind)
        replacement = existing.model_copy(update={
            "source_path": str(path), "size_bytes": size_bytes,
            "duration_ms": duration_ms, "metadata": metadata,
        })
        assets = [replacement if item.asset_id == asset_id else item for item in self.project.assets]
        timeline = self.project.timeline
        if timeline is not None:
            timeline_assets = [
                replacement.media_asset if item.asset_id == asset_id else item
                for item in timeline.assets
            ]
            timeline = timeline.model_copy(update={"assets": timeline_assets})
        previous = self.project
        self.project = self.project.model_copy(update={"assets": assets, "timeline": timeline})
        try:
            self.save()
        except OSError as error:
            self.project = previous
            raise AssetImportError(f"could not persist relink of {asset_id!r}: {error}") from error
        return replacement

    def set_timeline(self, timeline: EditorTimeline) -> None:
        if timeline.project_id != self.project.project_id:
            raise AssetImportError("timeline project_id does not match this project")
        project_ids = {item.asset_id for item in self.project.assets}
        missing = [item.asset_id for item in timeline.assets if item.asset_id not in project_ids]
        if missing:
            raise AssetImportError(f"timeline references assets absent from project: {', '.join(missing)}")
        violations = validate_editor_timeline(timeline)
        if violations:
            raise AssetImportError("timeline is invalid: " + "; ".join(map(str, violations)))
        previous = self.project
        self.project = self.project.model_copy(update={"timeline": timeline})
        try:
            self.save()
        except OSError as error:
            self.project = previous
            raise AssetImportError(f"could not persist timeline: {error}") from error

    def validate_timeline(self) -> list[Violation]:
        timeline = self.project.timeline
        if timeline is None:
            return []
        assets = {item.asset_id: item for item in self.project.assets}
        violations = list(validate_editor_timeline(timeline))
        for item in timeline.assets:
            project_asset = assets.get(item.asset_id)
            if project_asset is None:
                violations.append(Violation(code="UNKNOWN_ASSET", detail=f"asset {item.asset_id!r} is not in project"))
            elif item.uri != project_asset.source_path:
                violations.append(Violation(
                    code="ASSET_PATH_MISMATCH",
                    detail=f"timeline path for {item.asset_id!r} is not synchronized with project metadata",
                ))
        return violations

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / self.MANIFEST_NAME
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root,
                prefix=".project.", suffix=".tmp", delete=False,
            ) as stream:
                stream.write(self.project.model_dump_json(indent=2) + "\n")
                temporary = Path(stream.name)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
