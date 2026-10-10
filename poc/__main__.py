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

import imageio_ffmpeg
from poc import __version__
from poc.decide.style import Mood
from poc.execute import probe
from poc.evaluate import (
    EvaluationGenerationError,
    FootageSetSpec,
    MusicTrackSpec,
    build_blind_dataset,
    build_m2_report,
    compute_m1,
    format_report,
    generate_dataset,
    generate_mood_dataset,
    load_pairwise_ratings_csv,
    load_ratings_jsonl,
    load_mood_identification_ratings_csv,
    load_mood_identification_ratings_jsonl,
    load_output_ratings_csv,
    load_output_ratings_jsonl,
    append_ratings_jsonl,
    append_mood_identification_ratings_jsonl,
    append_output_ratings_jsonl,
    validate_pairwise_ratings,
    validate_mood_identification_ratings,
    validate_output_ratings,
)
from poc.evaluate.blind import AnswerKey, MoodAnswerKey
from poc.evaluate.manifest import PairManifest, is_git_worktree_dirty
from poc.pipeline import FOOTAGE_EXTENSIONS, PipelineError, run_pipeline

MIN_MVP_DURATION_S = 15.0
MAX_MVP_DURATION_S = 30.0
MIN_FOOTAGE_FILES = 5
MAX_FOOTAGE_FILES = 20
MIN_CLIP_DURATION_S = 5.0
MAX_CLIP_DURATION_S = 180.0
MIN_FOOTAGE_COVERAGE = 4.0
SUPPORTED_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}


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
    run_parser.add_argument("--output", "--out", required=True, type=Path, help="Path to write the final MP4.")
    run_parser.add_argument(
        "--allow-development-inputs", action="store_true",
        help="Allow undersized/short synthetic inputs; not valid for an MVP evaluation.",
    )
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
    gen_parser.add_argument(
        "--phase", choices=["development", "preliminary", "official"], default="official",
        help="Use development-sized inputs, the frozen 3x2 diagnostic grid, or the 5x3 MVP grid.",
    )
    gen_parser.add_argument("--seed", type=int, default=0, dest="base_seed", help="Base seed for baseline randomness.")
    gen_parser.add_argument("--blind-seed", type=int, default=0, help="Seed for the A/B blind assignment.")

    ingest_parser = eval_subparsers.add_parser("ingest", help="Validate and ingest a rating CSV into the raw log.")
    ingest_parser.add_argument("--out", required=True, type=Path, help="The evaluation run's output directory.")
    ingest_parser.add_argument("--ratings", required=True, type=Path, help="CSV file to ingest.")
    ingest_parser.add_argument(
        "--metric", choices=["M2", "M3", "M4", "M5"], default="M2",
        help="Rating instrument represented by the CSV (default: M2).",
    )

    report_parser = eval_subparsers.add_parser("report", help="Compute metrics and print the evaluation report.")
    report_parser.add_argument("--out", required=True, type=Path, help="The evaluation run's output directory.")
    report_parser.add_argument(
        "--expected-rater", action="append", dest="expected_raters",
        help="Declared rater ID; repeat once per expected rater to check completeness.",
    )

    return parser


def _run_command(args: argparse.Namespace) -> int:
    output_error = _prepare_output_dir(args.output.parent)
    if output_error:
        print(f"error: {output_error}", file=sys.stderr)
        return 1
    validation_error = _validate_media_inputs(
        args.duration_s,
        [("footage", args.footage)],
        [("music", args.music)],
        allow_development=args.allow_development_inputs,
    )
    if validation_error:
        print(f"error: {validation_error}", file=sys.stderr)
        return 1
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
    print(f"contact sheet: {result.contact_sheet_path}")
    print(f"output       : {result.output_path}")
    return 0


def _parse_kv(items: list[str], flag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"error: {flag} expects ID=VALUE, got {item!r}")
        key, _, value = item.partition("=")
        if not key or not value:
            raise SystemExit(f"error: {flag} expects non-empty ID=VALUE, got {item!r}")
        if key in result:
            raise SystemExit(f"error: duplicate ID {key!r} for {flag}")
        result[key] = value
    return result


def _prepare_output_dir(path: Path) -> str | None:
    try:
        if path.exists() and not path.is_dir():
            return f"output parent exists but is not a directory: {path}"
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"cannot create output directory {path}: {error}"
    return None


def _validate_media_inputs(
    duration_s: float,
    footage_sets: list[tuple[str, Path]],
    music_tracks: list[tuple[str, Path]],
    *,
    allow_development: bool,
    evaluation_phase: str | None = None,
) -> str | None:
    if allow_development:
        return None
    if not MIN_MVP_DURATION_S <= duration_s <= MAX_MVP_DURATION_S:
        return f"duration must be {MIN_MVP_DURATION_S:.0f}-{MAX_MVP_DURATION_S:.0f} seconds for MVP inputs"

    for set_id, footage_dir in footage_sets:
        if not footage_dir.is_dir():
            return f"footage set {set_id!r} is not a directory: {footage_dir}"
        video_files = sorted(
            path for path in footage_dir.iterdir()
            if path.is_file() and path.suffix.lower() in FOOTAGE_EXTENSIONS
        )
        minimum_files, maximum_files = (
            (8, 15) if evaluation_phase in {"preliminary", "official"}
            else (MIN_FOOTAGE_FILES, MAX_FOOTAGE_FILES)
        )
        if not minimum_files <= len(video_files) <= maximum_files:
            return (
                f"footage set {set_id!r} has {len(video_files)} supported videos; "
                f"{evaluation_phase or 'MVP'} requires {minimum_files}-{maximum_files} (.mp4/.mov)"
            )
        total_duration_ms = 0
        for video_path in video_files:
            try:
                video_info = probe(video_path, imageio_ffmpeg.get_ffmpeg_exe())
            except Exception as error:
                return f"could not probe video {video_path}: {error}"
            if not video_info.has_video:
                return f"input has no video stream: {video_path}"
            if not MIN_CLIP_DURATION_S * 1000 <= video_info.duration_ms <= MAX_CLIP_DURATION_S * 1000:
                return (
                    f"video {video_path} is {video_info.duration_ms / 1000:.1f}s; "
                    f"supported clips are {MIN_CLIP_DURATION_S:.0f}-{MAX_CLIP_DURATION_S:.0f}s"
                )
            total_duration_ms += video_info.duration_ms
        minimum_ms = duration_s * MIN_FOOTAGE_COVERAGE * 1000
        if total_duration_ms < minimum_ms:
            return (
                f"footage set {set_id!r} totals {total_duration_ms / 1000:.1f}s; "
                f"MVP requires at least {MIN_FOOTAGE_COVERAGE:.0f}x the target "
                f"({minimum_ms / 1000:.1f}s for a {duration_s:g}s edit)"
            )
        if evaluation_phase in {"preliminary", "official"}:
            if not 180_000 <= total_duration_ms <= 600_000:
                return (
                    f"evaluation footage set {set_id!r} totals {total_duration_ms / 1000:.1f}s; "
                    "the protocol requires 180-600 seconds"
                )

    seen_music: set[Path] = set()
    for track_id, music_path in music_tracks:
        resolved_path = music_path.resolve()
        if resolved_path in seen_music:
            continue
        seen_music.add(resolved_path)
        if not resolved_path.is_file():
            return f"music track {track_id!r} does not exist: {resolved_path}"
        if resolved_path.suffix.lower() not in SUPPORTED_MUSIC_EXTENSIONS:
            return (
                f"unsupported music format {resolved_path.suffix!r}; "
                f"supported extensions are {', '.join(sorted(SUPPORTED_MUSIC_EXTENSIONS))}"
            )
        try:
            music_info = probe(resolved_path, imageio_ffmpeg.get_ffmpeg_exe())
        except Exception as error:
            return f"could not probe music track {resolved_path}: {error}"
        if not music_info.has_audio:
            return f"input has no audio stream: {resolved_path}"
        minimum_music_ms = (duration_s + 10.0) * 1000
        if music_info.duration_ms < minimum_music_ms:
            return (
                f"music track {resolved_path} is {music_info.duration_ms / 1000:.1f}s; "
                f"MVP requires at least {duration_s + 10.0:.1f}s"
            )
    return None


def _eval_generate_command(args: argparse.Namespace) -> int:
    if args.phase == "official" and is_git_worktree_dirty():
        print(
            "error: official evaluation generation requires a clean tracked worktree; "
            "commit or otherwise resolve source changes first. Use --phase preliminary "
            "only for diagnostic data.",
            file=sys.stderr,
        )
        return 1
    output_error = _prepare_output_dir(args.out)
    if output_error:
        print(f"error: {output_error}", file=sys.stderr)
        return 1
    footage_sets = [
        FootageSetSpec(footage_set_id=k, footage_dir=Path(v))
        for k, v in _parse_kv(args.footage_sets, "--footage-set").items()
    ]
    music_tracks = [
        MusicTrackSpec(music_id=k, music_path=Path(v))
        for k, v in _parse_kv(args.music_tracks, "--music").items()
    ]
    moods: list[Mood] = args.moods or ["energetic", "calm"]

    if args.phase == "preliminary" and (len(footage_sets), len(music_tracks), set(moods)) != (
        3, 2, {"energetic", "calm"}
    ):
        print("error: preliminary phase requires 3 footage sets, 2 tracks, and both moods", file=sys.stderr)
        return 1
    if args.phase == "official" and (len(footage_sets), len(music_tracks), set(moods)) != (
        5, 3, {"energetic", "calm"}
    ):
        print("error: official phase requires 5 footage sets, 3 tracks, and both moods", file=sys.stderr)
        return 1
    validation_error = _validate_media_inputs(
        args.duration_s,
        [(spec.footage_set_id, spec.footage_dir) for spec in footage_sets],
        [(spec.music_id, spec.music_path) for spec in music_tracks],
        allow_development=args.phase == "development",
        evaluation_phase=args.phase,
    )
    if validation_error:
        print(f"error: {validation_error}", file=sys.stderr)
        return 1

    try:
        manifests = generate_dataset(
            footage_sets, music_tracks, moods, args.duration_s, args.out,
            base_seed=args.base_seed, evaluation_phase=args.phase,
        )
        mood_manifests = generate_mood_dataset(
            footage_sets, music_tracks, args.duration_s, args.out,
            blind_seed=args.blind_seed,
            evaluation_phase=args.phase,
            pair_manifests=manifests,
        )
        build_blind_dataset(manifests, args.out, blind_seed=args.blind_seed)
    except EvaluationGenerationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"generated {len(manifests)} pair(s) -> {len(manifests) * 2} rendered output(s)")
    print(f"generated {len(mood_manifests)} mood-identification pair(s)")
    print(f"dataset for raters : {Path(args.out) / 'dataset'}")
    print(f"answer key (private): {Path(args.out) / 'answer_key.json'}")
    print(f"manifests           : {Path(args.out) / 'manifests'}")
    return 0


def _eval_ingest_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    manifests_dir = out_dir / "manifests"
    pair_ids = {p.stem for p in manifests_dir.glob("*.json")} if manifests_dir.is_dir() else set()

    try:
        if args.metric == "M2":
            new_ratings = load_pairwise_ratings_csv(args.ratings)
            log_path = out_dir / "ratings.jsonl"
            prior = validate_pairwise_ratings(
                load_ratings_jsonl(log_path), expected_pair_ids=pair_ids
            ).valid_ratings
            result = validate_pairwise_ratings(prior + new_ratings, expected_pair_ids=pair_ids)
            errors = result.unknown_pair_errors
            duplicate_keys = result.duplicate_keys
            append_ratings_jsonl(new_ratings, log_path)
        elif args.metric == "M3":
            new_ratings = load_mood_identification_ratings_csv(args.ratings)
            log_path = out_dir / "m3_ratings.jsonl"
            expected_ids = set(MoodAnswerKey.load(out_dir / "mood_answer_key.json").assignments)
            prior = validate_mood_identification_ratings(
                load_mood_identification_ratings_jsonl(log_path), expected_ids
            ).valid_ratings
            result = validate_mood_identification_ratings(prior + new_ratings, expected_ids)
            errors = result.unknown_subject_errors
            duplicate_keys = result.duplicate_keys
            append_mood_identification_ratings_jsonl(new_ratings, log_path)
        else:
            new_ratings = load_output_ratings_csv(args.ratings, args.metric)
            log_path = out_dir / f"{args.metric.lower()}_ratings.jsonl"
            expected_ids = {f"{pair_id}_{side}" for pair_id in pair_ids for side in ("A", "B")}
            prior = validate_output_ratings(
                load_output_ratings_jsonl(log_path), expected_ids, metric=args.metric
            ).valid_ratings
            result = validate_output_ratings(
                prior + new_ratings, expected_ids, metric=args.metric
            )
            errors = result.unknown_subject_errors
            duplicate_keys = result.duplicate_keys
            append_output_ratings_jsonl(new_ratings, log_path)
    except (ValueError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for error in errors:
        print(f"warning: {error}", file=sys.stderr)
    for subject_id, rater_id in duplicate_keys:
        print(
            f"warning: duplicate {args.metric} rating for {subject_id!r} "
            f"from rater {rater_id!r} (first rating is used in reports)", file=sys.stderr,
        )
    print(f"ingested {len(new_ratings)} {args.metric} submission(s) -> {log_path}")
    if errors or duplicate_keys:
        print(
            f"{len(errors)} unknown-subject warning(s), {len(duplicate_keys)} duplicate(s) -- see above",
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
    mood_key_path = out_dir / "mood_answer_key.json"
    mood_answer_key = MoodAnswerKey.load(mood_key_path) if mood_key_path.is_file() else None
    ratings = load_ratings_jsonl(out_dir / "ratings.jsonl")
    m3_ratings = load_mood_identification_ratings_jsonl(out_dir / "m3_ratings.jsonl")
    m4_ratings = load_output_ratings_jsonl(out_dir / "m4_ratings.jsonl")
    m5_ratings = load_output_ratings_jsonl(out_dir / "m5_ratings.jsonl")
    manifests_dir = out_dir / "manifests"
    manifests = [PairManifest.load(p) for p in sorted(manifests_dir.glob("*.json"))] if manifests_dir.is_dir() else []

    m1 = compute_m1(manifests, expected_pair_ids=set(answer_key.assignments))
    report = build_m2_report(
        ratings,
        answer_key,
        manifests,
        m1=m1,
        mood_answer_key=mood_answer_key,
        m3_ratings=m3_ratings,
        m4_ratings=m4_ratings,
        m5_ratings=m5_ratings,
        expected_rater_ids=set(args.expected_raters) if args.expected_raters else None,
    )
    print(format_report(report))
    if not report.official_verdict_eligible:
        return 2
    return 0 if "hypothesis validated" in report.verdict else 1


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
