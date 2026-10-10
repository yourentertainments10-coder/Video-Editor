"""Enforces docs/OFFLINE_POLICY.md: no external AI or cloud access in product code."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_checker():
    path = REPO_ROOT / "tools" / "check_offline.py"
    spec = importlib.util.spec_from_file_location("check_offline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_offline"] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_repository_is_offline_clean():
    findings = checker.check(REPO_ROOT)
    assert findings == [], "\n".join(str(f) for f in findings)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detects_hosted_ai_imports(tmp_path):
    _write(tmp_path, "poc/a.py", "import anthropic\n")
    _write(tmp_path, "poc/b.py", "from openai import OpenAI\n")
    _write(tmp_path, "poc/c.py", "from google import genai\n")
    rules = sorted((f.path, f.rule) for f in checker.check(tmp_path))
    assert rules == [
        ("poc/a.py", "FORBIDDEN_IMPORT"),
        ("poc/b.py", "FORBIDDEN_IMPORT"),
        ("poc/c.py", "FORBIDDEN_IMPORT"),
    ]


def test_detects_network_clients_and_remote_urls(tmp_path):
    _write(tmp_path, "poc/net.py",
           "import urllib.request\nURL = 'https://api.example.com/v1'\n")
    rules = sorted(f.rule for f in checker.check(tmp_path))
    assert rules == ["FORBIDDEN_IMPORT", "REMOTE_URL"]


def test_allows_loopback_and_local_libraries(tmp_path):
    _write(tmp_path, "poc/ok.py",
           "import cv2, librosa, numpy, pydantic, json, subprocess\n"
           "API = 'http://127.0.0.1:8000/api'\nUI = 'http://localhost:5173'\n")
    assert checker.check(tmp_path) == []


def test_detects_forbidden_dependencies(tmp_path):
    _write(tmp_path, "pyproject.toml",
           '[project]\nname = "x"\nversion = "0"\n'
           'dependencies = ["numpy>=1.26", "Anthropic==1.0"]\n'
           '[project.optional-dependencies]\ndev = ["pytest", "litellm"]\n')
    _write(tmp_path, "requirements.txt", "opencv-python-headless\nrequests>=2  # no\n")
    details = sorted(f.detail for f in checker.check(tmp_path)
                     if f.rule == "FORBIDDEN_DEPENDENCY")
    assert details == ["Anthropic==1.0", "litellm", "requests>=2"]


def test_tests_docs_and_experiments_are_not_scanned(tmp_path):
    _write(tmp_path, "tests/test_x.py", "import requests\n")
    _write(tmp_path, "experiments/e.py", "import openai\n")
    _write(tmp_path, ".venv/lib/site.py", "import boto3\n")
    assert checker.check(tmp_path) == []
