"""Small FFmpeg process helpers with atomic output and cooperative cancellation."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable


def run_process(
    cmd: list[str], *, cancel: Callable[[], bool] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancel is not None and cancel():
        raise InterruptedError("render cancelled")
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(cwd) if cwd is not None else None,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel():
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                raise InterruptedError("render cancelled")
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def run_ffmpeg_atomic(
    cmd: list[str],
    out_path: Path,
    *,
    cancel: Callable[[], bool] | None = None,
    label: str = "FFmpeg render",
    cwd: str | Path | None = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
    command = list(cmd)
    command[-1] = str(partial)
    try:
        result = run_process(command, cancel=cancel, cwd=cwd)
        if result.returncode:
            raise RuntimeError(f"{label} failed: {result.stderr}")
        os.replace(partial, out_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return out_path
