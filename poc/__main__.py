"""CLI entry point: `python -m poc <subcommand>`.

`run` is the only subcommand: it wires the whole pipeline together
(poc/pipeline.py) behind the command shape docs/MVP.md Section 1 defines.
No editing logic lives here -- this file only parses arguments, calls
poc.pipeline.run_pipeline, and prints a summary or a clear error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from poc import __version__
from poc.pipeline import PipelineError, run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poc", description="MVP v0: deterministic, beat-synced automatic video editing."
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run", help="Analyze footage and music, cut a Timeline, render the final MP4."
    )
    run_parser.add_argument("--footage", required=True, type=Path, help="Folder of .mp4/.mov footage files.")
    run_parser.add_argument("--music", required=True, type=Path, help="Music file (any FFmpeg-decodable format).")
    run_parser.add_argument("--mood", required=True, choices=["energetic", "calm"])
    run_parser.add_argument(
        "--duration", required=True, type=float, dest="duration_s", help="Target duration in seconds."
    )
    run_parser.add_argument("--output", required=True, type=Path, help="Path to write the final MP4.")
    run_parser.add_argument(
        "--baseline", action="store_true", help="Produce the random control instead of the scored cut."
    )
    run_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for --baseline (default 0 if omitted)."
    )
    run_parser.add_argument(
        "--bpm-hint", type=float, default=None, dest="bpm_hint", help="Tempo prior for music analysis."
    )
    run_parser.add_argument(
        "--music-offset-s",
        type=float,
        default=None,
        dest="music_offset_s",
        help="Start from the first beat at or after this many seconds into the track.",
    )
    return parser


def _run_command(args: argparse.Namespace) -> int:
    try:
        result = run_pipeline(
            footage_dir=args.footage,
            music_path=args.music,
            mood=args.mood,
            duration_s=args.duration_s,
            output_path=args.output,
            baseline=args.baseline,
            seed=args.seed,
            bpm_hint=args.bpm_hint,
            music_offset_s=args.music_offset_s,
        )
    except PipelineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tl = result.timeline
    footage_dir = next(iter(result.footage_map_paths.values())).parent if result.footage_map_paths else "-"
    print(f"generator    : {tl.provenance.generator}")
    print(f"mood         : {args.mood}")
    print(f"duration     : {tl.duration_frames} frames ({tl.duration_ms / 1000:.2f}s) at {tl.settings.fps} fps")
    print(f"clips        : {len(tl.clips)}")
    print(f"music map    : {result.music_map_path}")
    print(f"footage maps : {len(result.footage_map_paths)} file(s) under {footage_dir}")
    print(f"timeline     : {result.timeline_path}")
    print(f"output       : {result.output_path}")
    return 0


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    print(f"poc {__version__}: no subcommand given. Try `python -m poc run --help`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
