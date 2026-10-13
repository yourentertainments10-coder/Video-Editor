"""Offline policy checker (docs/OFFLINE_POLICY.md, Section 4).

Fails when product code could reach an external AI or cloud service:

  * imports a hosted-AI SDK, cloud SDK, telemetry SDK, or network client;
  * declares one of those packages as a dependency (pyproject / requirements);
  * contains a hard-coded http(s) URL that is not loopback or allowlisted.

Standard library only, so it runs before dependencies are installed.

Usage:
    python tools/check_offline.py [repo_root]

Exit code 0 = clean, 1 = findings, 2 = usage error.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

# Top-level module names (or dotted prefixes) that product code must not import.
FORBIDDEN_MODULES: dict[str, str] = {
    # hosted AI / LLM SDKs and wrappers
    "anthropic": "hosted AI API",
    "openai": "hosted AI API",
    "google.generativeai": "hosted AI API",
    "google.genai": "hosted AI API",
    "vertexai": "hosted AI API",
    "cohere": "hosted AI API",
    "mistralai": "hosted AI API",
    "groq": "hosted AI API",
    "together": "hosted AI API",
    "replicate": "hosted AI API",
    "fireworks": "hosted AI API",
    "litellm": "multi-provider hosted AI proxy",
    "langchain": "framework that wires hosted AI providers",
    "langchain_core": "framework that wires hosted AI providers",
    "langchain_community": "framework that wires hosted AI providers",
    "llama_index": "framework that wires hosted AI providers",
    "huggingface_hub": "downloads models / calls inference API at runtime",
    "elevenlabs": "hosted TTS API",
    "deepgram": "hosted STT API",
    "assemblyai": "hosted STT API",
    # cloud SDKs
    "boto3": "cloud SDK",
    "botocore": "cloud SDK",
    "google.cloud": "cloud SDK",
    "azure": "cloud SDK",
    # telemetry
    "sentry_sdk": "remote telemetry",
    "posthog": "remote telemetry",
    "mixpanel": "remote telemetry",
    "analytics": "remote telemetry (segment)",
    # generic network clients (product code has no reason to fetch URLs)
    "requests": "network client",
    "httpx": "network client",
    "aiohttp": "network client",
    "urllib3": "network client",
    "urllib.request": "network client",
    "http.client": "network client",
    "ftplib": "network client",
    "smtplib": "network client",
}

# Distribution names that must not appear as dependencies.
FORBIDDEN_DISTRIBUTIONS: set[str] = {
    "anthropic", "openai", "google-generativeai", "google-genai",
    "google-cloud-aiplatform", "cohere", "mistralai", "groq", "together",
    "replicate", "fireworks-ai", "litellm", "langchain", "langchain-core",
    "langchain-community", "langchain-openai", "langchain-anthropic",
    "llama-index", "huggingface-hub", "elevenlabs", "deepgram-sdk",
    "assemblyai", "boto3", "botocore", "azure-ai-openai", "sentry-sdk",
    "posthog", "mixpanel", "analytics-python", "requests", "httpx",
    "aiohttp",
}

# TestClient's test-only transport is allowed as a dev extra; product runtime code never imports httpx.
ALLOWED_DEV_DISTRIBUTIONS: dict[str, str] = {"httpx": "FastAPI TestClient transport for tests only"}

# Directories never scanned (environments, generated output, non-product code).
SKIP_DIRS: set[str] = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules", "build",
    "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache", "docs", "tests",
    "experiments", "project", "examples",
}

LOOPBACK_URL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])([:/]|$)")
ANY_URL = re.compile(r"https?://[^\s'\"<>]+")

# Literal URLs allowed in product code, e.g. licence links in docstrings.
ALLOWED_URL_PREFIXES: tuple[str, ...] = ()

_DIST_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _forbidden_reason(module: str) -> str | None:
    for name, reason in FORBIDDEN_MODULES.items():
        if module == name or module.startswith(name + "."):
            return reason
    return None


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        yield path


def scan_python(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    self_path = Path(__file__).resolve()
    for path in _iter_py_files(root):
        if path.resolve() == self_path:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            findings.append(Finding(rel, getattr(exc, "lineno", 0) or 0,
                                    "PARSE_ERROR", str(exc)))
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for module in modules:
                reason = _forbidden_reason(module)
                if reason:
                    findings.append(Finding(rel, node.lineno, "FORBIDDEN_IMPORT",
                                            f"{module} ({reason})"))
                    break
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for url in ANY_URL.findall(node.value):
                    if LOOPBACK_URL.match(url) or url.startswith(ALLOWED_URL_PREFIXES):
                        continue
                    findings.append(Finding(rel, node.lineno, "REMOTE_URL", url))
    return findings


def _normalize_dist(spec: str) -> str | None:
    match = _DIST_NAME.match(spec)
    if not match:
        return None
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def scan_dependencies(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    specs: list[tuple[str, str, bool]] = []  # (source file, spec, is development extra)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib is not None:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        for group_name, group in project.get("optional-dependencies", {}).items():
            specs += [("pyproject.toml", s, group_name == "dev") for s in group]
        specs += [("pyproject.toml", s, False) for s in project.get("dependencies", [])]

    for req in sorted(root.glob("requirements*.txt")):
        for raw in req.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                specs.append((req.name, line, False))

    for source, spec, is_dev in specs:
        name = _normalize_dist(spec)
        if name in FORBIDDEN_DISTRIBUTIONS and not (is_dev and name in ALLOWED_DEV_DISTRIBUTIONS):
            findings.append(Finding(source, 0, "FORBIDDEN_DEPENDENCY", spec))
    return findings


def check(root: Path) -> list[Finding]:
    return scan_dependencies(root) + scan_python(root)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: python tools/check_offline.py [repo_root]", file=sys.stderr)
        return 2
    root = Path(argv[1] if len(argv) == 2 else Path(__file__).resolve().parents[1])
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    findings = check(root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"\nOFFLINE POLICY: {len(findings)} finding(s). See docs/OFFLINE_POLICY.md.")
        return 1
    print("OFFLINE POLICY: OK (no external AI/cloud imports, dependencies, or remote URLs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
