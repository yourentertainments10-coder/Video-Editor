"""poc/evaluate/metrics.py: the MVP.md Section 10 metrics, exactly.

Every threshold below is copied verbatim from docs/MVP.md's "Metrics and
pass thresholds" table -- none is invented, none is tuned. Where MVP.md
does not specify a method (M2's confidence interval), the simplest
standard, well-known statistical choice is used and disclosed, not a bespoke
one: the Wilson score interval, chosen because it behaves sensibly at the
small sample sizes (150 judgments) and extreme proportions this
experiment can produce, unlike a naive normal approximation.

| # | Claim | Threshold (docs/MVP.md, verbatim) |
|---|-------|-------------------------------------|
| M1 | mechanical | validator passes, on-beat within 1 frame, render succeeds, wall time under 2 min first run -- 100% of 60 outputs |
| M2 | selection quality (the core hypothesis) | system preferred in at least 70% of 150 judgments |
| M3 | style differentiation | at least 80% correct mood identification |
| M4 | usability (the honest gate) | "yes" from at least 3 of 5 raters on at least 50% of outputs |
| M5 | perceived sync | mean rating at least 4.0 (of 5) |

This module computes results from data that already exists (manifests,
Timelines, ratings); it never decodes media, never calls FFmpeg, and
never touches poc.decide/poc.execute/poc.perceive.
"""
from __future__ import annotations

import math
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from poc.evaluate.blind import AnswerKey
from poc.evaluate.manifest import PairManifest
from poc.evaluate.ratings import MoodIdentificationRating, OutputRating, PairwiseRating
from poc.execute.render import probe, resolve_media_path
from poc.timeline.schema import Timeline
from poc.timeline.validate import validate

__all__ = [
    "M1_WALL_TIME_FIRST_RUN_S",
    "M2_PREFERENCE_THRESHOLD",
    "M3_MOOD_ID_THRESHOLD",
    "M4_MIN_RATERS_YES",
    "M4_MIN_OUTPUT_FRACTION",
    "M5_MEAN_THRESHOLD",
    "M1Result",
    "M2Result",
    "M3Result",
    "M4Result",
    "M5Result",
    "compute_m1",
    "compute_m2",
    "compute_m3",
    "compute_m4",
    "compute_m5",
    "wilson_interval",
    "decide",
]

# docs/MVP.md Section 10, verbatim thresholds
M1_WALL_TIME_FIRST_RUN_S = 120.0  # "under two minutes on the first run"
M2_PREFERENCE_THRESHOLD = 0.70
M3_MOOD_ID_THRESHOLD = 0.80
M4_MIN_RATERS_YES = 3  # "of 5"
M4_MIN_OUTPUT_FRACTION = 0.50
M5_MEAN_THRESHOLD = 4.0


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion. Not specified
    by MVP.md; added because requirement 7 asks for a confidence interval
    "if specified or appropriate" and this is the standard choice for a
    proportion at this sample size."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ---------------------------------------------------------------------------
# M1: mechanical -- no human rating needed, computed from manifests/Timelines
# ---------------------------------------------------------------------------


class M1Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_outputs: int
    passing_outputs: int
    failures: list[str]

    @property
    def pass_rate(self) -> float:
        return self.passing_outputs / self.total_outputs if self.total_outputs else 0.0

    @property
    def passed(self) -> bool:
        return self.total_outputs > 0 and self.passing_outputs == self.total_outputs


def compute_m1(
    manifests: list[PairManifest],
    expected_pair_ids: set[str] | None = None,
    *,
    ffmpeg_exe: str | None = None,
) -> M1Result:
    """Verify each expected timeline and rendered artifact, including
    source/media bounds and the actual output's streams and duration."""
    import imageio_ffmpeg

    ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    failures: list[str] = []
    passing = 0
    manifest_by_id = {manifest.pair_id: manifest for manifest in manifests}
    pair_ids = expected_pair_ids if expected_pair_ids is not None else set(manifest_by_id)
    probe_cache = {}

    def media_probe(path: Path):
        resolved = path.resolve()
        if resolved not in probe_cache:
            probe_cache[resolved] = probe(resolved, ffmpeg_exe)
        return probe_cache[resolved]

    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def check_artifact(
        manifest: PairManifest, label: str, timeline_path: str, render_path: str, render_seconds: float
    ) -> bool:
        ok = True
        timeline_file = Path(timeline_path)
        video_file = Path(render_path)
        if not timeline_file.is_file():
            failures.append(f"{manifest.pair_id}/{label}: timeline artifact is missing: {timeline_file}")
            return False
        if not video_file.is_file() or video_file.stat().st_size == 0:
            failures.append(f"{manifest.pair_id}/{label}: rendered video is missing or empty: {video_file}")
            return False
        try:
            timeline = Timeline.load(timeline_file)
        except Exception as error:
            failures.append(f"{manifest.pair_id}/{label}: timeline could not be loaded: {error}")
            return False

        if not timeline.beats:
            failures.append(f"{manifest.pair_id}/{label}: timeline has no beat markers to verify alignment")
            ok = False
        if not manifest.media_base_dir:
            failures.append(f"{manifest.pair_id}/{label}: manifest has no media base directory")
            ok = False
            violations = validate(timeline)
        else:
            try:
                media_base = Path(manifest.media_base_dir)
                for relative_path, expected_hash in manifest.input_sha256.items():
                    source_file = resolve_media_path(relative_path, media_base)
                    actual_hash = file_sha256(source_file)
                    if actual_hash != expected_hash:
                        failures.append(
                            f"{manifest.pair_id}/{label}: source changed since generation: {relative_path}"
                        )
                        ok = False
                source_durations = {}
                for source in {clip.source.path for clip in timeline.clips}:
                    source_file = resolve_media_path(source, media_base)
                    source_info = media_probe(source_file)
                    if not source_info.has_video:
                        raise ValueError(f"source has no video stream: {source_file}")
                    source_durations[source] = source_info.duration_ms
                music_file = resolve_media_path(timeline.music.path, media_base)
                music_info = media_probe(music_file)
                if not music_info.has_audio:
                    raise ValueError(f"music has no audio stream: {music_file}")
                violations = validate(
                    timeline,
                    source_durations_ms=source_durations,
                    music_duration_ms=music_info.duration_ms,
                )
            except Exception as error:
                failures.append(f"{manifest.pair_id}/{label}: source/media verification failed: {error}")
                ok = False
                violations = validate(timeline)
        if violations:
            failures.append(f"{manifest.pair_id}/{label}: {'; '.join(str(v) for v in violations)}")
            ok = False

        try:
            rendered = media_probe(video_file)
            if not rendered.has_video or not rendered.has_audio:
                failures.append(f"{manifest.pair_id}/{label}: render is missing an audio or video stream")
                ok = False
            tolerance_ms = max(1, round(1000 / timeline.settings.fps))
            if abs(rendered.duration_ms - timeline.duration_ms) > tolerance_ms:
                failures.append(
                    f"{manifest.pair_id}/{label}: render duration {rendered.duration_ms} ms "
                    f"does not match timeline {timeline.duration_ms} ms within one frame"
                )
                ok = False
        except Exception as error:
            failures.append(f"{manifest.pair_id}/{label}: render is not playable: {error}")
            ok = False

        if render_seconds > M1_WALL_TIME_FIRST_RUN_S:
            failures.append(
                f"{manifest.pair_id}/{label}: render took {render_seconds:.1f}s "
                f"> {M1_WALL_TIME_FIRST_RUN_S:.0f}s budget"
            )
            ok = False
        return ok

    total = 0
    for pair_id in sorted(pair_ids):
        manifest = manifest_by_id.get(pair_id)
        if manifest is None:
            total += 2
            failures.extend((
                f"{pair_id}/cutter: expected pair manifest is missing",
                f"{pair_id}/baseline: expected pair manifest is missing",
            ))
            continue
        artifacts = (
            ("cutter", manifest.cutter_timeline_path, manifest.cutter_video_path, manifest.cutter_render_seconds),
            ("baseline", manifest.baseline_timeline_path, manifest.baseline_video_path, manifest.baseline_render_seconds),
        )
        for label, timeline_path, render_path, render_seconds in artifacts:
            total += 1
            if check_artifact(manifest, label, timeline_path, render_path, render_seconds):
                passing += 1
    return M1Result(total_outputs=total, passing_outputs=passing, failures=failures)


# ---------------------------------------------------------------------------
# M2: the core hypothesis -- blind pairwise preference
# ---------------------------------------------------------------------------


class M2Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_judgments: int
    cutter_wins: int
    baseline_wins: int
    confidence_interval_95: tuple[float, float] | None

    @property
    def preference_rate(self) -> float:
        return self.cutter_wins / self.total_judgments if self.total_judgments else 0.0

    @property
    def passed(self) -> bool:
        return self.total_judgments > 0 and self.preference_rate >= M2_PREFERENCE_THRESHOLD


def compute_m2(ratings: list[PairwiseRating], answer_key: AnswerKey) -> M2Result:
    cutter_wins = 0
    total = 0
    for r in ratings:
        assignment = answer_key.assignments.get(r.pair_id)
        if assignment is None:
            continue  # not part of this dataset -- validate_pairwise_ratings catches this explicitly
        total += 1
        if assignment.source_of[r.chosen_video] == "cutter":
            cutter_wins += 1
    ci = wilson_interval(cutter_wins, total) if total else None
    return M2Result(
        total_judgments=total, cutter_wins=cutter_wins, baseline_wins=total - cutter_wins,
        confidence_interval_95=ci,
    )


# ---------------------------------------------------------------------------
# M3: style differentiation -- mood identification
# ---------------------------------------------------------------------------


class M3Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_judgments: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total_judgments if self.total_judgments else 0.0

    @property
    def passed(self) -> bool:
        return self.total_judgments > 0 and self.accuracy >= M3_MOOD_ID_THRESHOLD


def compute_m3(
    ratings: list[MoodIdentificationRating],
    energetic_side: dict[str, Literal["A", "B"]],
) -> M3Result:
    """``energetic_side`` maps mood_pair_id -> which of "A"/"B" is
    actually the energetic output (the M3 analogue of poc.evaluate.blind's
    answer key)."""
    correct = 0
    total = 0
    for r in ratings:
        expected = energetic_side.get(r.mood_pair_id)
        if expected is None:
            continue
        total += 1
        if r.guessed_energetic == expected:
            correct += 1
    return M3Result(total_judgments=total, correct=correct)


# ---------------------------------------------------------------------------
# M4: usability -- the honest gate
# ---------------------------------------------------------------------------


class M4Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_outputs: int
    passing_outputs: int
    yes_count_by_output: dict[str, int]
    rated_outputs: int
    total_responses: int

    @property
    def passing_fraction(self) -> float:
        return self.passing_outputs / self.total_outputs if self.total_outputs else 0.0

    @property
    def passed(self) -> bool:
        return self.total_outputs > 0 and self.passing_fraction >= M4_MIN_OUTPUT_FRACTION


def compute_m4(
    ratings: list[OutputRating], expected_output_ids: set[str] | None = None
) -> M4Result:
    by_output: dict[str, list[OutputRating]] = defaultdict(list)
    for output_id in expected_output_ids or set():
        by_output[output_id] = []
    for r in ratings:
        if r.would_post is not None and (
            expected_output_ids is None or r.output_id in expected_output_ids
        ):
            by_output[r.output_id].append(r)

    yes_count_by_output = {oid: sum(1 for r in rs if r.would_post) for oid, rs in by_output.items()}
    passing = sum(1 for count in yes_count_by_output.values() if count >= M4_MIN_RATERS_YES)
    return M4Result(
        total_outputs=len(by_output),
        passing_outputs=passing,
        yes_count_by_output=yes_count_by_output,
        rated_outputs=sum(bool(rs) for rs in by_output.values()),
        total_responses=sum(len(rs) for rs in by_output.values()),
    )


# ---------------------------------------------------------------------------
# M5: perceived sync
# ---------------------------------------------------------------------------


class M5Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_judgments: int
    mean_rating: float

    @property
    def passed(self) -> bool:
        return self.total_judgments > 0 and self.mean_rating >= M5_MEAN_THRESHOLD


def compute_m5(ratings: list[OutputRating]) -> M5Result:
    values = [r.in_time_rating for r in ratings if r.in_time_rating is not None]
    mean = sum(values) / len(values) if values else 0.0
    return M5Result(total_judgments=len(values), mean_rating=mean)


# ---------------------------------------------------------------------------
# overall decision rule -- docs/MVP.md Section 10 "Decision rule", verbatim
# ---------------------------------------------------------------------------


def decide(
    m1: M1Result,
    m2: M2Result,
    m3: M3Result | None = None,
    m4: M4Result | None = None,
    m5: M5Result | None = None,
    *,
    iterated_with_faces: bool = False,
) -> str:
    """Apply the frozen decision rule only after every required metric is measured."""
    missing = [
        name for name, result in (("M3", m3), ("M4", m4), ("M5", m5))
        if result is None
    ]
    if missing:
        return f"INCOMPLETE: required metrics not measured: {', '.join(missing)}. No final verdict issued."

    all_measured_pass = (
        m1.passed
        and m2.passed
        and m3.passed
        and m4.passed
        and m5.passed
    )
    if all_measured_pass:
        return (
            "M1 through M5 all pass: hypothesis validated. Proceed to v0.2 "
            "(LLM Director, natural-language brief) with confidence that the core is worth wrapping."
        )
    if m2.passed and m4 is not None and not m4.passed:
        return (
            "M2 passes, M4 fails: selection works but output is not good enough to ship. "
            "Add faces and downbeat-aligned motion peaks, re-run once. If M4 still fails, the "
            "product needs semantic understanding (VLM) before it is useful, which changes the "
            "cost structure and must be decided explicitly."
        )
    if not m2.passed:
        if iterated_with_faces:
            return (
                "M2 fails after one iteration with faces: the cheap-signal hypothesis is false "
                "for this footage class. Stop and rethink perception before writing any more "
                "cutter code."
            )
        if 0.55 <= m2.preference_rate <= 0.65:
            return (
                "M2 in the 55-65% range: the signals are too weak. Add faces and re-run "
                "(one iteration) before drawing a conclusion."
            )
        return "M2 at or near 50%: selection adds nothing over random choice on this data."
    return "Not all measured metrics pass, and none of MVP.md's named decision-rule cases apply exactly; report the individual results."
