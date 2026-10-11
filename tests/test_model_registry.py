"""Unit tests for poc.models: model registry and loader."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from poc.models import (
    ModelRegistryError,
    load_model_registry,
    get_model_path,
    is_model_registered,
)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def test_load_model_registry_success(tmp_path: Path):
    registry = tmp_path / "registry.json"
    data = [
        {
            "id": "test-model",
            "file": "model.onnx",
            "sha256": "a" * 64,
            "licence": "MIT",
            "size_bytes": 1234,
            "description": "A test model",
        }
    ]
    registry.write_text(json.dumps(data, indent=2))
    entries = load_model_registry(registry)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "test-model"
    assert e.file == "model.onnx"
    assert e.sha256 == "a" * 64
    assert e.licence == "MIT"
    assert e.size_bytes == 1234
    assert e.description == "A test model"


def test_load_model_registry_bad_json(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text("{not valid json}")
    with pytest.raises(ModelRegistryError, match="Invalid JSON"):
        load_model_registry(registry)


def test_load_model_registry_not_array(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"id": "x"}))
    with pytest.raises(ModelRegistryError, match="must be a JSON array"):
        load_model_registry(registry)


def test_load_model_registry_missing_fields(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps([{"id": "x"}]))


    with pytest.raises(ModelRegistryError):
        load_model_registry(registry)


def test_get_model_path_success(tmp_path: Path):
    registry_dir = tmp_path / "models"
    registry_dir.mkdir()
    registry = registry_dir / "registry.json"
    model_file = registry_dir / "dummy.onnx"
    model_file.write_bytes(b"0" * 10)  # 10 bytes
    expected_hash = _sha256_of_file(model_file)
    data = [
        {
            "id": "dummy",
            "file": "dummy.onnx",
            "sha256": expected_hash,
            "licence": "BSD-3-Clause",
            "size_bytes": 10,
        }
    ]
    registry.write_text(json.dumps(data, indent=2))

    result_path = get_model_path("dummy", registry, registry_dir)
    assert result_path == model_file.resolve()


def test_get_model_path_not_found(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text("[]")
    with pytest.raises(ModelRegistryError, match="Model ID not found"):
        get_model_path("ghost", registry, tmp_path)


def test_get_model_path_wrong_size(tmp_path: Path):
    registry_dir = tmp_path / "models"
    registry_dir.mkdir()
    registry = registry_dir / "registry.json"
    model_file = registry_dir / "model.onnx"
    model_file.write_bytes(b"1" * 20)  # 20 bytes
    data = [
        {
            "id": "model",
            "file": "model.onnx",
            "sha256": _sha256_of_file(model_file),
            "licence": "MIT",
            "size_bytes": 999,  # wrong
        }
    ]
    registry.write_text(json.dumps(data, indent=2))
    with pytest.raises(ModelRegistryError, match="size mismatch"):
        get_model_path("model", registry, registry_dir)


def test_get_model_path_wrong_hash(tmp_path: Path):
    registry_dir = tmp_path / "models"
    registry_dir.mkdir()
    registry = registry_dir / "registry.json"
    model_file = registry_dir / "model.onnx"
    # 15 bytes of data
    model_file.write_bytes(b"2" * 15)  # 15 bytes
    data = [
        {
            "id": "model",
            "file": "model.onnx",
            "sha256": "a" * 64,  # wrong hash
            "licence": "MIT",
            "size_bytes": 15,  # correct size
        }
    ]
    registry.write_text(json.dumps(data, indent=2))
    with pytest.raises(ModelRegistryError, match="SHA-256 mismatch"):
        get_model_path("model", registry, registry_dir)


def test_get_model_path_absolute_file(tmp_path: Path):
    # file path in registry can be absolute
    registry = tmp_path / "registry.json"
    abs_model = tmp_path / "absolute.onnx"
    abs_model.write_bytes(b"abc")
    expected_hash = _sha256_of_file(abs_model)
    data = [
        {
            "id": "abs",
            "file": str(abs_model),
            "sha256": expected_hash,
            "licence": "MIT",
            "size_bytes": 3,
        }
    ]
    registry.write_text(json.dumps(data, indent=2))
    result = get_model_path("abs", registry, tmp_path)  # registry_dir ignored for abs
    assert result == abs_model.resolve()


def test_is_model_registered(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps([{"id": "yes", "file": "f.onnx", "sha256": "a"*64, "licence": "MIT", "size_bytes": 1}]))
    assert is_model_registered("yes", registry) is True
    assert is_model_registered("no", registry) is False