"""P2.6 EditorTimeline compiler and renderer built from tested render modules."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import av

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.project_assets import AssetImportError, ProjectAssetLibrary
from poc.render._process import run_ffmpeg_atomic, run_process
from poc.render.aspect import fit_to_frame_to_file
from poc.render.audio import loudness_normalize, render_audio_track
from poc.render.export import VerifyReport, _measure_input_i, verify_render
from poc.render.effects import build_effect_filters
from poc.render.text import TextOverlay, burn_text_to_file
from poc.render.transitions import Join, Segment, render_sequence_to_file
from poc.timeline import (
    AudioProperties,
    EFFECT_CATALOGUE,
    TRANSITION_CATALOGUE,
    EditorTimeline,
    MediaAsset,
    get_effect_metadata,
    get_transition_metadata,
    validate_editor_timeline,
)


class EditorRenderError(Exception):
    """A timeline cannot be compiled or an editor render failed."""


class RenderCancelled(EditorRenderError):
    """Rendering was cancelled by its caller."""


@dataclass(frozen=True)
class VideoSegment:
    clip_id: str
    asset_id: str
    kind: Literal["video", "image", "gap"]
    path: str | None
    source_in_ms: int | None
    source_out_ms: int | None
    start_frame: int
    duration_frames: int
    transition_in: str
    transition_frames: int
    vfilter: str = ""


@dataclass(frozen=True)
class AudioPlacement:
    clip_id: str
    asset_id: str
    path: str
    role: Literal["clip", "voice", "music"]
    source_in_ms: int
    source_out_ms: int
    start_ms: int
    duration_ms: int
    gain_db: float
    pan: float
    muted: bool
    fade_in_ms: int
    fade_out_ms: int


@dataclass(frozen=True)
class EditorRenderPlan:
    width: int
    height: int
    fps: int
    duration_frames: int
    duration_ms: int
    video_segments: tuple[VideoSegment, ...]
    audio_placements: tuple[AudioPlacement, ...]
    text_overlays: tuple[TextOverlay, ...]
    video_clip_order: tuple[str, ...]
    audio_clip_count: int


@dataclass(frozen=True)
class RenderResult:
    out_path: Path
    duration_frames: int
    duration_ms: int
    video_clip_order: tuple[str, ...]
    audio_clip_count: int
    report: VerifyReport


_TRANSITION_MAP = {
    "cut": "cut",
    "fade": "crossfade",
    "dip_to_black": "fade_black",
    "wipe_left": "wipe_left",
}


def _frame(tick: int, fps: int, ticks_per_second: int) -> int:
    return round(tick * fps / ticks_per_second)


def _source_range(clip, project_asset) -> tuple[int, int]:
    source_in = clip.source_in_ms if clip.source_in_ms is not None else 0
    source_out = clip.source_out_ms
    if source_out is None:
        source_out = project_asset.duration_ms or source_in
    return int(source_in), int(source_out)


def _audio_props(clip) -> AudioProperties:
    return clip.audio or AudioProperties()


def _has_audio(path: Path) -> bool:
    with av.open(str(path)) as container:
        return bool(container.streams.audio)


def _check_transition_parameters(clip) -> None:
    ref = clip.transition
    if ref is None:
        return
    transition_id = ref.transition_id
    params = ref.parameters
    reason = None
    if transition_id == "fade" and params is not None and params.curve != "linear":
        reason = f"curve={params.curve!r}"
    elif transition_id == "dip_to_black" and params is not None:
        if params.peak_fraction != 0.5:
            reason = f"peak_fraction={params.peak_fraction!r}"
        elif params.hold_fraction != 0.0:
            reason = f"hold_fraction={params.hold_fraction!r}"
    elif transition_id == "wipe_left" and params is not None and params.softness != 0.0:
        reason = f"softness={params.softness!r}"
    if reason:
        raise EditorRenderError(
            f"transition '{transition_id}' with {reason} is not renderable"
        )


def _join_duration_ms(frames: int, fps: int) -> int:
    if frames <= 0:
        return 0
    # Find the shortest millisecond duration that rounds to this same even frame count.
    candidate = max(1, math.floor((frames / 2 - 0.5) * 2000 / fps))
    while 2 * round(candidate * fps / 2000) < frames:
        candidate += 1
    while candidate > 1 and 2 * round((candidate - 1) * fps / 2000) == frames:
        candidate -= 1
    return candidate


def _validate_namespaced_timeline(timeline: EditorTimeline):
    """Validate IDs within their entity types while keeping references intact.

    Editor documents use separate ID namespaces for assets, tracks, layers,
    clips, and markers. The shared validator historically checks IDs across
    those namespaces, so validate a namespaced copy and preserve duplicates
    within each entity type.
    """
    asset_ids = {item.asset_id: f"asset::{item.asset_id}" for item in timeline.assets}
    layer_ids = {item.layer_id: f"layer::{item.layer_id}" for item in timeline.layers}
    track_ids = {item.track_id: f"track::{item.track_id}" for item in timeline.tracks}
    clip_ids = {item.clip_id: f"clip::{item.clip_id}" for item in timeline.clips}
    assets = [item.model_copy(update={"asset_id": asset_ids[item.asset_id]}) for item in timeline.assets]
    layers = [item.model_copy(update={"layer_id": layer_ids[item.layer_id]}) for item in timeline.layers]
    tracks = [item.model_copy(update={
        "track_id": track_ids[item.track_id], "layer_id": layer_ids.get(item.layer_id, item.layer_id),
    }) for item in timeline.tracks]
    clips = [item.model_copy(update={
        "clip_id": clip_ids[item.clip_id],
        "asset_id": asset_ids.get(item.asset_id, item.asset_id),
        "track_id": track_ids.get(item.track_id, item.track_id),
    }) for item in timeline.clips]
    markers = [item.model_copy(update={"marker_id": f"marker::{item.marker_id}"}) for item in timeline.markers]
    candidate = timeline.model_copy(update={
        "assets": assets, "layers": layers, "tracks": tracks, "clips": clips,
        "markers": markers,
        "music_asset_id": asset_ids.get(timeline.music_asset_id, timeline.music_asset_id),
        "music_track_id": track_ids.get(timeline.music_track_id, timeline.music_track_id),
    })
    return validate_editor_timeline(candidate)


class EditorRenderer:
    def compile(self, tl: EditorTimeline, library: ProjectAssetLibrary) -> EditorRenderPlan:
        tracks = {track.track_id: track for track in tl.tracks}
        timeline_assets = {asset.asset_id: asset for asset in tl.assets}
        tps = tl.time_base.ticks_per_second
        visual_clips = sorted(
            (clip for clip in tl.clips if tracks.get(clip.track_id) is not None
             and tracks[clip.track_id].kind in {"video", "overlay"}
             and timeline_assets.get(clip.asset_id) is not None
             and timeline_assets[clip.asset_id].kind != "text"),
            key=lambda clip: (clip.start_tick, clip.clip_id),
        )

        # Required diagnostics are deliberately checked in this order.
        for index, clip in enumerate(visual_clips):
            if clip.transition is not None:
                previous = visual_clips[index - 1] if index else None
                if previous is None or previous.start_tick + previous.duration_ticks != clip.start_tick:
                    raise EditorRenderError("incoming transition requires an adjacent preceding clip")
        project_assets = {}
        for clip in tl.clips:
            asset = timeline_assets.get(clip.asset_id)
            if asset is not None and asset.kind == "text":
                continue
            if clip.asset_id in project_assets:
                continue
            try:
                project_assets[clip.asset_id] = library.asset(clip.asset_id)
            except AssetImportError as exc:
                raise EditorRenderError(
                    f"asset '{clip.asset_id}' is not present in project asset library"
                ) from exc

        settings = tl.settings
        if settings.fps_num % settings.fps_den:
            raise EditorRenderError(
                f"frame rate {settings.fps_num}/{settings.fps_den} not supported yet"
            )
        fps = settings.fps_num // settings.fps_den
        if fps <= 0:
            raise EditorRenderError(f"frame rate {settings.fps_num}/{settings.fps_den} not supported yet")

        violations = _validate_namespaced_timeline(tl)
        if violations:
            codes = sorted({violation.code for violation in violations})
            raise EditorRenderError(f"timeline validation failed: {', '.join(codes)}")

        for clip in tl.clips:
            asset = timeline_assets.get(clip.asset_id)
            if asset is not None and asset.kind == "text":
                continue
            project_asset = project_assets[clip.asset_id]
            kind = asset.kind if asset is not None else project_asset.kind
            if kind in {"video", "audio", "music"}:
                source_in, source_out = _source_range(clip, project_asset)
                source_frames = (source_out - source_in) * fps / 1000
                start_frame = _frame(clip.start_tick, fps, tps)
                end_frame = _frame(clip.start_tick + clip.duration_ticks, fps, tps)
                duration_frames = end_frame - start_frame
                if abs(source_frames - duration_frames) > 1:
                    raise EditorRenderError("speed changes are not supported yet")

        timeline_frames = _frame(tl.duration_ticks, fps, tps)
        visual_frame_ranges = []
        for clip in visual_clips:
            start = _frame(clip.start_tick, fps, tps)
            end = _frame(clip.start_tick + clip.duration_ticks, fps, tps)
            visual_frame_ranges.append((clip, start, end))
        for (previous, _, previous_end), (current, current_start, _) in zip(
            visual_frame_ranges, visual_frame_ranges[1:]
        ):
            if current_start < previous_end:
                raise EditorRenderError(
                    f"overlapping visual clips '{previous.clip_id}' and '{current.clip_id}'"
                )

        transition_catalogue = {item.transition_id: item for item in TRANSITION_CATALOGUE}
        effect_catalogue = {item.effect_id: item for item in EFFECT_CATALOGUE}
        for clip in tl.clips:
            for effect in clip.effects:
                metadata = effect_catalogue.get(effect.effect_id) or get_effect_metadata(effect.effect_id)
                if not metadata.renderable:
                    raise EditorRenderError(f"effect '{effect.effect_id}' is not renderable")
            if clip.transition is not None:
                transition_id = clip.transition.transition_id
                metadata = transition_catalogue.get(transition_id) or get_transition_metadata(transition_id)
                if not metadata.renderable:
                    raise EditorRenderError(f"transition '{transition_id}' is not renderable")
                _check_transition_parameters(clip)

        visual_segments: list[VideoSegment] = []
        clip_order: list[str] = []
        filters_by_clip: dict[str, str] = {}
        for clip, start, end in visual_frame_ranges:
            if not clip.effects:
                continue
            try:
                filters_by_clip[clip.clip_id] = build_effect_filters(
                    clip.effects, width=settings.width, height=settings.height, fps=fps,
                    left_handle_frames=(
                        round(clip.transition.duration_ticks * fps / (2 * tps))
                        if clip.transition is not None else 0
                    ),
                    clip_frames=end-start,
                )
            except ValueError as exc:
                if str(exc) == "zoom below 1.0 is not supported":
                    for effect in clip.effects:
                        if effect.effect_id not in {"zoom_in", "zoom_out"}:
                            continue
                        params = effect.parameters.model_dump()
                        for param in ("from_scale", "to_scale"):
                            value = params.get(param)
                            if value is not None and value < 1.0:
                                raise EditorRenderError(
                                    f"effect '{effect.effect_id}' with {param}={value} is not renderable"
                                ) from exc
                raise EditorRenderError(
                    f"effect '{clip.effects[0].effect_id}' is not renderable: {exc}"
                ) from exc
        cursor = 0
        for clip, start, end in visual_frame_ranges:
            if start > cursor:
                visual_segments.append(VideoSegment(
                    clip_id=f"__gap_{cursor}_{start}", asset_id="", kind="gap", path=None,
                    source_in_ms=None, source_out_ms=None, start_frame=cursor,
                    duration_frames=start-cursor, transition_in="cut", transition_frames=0,
                ))
            asset = timeline_assets.get(clip.asset_id)
            project_asset = project_assets[clip.asset_id]
            path = library.resolve_source(clip.asset_id)
            source_in, source_out = _source_range(clip, project_asset)
            transition_id = clip.transition.transition_id if clip.transition else "cut"
            transition_frames = (
                2 * round(clip.transition.duration_ticks * fps / (2 * tps))
                if clip.transition is not None else 0
            )
            visual_segments.append(VideoSegment(
                clip_id=clip.clip_id, asset_id=clip.asset_id, kind=asset.kind,
                path=str(path),
                source_in_ms=source_in if asset.kind == "video" else None,
                source_out_ms=source_out if asset.kind == "video" else None,
                start_frame=start, duration_frames=end-start,
                transition_in=transition_id, transition_frames=transition_frames,
                vfilter=filters_by_clip.get(clip.clip_id, ""),
            ))
            clip_order.append(clip.clip_id)
            cursor = end
        if cursor < timeline_frames:
            visual_segments.append(VideoSegment(
                clip_id=f"__gap_{cursor}_{timeline_frames}", asset_id="", kind="gap", path=None,
                source_in_ms=None, source_out_ms=None, start_frame=cursor,
                duration_frames=timeline_frames-cursor, transition_in="cut", transition_frames=0,
            ))
        if not visual_segments:
            visual_segments.append(VideoSegment(
                clip_id=f"__gap_0_{timeline_frames}", asset_id="", kind="gap", path=None,
                source_in_ms=None, source_out_ms=None, start_frame=0,
                duration_frames=timeline_frames, transition_in="cut", transition_frames=0,
            ))

        text_overlays: list[TextOverlay] = []
        for clip in sorted(tl.clips, key=lambda item: (item.start_tick, item.clip_id)):
            track = tracks.get(clip.track_id)
            asset = timeline_assets.get(clip.asset_id)
            if track is None or track.kind != "overlay" or asset is None or asset.kind != "text":
                continue
            start_frame = _frame(clip.start_tick, fps, tps)
            end_frame = _frame(clip.start_tick + clip.duration_ticks, fps, tps)
            x = 0.5 + clip.transform.position_x
            y = 0.8 + clip.transform.position_y
            if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (x, y)):
                raise EditorRenderError(
                    f"text clip '{clip.clip_id}' is positioned outside the frame"
                )
            text_overlays.append(TextOverlay(
                text=asset.text or "",
                start_ms=round(start_frame * 1000 / fps),
                end_ms=round(end_frame * 1000 / fps),
                x=x,
                y=y,
            ))

        audio_placements: list[AudioPlacement] = []
        audio_clip_count = 0
        for clip in sorted(tl.clips, key=lambda item: (item.start_tick, item.clip_id)):
            track = tracks[clip.track_id]
            timeline_asset = timeline_assets[clip.asset_id]
            if timeline_asset.kind == "image" or timeline_asset.kind == "text":
                continue
            project_asset = project_assets[clip.asset_id]
            path = library.resolve_source(clip.asset_id)
            if track.kind in {"video", "overlay"}:
                if timeline_asset.kind != "video" or not _has_audio(path):
                    continue
                role: Literal["clip", "voice", "music"] = "clip"
            elif track.kind == "audio":
                role = "voice"
            elif track.kind == "music":
                role = "music"
            else:
                continue
            props = _audio_props(clip)
            start_frame = _frame(clip.start_tick, fps, tps)
            end_frame = _frame(clip.start_tick + clip.duration_ticks, fps, tps)
            source_in, source_out = _source_range(clip, project_asset)
            muted = bool(track.muted or props.muted)
            placement = AudioPlacement(
                clip_id=clip.clip_id, asset_id=clip.asset_id, path=str(path), role=role,
                source_in_ms=source_in, source_out_ms=source_out,
                start_ms=round(start_frame * 1000 / fps),
                duration_ms=round((end_frame-start_frame) * 1000 / fps),
                gain_db=props.gain_db, pan=props.pan, muted=muted,
                fade_in_ms=props.fade_in_ms, fade_out_ms=props.fade_out_ms,
            )
            audio_placements.append(placement)
            if not muted:
                audio_clip_count += 1

        duration_ms = round(timeline_frames * 1000 / fps)
        return EditorRenderPlan(
            width=settings.width, height=settings.height, fps=fps,
            duration_frames=timeline_frames, duration_ms=duration_ms,
            video_segments=tuple(visual_segments), audio_placements=tuple(audio_placements),
            text_overlays=tuple(text_overlays),
            video_clip_order=tuple(clip_order), audio_clip_count=audio_clip_count,
        )

    def _check_cancel(self, cancel: Callable[[], bool] | None) -> None:
        if cancel is not None and cancel():
            raise RenderCancelled("render cancelled")

    def _emit(self, progress, fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    def _make_gap(self, path: Path, plan: EditorRenderPlan, frames: int,
                  cancel: Callable[[], bool] | None) -> Path:
        duration = frames / plan.fps
        cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:],
               "-f", "lavfi", "-i", f"color=c=black:s={plan.width}x{plan.height}:r={plan.fps}:d={duration:.12g}",
               "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "18", "-pix_fmt", "yuv420p", "-flags:v", "+bitexact",
               "-fflags", "+bitexact", "-map_metadata", "-1", "-colorspace", "bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
               "-movflags", "+faststart", str(path)]
        return run_ffmpeg_atomic(cmd, path, cancel=cancel, label="black gap generation")

    def _mix_placements(self, base_wav: Path, placements: tuple[AudioPlacement, ...],
                        out_wav: Path, *, duration_ms: int,
                        cancel: Callable[[], bool] | None) -> Path:
        external = [p for p in placements if p.role in {"voice", "music"} and not p.muted]
        if not external:
            shutil.copyfile(base_wav, out_wav)
            return out_wav
        cmd = [FFMPEG_EXE, "-y", "-fflags", "+bitexact", *FFMPEG_NETWORK_SAFE[1:],
               "-i", str(base_wav)]
        for placement in external:
            cmd.extend([
                "-ss", f"{placement.source_in_ms/1000:.12g}",
                "-t", f"{placement.duration_ms/1000:.12g}",
                "-i", placement.path,
            ])
        filters = [
            f"[0:a]atrim=duration={duration_ms/1000:.12g},asetpts=PTS-STARTPTS,"
            "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[base]"
        ]
        labels = ["[base]"]
        for index, placement in enumerate(external, start=1):
            duration = placement.duration_ms / 1000
            filters_for_placement = [
                f"atrim=duration={duration:.12g}", "asetpts=PTS-STARTPTS", "apad",
                "aresample=48000", "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
            ]
            if placement.gain_db:
                filters_for_placement.append(f"volume={placement.gain_db:.12g}dB")
            if placement.pan < 0:
                filters_for_placement.append(f"pan=stereo|c0=c0|c1={1+placement.pan:.12g}*c1")
            elif placement.pan > 0:
                filters_for_placement.append(f"pan=stereo|c0={1-placement.pan:.12g}*c0|c1=c1")
            if placement.fade_in_ms:
                fade = min(placement.fade_in_ms, placement.duration_ms) / 1000
                filters_for_placement.append(f"afade=t=in:st=0:d={fade:.12g}")
            if placement.fade_out_ms:
                fade = min(placement.fade_out_ms, placement.duration_ms) / 1000
                filters_for_placement.append(
                    f"afade=t=out:st={max(0, duration-fade):.12g}:d={fade:.12g}"
                )
            delay = max(0, placement.start_ms)
            filters_for_placement.extend([
                f"adelay={delay}|{delay}", f"atrim=duration={duration_ms/1000:.12g}"
            ])
            label = f"place{index}"
            filters.append(f"[{index}:a]{','.join(filters_for_placement)}[{label}]")
            labels.append(f"[{label}]")
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[outa]")
        cmd.extend(["-filter_complex", ";".join(filters), "-map", "[outa]",
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-bitexact",
                    "-map_metadata", "-1", str(out_wav)])
        return run_ffmpeg_atomic(cmd, out_wav, cancel=cancel, label="timeline audio mix")

    def render(self, plan: EditorRenderPlan, *, out: Path,
               progress: Callable[[float, str], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> RenderResult:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        self._check_cancel(cancel)
        self._emit(progress, 0.0, "Preparing render")
        try:
            with tempfile.TemporaryDirectory(prefix="editor-render-", dir=out.parent) as tmp_name:
                temp_dir = Path(tmp_name)
                resolved_segments: list[tuple[VideoSegment, Path]] = []
                for index, item in enumerate(plan.video_segments):
                    self._check_cancel(cancel)
                    if item.kind == "gap":
                        file = self._make_gap(temp_dir / f"gap_{index:04d}.mp4", plan,
                                              item.duration_frames, cancel)
                    elif item.kind == "image":
                        source = Path(item.path or "")
                        left = item.transition_frames
                        right = (plan.video_segments[index + 1].transition_frames
                                 if index + 1 < len(plan.video_segments) else 0)
                        frames = item.duration_frames + left + right
                        duration_ms = round(frames * 1000 / plan.fps)
                        still = MediaAsset(
                            asset_id=item.asset_id, kind="image", uri=str(source),
                            duration_ms=duration_ms,
                        )
                        file = fit_to_frame_to_file(
                            still, temp_dir / f"image_{index:04d}.mp4", plan.width,
                            plan.height, mode="cover", fps=plan.fps, cancel=cancel,
                        )
                    else:
                        file = Path(item.path or "")
                    resolved_segments.append((item, file))

                actual_segments: list[Segment] = []
                actual_joins: list[Join] = []
                audio_options: list[dict] = []
                placement_by_clip = {
                    p.clip_id: p for p in plan.audio_placements if p.role == "clip"
                }
                for index, (item, path) in enumerate(resolved_segments):
                    left_frames = item.transition_frames
                    base_ms = round(item.duration_frames * 1000 / plan.fps)
                    if item.kind == "gap":
                        source_in = 0
                    elif item.kind == "image":
                        source_in = round(left_frames * 1000 / plan.fps)
                    else:
                        source_in = int(item.source_in_ms or 0)
                    source_out = source_in + base_ms
                    actual_segments.append(Segment(path=path, src_in_ms=source_in,
                                                   src_out_ms=source_out, vfilter=item.vfilter))
                    placement = placement_by_clip.get(item.clip_id)
                    audio_options.append({
                        "muted": item.kind != "video" or placement is None or placement.muted,
                        "gain_db": placement.gain_db if placement else 0.0,
                        "pan": placement.pan if placement else 0.0,
                        "fade_in_ms": placement.fade_in_ms if placement else 0,
                        "fade_out_ms": placement.fade_out_ms if placement else 0,
                    })
                    if index:
                        if item.transition_in == "cut" or not item.transition_frames:
                            actual_joins.append(Join("cut", 0))
                        else:
                            kind = _TRANSITION_MAP[item.transition_in]
                            actual_joins.append(Join(
                                kind, _join_duration_ms(item.transition_frames, plan.fps)
                            ))

                self._check_cancel(cancel)
                self._emit(progress, 0.2, "Rendering clip audio")
                track_wav = render_audio_track(
                    actual_segments, actual_joins, temp_dir / "track.wav", fps=plan.fps,
                    segment_audio_options=audio_options, cancel=cancel,
                )
                self._check_cancel(cancel)
                self._emit(progress, 0.35, "Mixing voice and music")
                mixed_wav = self._mix_placements(
                    track_wav, plan.audio_placements, temp_dir / "mixed.wav",
                    duration_ms=plan.duration_ms, cancel=cancel,
                )
                self._check_cancel(cancel)
                self._emit(progress, 0.5, "Normalizing loudness")
                if _measure_input_i(mixed_wav) <= -70:
                    final_wav = temp_dir / "final.wav"
                    shutil.copyfile(mixed_wav, final_wav)
                else:
                    final_wav = loudness_normalize(
                        mixed_wav, temp_dir / "final.wav", target_lufs=-14.0,
                        true_peak_db=-1.0, cancel=cancel,
                    )
                self._check_cancel(cancel)
                self._emit(progress, 0.68, "Rendering video")
                joins = [
                    Join("cut", 0) if item.transition_in == "cut" or not item.transition_frames
                    else Join(_TRANSITION_MAP[item.transition_in],
                              _join_duration_ms(item.transition_frames, plan.fps))
                    for item in plan.video_segments[1:]
                ]
                video_path = render_sequence_to_file(
                    actual_segments, joins, temp_dir / "video.mp4", width=plan.width,
                    height=plan.height, fps=plan.fps, audio_path=final_wav,
                    input_seeking=True, cancel=cancel,
                )
                self._check_cancel(cancel)
                if plan.text_overlays:
                    self._emit(progress, 0.82, "Burning text overlays")
                    video_path = burn_text_to_file(
                        video_path, list(plan.text_overlays), temp_dir / "text.mp4",
                        cancel=cancel,
                    )
                    self._check_cancel(cancel)
                else:
                    self._emit(progress, 0.82, "Preparing verification")
                self._emit(progress, 0.88, "Verifying render")
                report = verify_render(
                    video_path, expected_width=plan.width, expected_height=plan.height,
                    expected_fps=plan.fps, expected_duration_ms=plan.duration_ms,
                    expect_audio=True, target_lufs=-14.0, true_peak_db=-1.0,
                )
                self._check_cancel(cancel)
                self._emit(progress, 0.96, "Writing output")
                partial = out.with_name(f"{out.stem}.{os.getpid()}.partial{out.suffix}")
                try:
                    shutil.copyfile(video_path, partial)
                    os.replace(partial, out)
                except BaseException:
                    partial.unlink(missing_ok=True)
                    raise
            self._emit(progress, 1.0, "Render complete")
            return RenderResult(
                out_path=out, duration_frames=plan.duration_frames,
                duration_ms=plan.duration_ms, video_clip_order=plan.video_clip_order,
                audio_clip_count=plan.audio_clip_count, report=report,
            )
        except InterruptedError as exc:
            raise RenderCancelled("render cancelled") from exc
        except EditorRenderError:
            raise
        except Exception as exc:
            if cancel is not None and cancel():
                raise RenderCancelled("render cancelled") from exc
            raise EditorRenderError(f"editor render failed: {exc}") from exc


def build_editor_render_plan(timeline: EditorTimeline,
                             library: ProjectAssetLibrary) -> EditorRenderPlan:
    return EditorRenderer().compile(timeline, library)


def render_editor_timeline(timeline: EditorTimeline, library: ProjectAssetLibrary,
                           out_path: Path) -> RenderResult:
    renderer = EditorRenderer()
    plan = renderer.compile(timeline, library)
    return renderer.render(plan, out=out_path)
