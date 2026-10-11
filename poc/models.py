"""Model registry and loader for offline enforcement (docs/OFFLINE_POLICY.md §4).

The model registry is a JSON file listing allowed local model files with their
SHA-256 hash, licence, and size. Before any model file is loaded, the loader
checks:
  * the model ID exists in the registry,
  * the file exists at the listed path,
  * the file's SHA-256 matches the registry,
  * the file size matches (optional fast reject),
  * the licence is acceptable (currently we just record it; product may enforce).

If any check fails, ModelRegistryError is raised.

The registry itself is intended to be version-controlled and updated only when
a new local model is explicitly approved (e.g., adding a face detector).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class ModelRegistryError(RuntimeError):
    """Raised when a model fails registry validation."""


@dataclass(frozen=True)
class ModelRegistryEntry:
    """Single entry in the model registry."""
    id: str
    file: str  # relative to registry directory or absolute path
    sha256: str  # lowercase hex
    licence: str
    size_bytes: int
    description: Optional[str] = None


def _compute_sha256(path: Path) -> str:
    """Return SHA-256 hex digest of file at path."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_model_registry(registry_path: Path | str) -> List[ModelRegistryEntry]:
    """Load and parse the model registry JSON file."""
    registry_path = Path(registry_path)
    if not registry_path.is_file():
        raise ModelRegistryError(f"Model registry not found: {registry_path}")
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ModelRegistryError(f"Invalid JSON in model registry: {e}") from e
    if not isinstance(data, list):
        raise ModelRegistryError("Model registry root must be a JSON array")
    entries: List[ModelRegistryEntry] = []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            raise ModelRegistryError(f"Registry entry {i} must be a JSON object")
        try:
            entry = ModelRegistryEntry(
                id=str(obj["id"]),
                file=str(obj["file"]),
                sha256=str(obj["sha256"]).lower(),
                licence=str(obj["licence"]),
                size_bytes=int(obj["size_bytes"]),
                description=obj.get("description"),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise ModelRegistryError(
                f"Invalid or missing field in registry entry {i}: {e}"
            ) from e
        entries.append(entry)
    return entries


def get_model_path(
    model_id: str,
    registry_path: Path | str,
    registry_dir: Optional[Path | str] = None,
) -> Path:
    """Validate a model ID and return the absolute path to the model file.

    Steps:
      1. Load registry from ``registry_path``.
      2. Find entry with matching ``model_id``.
      3. Resolve ``file`` relative to ``registry_dir`` (defaults to
         registry_path's parent) if it is not already absolute.
      4. Check that the file exists.
      5. Verify SHA-256 hash matches.
      6. Verify file size matches (optional but cheap).
      7. Return the absolute Path to the model file.

    Raises ModelRegistryError on any validation failure.
    """
    registry_path = Path(registry_path)
    if registry_dir is None:
        registry_dir = registry_path.parent
    else:
        registry_dir = Path(registry_dir)

    entries = load_model_registry(registry_path)
    entry: Optional[ModelRegistryEntry] = None
    for e in entries:
        if e.id == model_id:
            entry = e
            break
    if entry is None:
        raise ModelRegistryError(f"Model ID not found in registry: {model_id!r}")

    # Resolve model file path
    model_rel = Path(entry.file)
    if model_rel.is_absolute():
        model_path = model_rel
    else:
        model_path = (registry_dir / model_rel).resolve()

    if not model_path.is_file():
        raise ModelRegistryError(
            f"Model file not found: {model_path} (registered for ID {model_id!r})"
        )

    # Fast size check
    actual_size = model_path.stat().st_size
    if actual_size != entry.size_bytes:
        raise ModelRegistryError(
            f"Model file size mismatch for ID {model_id!r}: "
            f"expected {entry.size_bytes}, got {actual_size}"
        )

    # Verify SHA-256
    actual_hash = _compute_sha256(model_path)
    if actual_hash != entry.sha256:
        raise ModelRegistryError(
            f"Model file SHA-256 mismatch for ID {model_id!r}: "
            f"expected {entry.sha256}, got {actual_hash}"
        )

    return model_path


def is_model_registered(model_id: str, registry_path: Path | str) -> bool:
    """Return True if ``model_id`` exists in the registry."""
    try:
        entries = load_model_registry(registry_path)
        return any(e.id == model_id for e in entries)
    except ModelRegistryError:
        return False