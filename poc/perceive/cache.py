"""Content-addressed JSON cache for deterministic media analysis."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def default_analysis_cache_dir() -> Path:
    """Return the per-user analysis cache directory for this platform."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "poc" / "analysis"


def sha256_file(path: str | Path) -> str:
    """Return a streaming SHA-256 digest for a source file."""
    path = Path(path)
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_or_compute(
    source_path: str | Path,
    *,
    namespace: str,
    analyzer_version: str,
    parameters: dict[str, object],
    model: type[ModelT],
    compute: Callable[[], ModelT],
    cache_dir: str | Path | None = None,
    content_hash: str | None = None,
) -> ModelT:
    """Load a validated result or compute and atomically cache it."""
    source = Path(source_path).resolve()
    source_hash = content_hash or sha256_file(source)
    if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash.lower()):
        raise ValueError("content_hash must be a 64-character SHA-256 hex digest")
    key_data = {
        "cache_schema": 1,
        "source_sha256": source_hash,
        "analyzer_version": analyzer_version,
        "parameters": parameters,
    }
    encoded = json.dumps(key_data, sort_keys=True, separators=(",", ":"), default=str)
    key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    root = Path(cache_dir) if cache_dir is not None else default_analysis_cache_dir()
    cache_path = root / namespace / key[:2] / f"{key}.json"

    if cache_path.is_file():
        try:
            return model.model_validate_json(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    result = compute()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix=f".{key}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(result.model_dump_json() + "\n")
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, cache_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return result