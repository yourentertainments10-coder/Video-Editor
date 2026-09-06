"""CLI entry point: `python -m poc <subcommand>`.

`run` wires the whole pipeline together (poc/pipeline.py) behind the
command shape docs/MVP.md Section 1 defines. `eval` (Step 9) is a
separate, isolated subcommand group for generating and scoring the
evaluation dataset -- it never edits video itself, only orchestrates
poc.evaluate. No editing logic lives in this file -- it only parses
arguments and calls into poc.pipeline / poc.evaluate, printing a summary
or a clear error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from poc import __version__
from poc.decide.style import Mood
from poc.evaluate import (
    EvaluationGenerationError,
    FootageSetSpec,
    MusicTrackSpec,
    build_blind_dataset,
    build_m2_report,
    compute_m1,
    format_report,
    generate_dataset,
    load_pairwise_ratings_csv,
    load_ratings_jsonl,
    append_ratings_jsonl,
    validate_pairwise_ratings,
)
from poc.evaluate.blind import AnswerKey
from poc.evaluate.manifest import PairManifest
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

    eval_parser = subparsers.add_parser("eval", help="Step 9: generate and score the evaluation dataset.")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")

    gen_parser = eval_subparsers.add_parser(
        "generate", help="Generate matched cutter/baseline pairs and the blind rater dataset."
    )
    gen_parser.add_argument(
        "--footage-set", action="append", required=True, dest="footage_sets", metavar="ID=DIR",
        help="One footage set, repeatable: --footage-set set1=./footage/set1",
    )
    gen_parser.add_argument(
        "--music", action="append", required=True, dest="music_tracks", metavar="ID=FILE",
        help="One music track, repeatable: --music track1=./music/a.mp3",
    )
    gen_parser.add_argument(
        "--mood", action="append", dest="moods", choices=["energetic", "calm"],
        help="Repeatable; defaults to both moods if omitted.",
    )
    gen_parser.add_argument("--duration", required=True, type=float, dest="duration_s")
    gen_parser.add_argument("--out", required=True, type=Path)
    gen_parser.add_argument("--seed", type=int, default=0, dest="base_seed", help="Base seed for baseline randomness.")
    gen_parser.add_argument("--blind-seed", type=int, default=0, help="Seed for the A/B blind assignment.")

    ingest_parser = eval_subparsers.add_parser("ingest", help="Validate and ingest a rating CSV into the raw log.")
    ingest_parser.add_argument("--out", required=True, type=Path, help="The evaluation run's output directory.")
    ingest_parser.add_argument("--ratings", required=True, type=Path, help="CSV file to ingest.")

    report_parser = eval_subparsers.add_parser("report", help="Compute metrics and print the evaluation report.")
    report_parser.add_argument("--out", required=True, type=Path, help="The evaluation run's output directory.")

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


def _parse_kv(items: list[str], flag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"error: {flag} expects ID=VALUE, got {item!r}")
        key, _, value = item.partition("=")
        result[key] = value
    return result


def _eval_generate_command(args: argparse.Namespace) -> int:
    footage_sets = [
        FootageSetSpec(footage_set_id=k, footage_dir=Path(v))
        for k, v in _parse_kv(args.footage_sets, "--footage-set").items()
    ]
    music_tracks = [
        MusicTrackSpec(music_id=k, music_path=Path(v))
        for k, v in _parse_kv(args.music_tracks, "--music").items()
    ]
    moods: list[Mood] = args.moods or ["energetic", "calm"]

    try:
        manifests = generate_dataset(
            footage_sets, music_tracks, moods, args.duration_s, args.out, base_seed=args.base_seed
        )
        build_blind_dataset(manifests, args.out, blind_seed=args.blind_seed)
    except EvaluationGenerationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"generated {len(manifests)} pair(s) -> {len(manifests) * 2} rendered output(s)")
    print(f"dataset for raters : {Path(args.out) / 'dataset'}")
    print(f"answer key (private): {Path(args.out) / 'answer_key.json'}")
    print(f"manifests           : {Path(args.out) / 'manifests'}")
    return 0


def _eval_ingest_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    manifests_dir = out_dir / "manifests"
    pair_ids = {p.stem for p in manifests_dir.glob("*.json")} if manifests_dir.is_dir() else set()

    try:
        new_ratings = load_pairwise_ratings_csv(args.ratings)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    result = validate_pairwise_ratings(new_ratings, expected_pair_ids=pair_ids)
    for err in result.unknown_pair_errors:
        print(f"warning: {err}", file=sys.stderr)
    for pid, rid in result.duplicate_keys:
        print(f"warning: duplicate rating for pair {pid!r} from rater {rid!r} (ignored)", file=sys.stderr)

    append_ratings_jsonl(result.valid_ratings, out_dir / "ratings.jsonl")
    print(f"ingested {len(result.valid_ratings)} rating(s) -> {out_dir / 'ratings.jsonl'}")
    if result.unknown_pair_errors or result.duplicate_keys:
        print(
            f"{len(result.unknown_pair_errors)} unknown-pair warning(s), "
            f"{len(result.duplicate_keys)} duplicate(s) -- see above",
            file=sys.stderr,
        )
    return 0


def _eval_report_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    answer_key_path = out_dir / "answer_key.json"
    if not answer_key_path.is_file():
        print(f"error: no answer key found at {answer_key_path} -- run `eval generate` first", file=sys.stderr)
        return 1

    answer_key = AnswerKey.load(answer_key_path)
    ratings = load_ratings_jsonl(out_dir / "ratings.jsonl")
    manifests_dir = out_dir / "manifests"
    manifests = [PairManifest.load(p) for p in sorted(manifests_dir.glob("*.json"))] if manifests_dir.is_dir() else []

    m1 = compute_m1(manifests) if manifests else None
    report = build_m2_report(ratings, answer_key, manifests, m1=m1)
    print(format_report(report))
    return 0 if report.m2.passed else 1


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    if args.command == "eval":
        if args.eval_command == "generate":
            return _eval_generate_command(args)
        if args.eval_command == "ingest":
            return _eval_ingest_command(args)
        if args.eval_command == "report":
            return _eval_report_command(args)
        print("poc eval: no subcommand given. Try `python -m poc eval --help`.")
        return 0
    print(f"poc {__version__}: no subcommand given. Try `python -m poc run --help`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
