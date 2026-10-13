#!/usr/bin/env python3
"""Run the existing local auto-cut pipeline, then render its timeline at an export size."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

from poc import __main__ as poc_cli
from poc.ingest.normalize import normalize_asset
from poc.project_assets import ProjectAssetLibrary
from poc.render.editor import EditorRenderer
from poc.render.export import get_profile
from poc.editor_bridge import legacy_to_editor
from poc.timeline import EditorSettings, MediaAsset, Timeline


MEDIA_KINDS = {
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video",
    ".avi": "video", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".webp": "image", ".bmp": "image", ".mp3": "audio", ".wav": "audio",
    ".m4a": "audio", ".flac": "audio", ".aac": "audio",
}


def _probe_files(footage_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(p for p in footage_dir.rglob("*")
                       if p.is_file() and p.suffix.lower() in MEDIA_KINDS):
        normalized = normalize_asset(MediaAsset(
            asset_id=f"probe_{len(rows)}", kind=MEDIA_KINDS[path.suffix.lower()],
            uri=str(path.resolve()),
        ))
        rows.append({
            "file": str(path.resolve()),
            "width": normalized.width,
            "height": normalized.height,
            "fps": normalized.fps,
            "vfr": normalized.is_vfr,
            "rotation": normalized.rotation_degrees,
            "hdr": normalized.is_hdr,
            "interlaced": normalized.needs_deinterlace,
            "has_audio": normalized.has_audio,
            "duration_ms": normalized.duration_ms,
        })
    return rows


def _print_probe_table(rows: list[dict]) -> None:
    headings = ("file", "WxH", "fps", "VFR", "rotation", "HDR", "interlaced", "has_audio", "duration")
    print(" | ".join(headings))
    for row in rows:
        size = f"{row['width']}x{row['height']}" if row["width"] else "-"
        fps = f"{row['fps']:.3f}" if row["fps"] else "-"
        duration = f"{row['duration_ms'] / 1000:.3f}s" if row["duration_ms"] else "-"
        print(" | ".join((Path(row["file"]).name, size, fps, str(row["vfr"]),
                         str(row["rotation"]), str(row["hdr"]), str(row["interlaced"]),
                         str(row["has_audio"]), duration)))


def _timeline_with_library(legacy: Timeline, library: ProjectAssetLibrary,
                           imported: dict[Path, object], base_dir: Path,
                           music_path: Path, profile: str):
    source_to_asset_id = {path.resolve(): item.asset_id for path, item in imported.items()}
    for clip in legacy.clips:
        source = Path(clip.source.path)
        resolved = source if source.is_absolute() else base_dir / source
        source_to_asset_id[clip.source.path] = imported[resolved.resolve()].asset_id
    export = get_profile(profile)
    return legacy_to_editor(
        legacy, library=library, source_to_asset_id=source_to_asset_id,
        music_asset_id=imported[music_path.resolve()].asset_id,
        settings=EditorSettings(fps_num=30, fps_den=1, width=export.width, height=export.height),
    )


def _markdown(report: dict) -> str:
    rows = report.get("probe_table", [])
    lines = [
        "# Smoke render report", "",
        f"- Profile: `{report['profile']}`",
        f"- Video clips: {report.get('number_of_clips', 'n/a')}",
        f"- Timeline duration: {report.get('timeline_duration_seconds', 'n/a')} s",
        f"- Render seconds: {report.get('render_seconds', 'n/a')}",
        f"- Realtime factor: {report.get('realtime_factor', 'n/a')}",
        "", "## Probes", "",
        "| File | WxH | FPS | VFR | Rotation | HDR | Interlaced | Audio | Duration |",
        "|---|---:|---:|---|---:|---|---|---|---:|",
    ]
    for row in rows:
        filename = Path(row["file"]).name
        size = f"{row['width']}x{row['height']}" if row["width"] else "-"
        fps = f"{row['fps']:.3f}" if row["fps"] else "-"
        duration = f"{row['duration_ms'] / 1000:.3f}s" if row["duration_ms"] else "-"
        lines.append(f"| {filename} | {size} | {fps} | {row['vfr']} | {row['rotation']} | "
                     f"{row['hdr']} | {row['interlaced']} | {row['has_audio']} | {duration} |")
    verify = report.get("verify_report")
    lines.extend(["", "## Verification", ""])
    if verify is None:
        lines.append("Render verification was not reached.")
    else:
        lines.extend([f"- OK: {verify['ok']}", f"- Problems: {json.dumps(verify['problems'], ensure_ascii=False)}",
                      f"- Measured: `{json.dumps(verify['measured'], ensure_ascii=False, sort_keys=True)}`"])
    if report.get("exception"):
        lines.extend(["", "## Exception", "", "```text", report["exception"], "```"])
    return "\n".join(lines) + "\n"


def run_smoke(footage_dir: Path, music: Path, out_dir: Path, *, duration: float = 30,
              profile: str = "youtube_1080p") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "profile": profile, "number_of_clips": None, "timeline_duration_seconds": None,
        "render_seconds": None, "realtime_factor": None, "verify_report": None,
        "ok": None, "exception": None, "probe_table": [],
    }
    try:
        rows = _probe_files(footage_dir)
        report["probe_table"] = rows
        (out_dir / "probe.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _print_probe_table(rows)
        if not rows:
            raise ValueError(f"no supported media files found in {footage_dir}")

        # Invoke the exact CLI entry point used by `python -m poc run`.
        legacy_output = out_dir / "auto_cut_legacy.mp4"
        argv = ["run", "--footage", str(footage_dir.resolve()), "--music", str(music.resolve()),
                "--mood", "energetic", "--duration", str(duration), "--output", str(legacy_output),
                "--allow-development-inputs"]
        captured_out, captured_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            status = poc_cli.main(argv)
        cli_output = captured_out.getvalue() + captured_err.getvalue()
        if cli_output:
            print(cli_output, end="")
        if status != 0:
            raise RuntimeError(f"existing run CLI returned {status}: {cli_output.strip()}")

        legacy = Timeline.load(out_dir / "timeline.json")
        base_dir = Path(os.path.commonpath([str(footage_dir.resolve()), str(music.resolve().parent)]))
        library = ProjectAssetLibrary.create(out_dir / "project", project_id=f"smoke_{profile}")
        imported = {}
        for row in rows:
            source = Path(row["file"]).resolve()
            if source not in imported:
                imported[source] = library.import_asset(source)
        music_source = music.resolve()
        if music_source not in imported:
            imported[music_source] = library.import_asset(music_source)
        editor_timeline = _timeline_with_library(legacy, library, imported, base_dir, music_source, profile)
        render_path = out_dir / f"smoke_{profile}.mp4"
        renderer = EditorRenderer()
        plan = renderer.compile(editor_timeline, library)

        def progress(fraction: float, message: str) -> None:
            print(f"render {fraction * 100:5.1f}%: {message}", flush=True)

        started = time.perf_counter()
        result = renderer.render(plan, out=render_path, progress=progress)
        render_seconds = time.perf_counter() - started
        video_seconds = result.duration_ms / 1000
        report.update({
            "number_of_clips": len(legacy.clips),
            "timeline_duration_seconds": video_seconds,
            "render_seconds": render_seconds,
            "realtime_factor": render_seconds / video_seconds if video_seconds else None,
            "verify_report": {
                "ok": result.report.ok,
                "problems": result.report.problems,
                "measured": result.report.measured,
            },
            "ok": result.report.ok,
        })
    except Exception as exc:
        report["exception"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        print(report["exception"], file=sys.stderr)
    finally:
        (out_dir / "smoke_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / "smoke_report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footage", required=True, type=Path)
    parser.add_argument("--music", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--profile", choices=("youtube_1080p", "reels_9x16"), default="youtube_1080p")
    args = parser.parse_args(argv)
    report = run_smoke(args.footage, args.music, args.out, duration=args.duration, profile=args.profile)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
