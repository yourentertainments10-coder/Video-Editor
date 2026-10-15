"""P2.6 EditorTimeline compiler and renderer built from tested render modules."""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import av

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.project_assets import AssetImportError, ProjectAssetLibrary
from poc.render._process import run_ffmpeg_atomic, run_process
from poc.render.aspect import fit_to_frame_to_file
from poc.render.audio import encode_delivery_audio, render_audio_track
from poc.render.export import ExportProfile, VerifyReport, get_profile, verify_render
from poc.render.effects import build_effect_filters
from poc.render.text import ANIMATIONS, TextOverlay, _FONTS_DIR, _HEX_COLOR, _ass_filter, _write_ass
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
    encoder: dict[str, object]
    timings: dict[str, float]
    audio: dict[str, object] = field(default_factory=dict)


_TRANSITION_MAP = {
    "cut": "cut",
    "fade": "crossfade",
    "dip_to_black": "fade_black",
    "dip_to_white": "fade_white",
    "wipe_left": "wipe_left",
    "wipe_right": "wipe_right",
    "wipe_up": "wipe_up",
    "wipe_down": "wipe_down",
    "dissolve": "dissolve",
    **{tid: tid for tid in (
        "slide_left", "slide_right", "slide_up", "slide_down", "zoom_through", "blur_through",
        "circle_open", "circle_close", "radial", "pixelize", "smooth_left", "smooth_right",
        "squeeze", "slice", "wind", "diagonal", "fade_mono")},
}



# FFmpeg's deshake crashes (SIGSEGV) when three or more run in one filter graph, so each
# stabilised clip is rendered on its own first (see EditorRenderer._stabilise).
_STABILISE = re.compile(r"deshake=rx=\d+:ry=\d+,crop=[^,]+:[^,]+,scale=\d+:\d+")


def _title_style(metadata: dict | None) -> dict:
    """TextOverlay settings from a text asset's ``metadata["title_style"]`` (set by the Director or
    the Inspector). Unknown or broken values fall back to the plain fade instead of failing a render."""
    style = (metadata or {}).get("title_style")
    if not isinstance(style, dict):
        return {}
    out: dict = {}
    if style.get("animation") in ANIMATIONS:
        out["animation"] = style["animation"]
    for key in ("color", "accent"):
        value = style.get(key)
        if isinstance(value, str) and _HEX_COLOR.fullmatch(value):
            out[key] = value
    size = style.get("size_pct")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and math.isfinite(size) and 0.02 <= size <= 0.2:
        out["size_pct"] = float(size)
    return out

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


_AUDIO_FLAGS: dict[tuple[str, int, int], bool] = {}


def _has_audio(path: Path) -> bool:
    """Whether a source has an audio stream; remembered per file version (opened once, not per clip)."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    known = _AUDIO_FLAGS.get(key)
    if known is None:
        with av.open(str(path)) as container:
            known = bool(container.streams.audio)
        if len(_AUDIO_FLAGS) > 4096:
            _AUDIO_FLAGS.clear()
        _AUDIO_FLAGS[key] = known
    return known


def _check_transition_parameters(clip) -> None:
    ref = clip.transition
    if ref is None:
        return
    transition_id = ref.transition_id
    params = ref.parameters
    reason = None
    if transition_id == "fade" and params is not None and params.curve != "linear":
        reason = f"curve={params.curve!r}"
    elif transition_id in ("dip_to_black", "dip_to_white") and params is not None:
        if params.peak_fraction != 0.5:
            reason = f"peak_fraction={params.peak_fraction!r}"
        elif params.hold_fraction != 0.0:
            reason = f"hold_fraction={params.hold_fraction!r}"
    elif transition_id.startswith("wipe_") and params is not None and params.softness != 0.0:
        reason = f"softness={params.softness!r}"
    elif transition_id == "dissolve" and params is not None and params.softness != 0.0:
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
                **_title_style(asset.metadata),
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
               cancel: Callable[[], bool] | None = None,
               profile: ExportProfile | None = None) -> RenderResult:
        out = Path(out)
        selected = profile or get_profile("youtube_1080p")
        encoder = {"crf": selected.crf, "preset": selected.preset,
                   "audio_bitrate": selected.audio_bitrate}
        out.parent.mkdir(parents=True, exist_ok=True)
        self._check_cancel(cancel)
        total_started = time.perf_counter()
        timings = {name: 0.0 for name in ("prepare", "audio", "video", "mux", "verify", "total")}
        self._emit(progress, 0.0, "Preparing render")
        try:
            with tempfile.TemporaryDirectory(prefix="editor-render-", dir=out.parent) as tmp_name:
                temp_dir = Path(tmp_name)
                prepare_started = time.perf_counter()
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
                        still = MediaAsset(asset_id=item.asset_id, kind="image", uri=str(source),
                                           duration_ms=round(frames * 1000 / plan.fps))
                        file = fit_to_frame_to_file(
                            still, temp_dir / f"image_{index:04d}.mp4", plan.width,
                            plan.height, mode="cover", fps=plan.fps, cancel=cancel,
                        )
                    else:
                        file = Path(item.path or "")
                    resolved_segments.append((item, file))

                # shaky clips: stabilised one by one into temp files (index -> path, source in, rest of filters)
                stable: dict[int, tuple[Path, int, str]] = {}
                for index, (item, path) in enumerate(resolved_segments):
                    match = _STABILISE.search(item.vfilter) if item.kind == "video" else None
                    if match is None:
                        continue
                    self._check_cancel(cancel)
                    left_ms = min(int(item.source_in_ms or 0), round(item.transition_frames * 1000 / plan.fps))
                    right_frames = (plan.video_segments[index + 1].transition_frames
                                    if index + 1 < len(plan.video_segments) else 0)
                    span_ms = round((item.duration_frames + right_frames) * 1000 / plan.fps)
                    start_ms = int(item.source_in_ms or 0) - left_ms
                    stable_path = render_sequence_to_file(
                        [Segment(path=path, src_in_ms=start_ms, src_out_ms=start_ms + left_ms + span_ms,
                                 vfilter=match.group(0))], [],
                        temp_dir / f"stable_{index:04d}.mp4", width=plan.width, height=plan.height,
                        fps=plan.fps, crf=12, preset="veryfast", input_seeking=True, cwd=temp_dir,
                        cancel=cancel,
                    )
                    rest = (item.vfilter[:match.start()] + item.vfilter[match.end():]).replace(",,", ",").strip(",")
                    stable[index] = (stable_path, left_ms, rest)

                def video_segment(index: int, segment: Segment) -> Segment:
                    if index not in stable:
                        return segment
                    stable_path, left_ms, rest = stable[index]
                    return Segment(path=stable_path, src_in_ms=left_ms,
                                   src_out_ms=left_ms + segment.src_out_ms - segment.src_in_ms, vfilter=rest)

                actual_segments: list[Segment] = []
                actual_joins: list[Join] = []
                audio_options: list[dict] = []
                placement_by_clip = {p.clip_id: p for p in plan.audio_placements if p.role == "clip"}
                for index, (item, path) in enumerate(resolved_segments):
                    left_frames = item.transition_frames
                    base_ms = round(item.duration_frames * 1000 / plan.fps)
                    source_in = (0 if item.kind == "gap" else
                                 round(left_frames * 1000 / plan.fps) if item.kind == "image" else
                                 int(item.source_in_ms or 0))
                    actual_segments.append(Segment(
                        path=path, src_in_ms=source_in, src_out_ms=source_in + base_ms,
                        vfilter=item.vfilter,
                    ))
                    placement = placement_by_clip.get(item.clip_id)
                    audio_options.append({
                        "muted": item.kind != "video" or placement is None or placement.muted,
                        "gain_db": placement.gain_db if placement else 0.0,
                        "pan": placement.pan if placement else 0.0,
                        "fade_in_ms": placement.fade_in_ms if placement else 0,
                        "fade_out_ms": placement.fade_out_ms if placement else 0,
                    })
                    if index:
                        actual_joins.append(
                            Join("cut", 0) if item.transition_in == "cut" or not item.transition_frames
                            else Join(_TRANSITION_MAP[item.transition_in],
                                      _join_duration_ms(item.transition_frames, plan.fps))
                        )
                joins = [
                    Join("cut", 0) if item.transition_in == "cut" or not item.transition_frames
                    else Join(_TRANSITION_MAP[item.transition_in],
                              _join_duration_ms(item.transition_frames, plan.fps))
                    for item in plan.video_segments[1:]
                ]
                ass_filter = None
                if plan.text_overlays:
                    ass_path = temp_dir / "overlays.ass"
                    _write_ass(ass_path, list(plan.text_overlays), plan.width, plan.height)
                    ass_filter = _ass_filter(Path(ass_path.name))
                timings["prepare"] = float(time.perf_counter() - prepare_started)

                audio_report: dict = {}
                failed = threading.Event()
                def cancelled() -> bool:
                    return failed.is_set() or (cancel is not None and cancel())

                def audio_job() -> Path:
                    began = time.perf_counter()
                    track_wav = render_audio_track(
                        actual_segments, actual_joins, temp_dir / "track.wav", fps=plan.fps,
                        segment_audio_options=audio_options, cancel=cancelled,
                    )
                    mixed_wav = self._mix_placements(
                        track_wav, plan.audio_placements, temp_dir / "mixed.wav",
                        duration_ms=plan.duration_ms, cancel=cancelled,
                    )
                    final_audio = temp_dir / "final.m4a"
                    audio_report.update(encode_delivery_audio(
                        mixed_wav, final_audio, target_lufs=selected.target_lufs,
                        true_peak_db=selected.true_peak_db, bitrate=selected.audio_bitrate,
                        cancel=cancelled,
                    ))
                    timings["audio"] = float(time.perf_counter() - began)
                    return final_audio

                def video_job() -> Path:
                    began = time.perf_counter()

                    # Chunked video processing to limit memory usage
                    # Split segments into chunks of at most 8 segments, splitting only at plain cuts
                    max_inputs_per_encode = 8
                    chunks = []  # List of (start_idx, end_idx) into resolved_segments
                    current_chunk_start = 0

                    for i in range(len(resolved_segments)):
                        # Check if we should finalize this chunk
                        should_split = False
                        if i - current_chunk_start + 1 >= max_inputs_per_encode:
                            # Only split at plain cuts (not inside transitions)
                            if i == len(resolved_segments) - 1:
                                # Last segment
                                should_split = True
                            else:
                                # Check if the JOIN to next segment is a cut
                                join_index = i  # join i is between segment i and i+1
                                if join_index < len(actual_joins) and actual_joins[join_index].kind == "cut":
                                    should_split = True
                        elif i == len(resolved_segments) - 1:
                            # Last segment always finalizes the chunk
                            should_split = True

                        if should_split:
                            chunks.append((current_chunk_start, i + 1))
                            current_chunk_start = i + 1

                    # If only one chunk, use original path
                    if len(chunks) == 1:
                        path = render_sequence_to_file(
                            [video_segment(i, seg) for i, seg in enumerate(actual_segments)], joins,
                            temp_dir / "video.mp4", width=plan.width,
                            height=plan.height, fps=plan.fps, audio_path=None,
                            crf=selected.crf, preset=selected.preset, input_seeking=True, max_kbps=selected.max_kbps,
                            final_filter=ass_filter, cwd=temp_dir, cancel=cancelled,
                        )
                        timings["video"] = float(time.perf_counter() - began)
                        return path

                    # Process each chunk
                    chunk_videos = []

                    # Determine parallelism
                    max_parallel = max(1, min(3, os.cpu_count() // 4))

                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def process_chunk(chunk_idx, chunk_start, chunk_end):
                        if chunk_end <= chunk_start:
                            return None

                        # Build Segment and Join lists for this chunk
                        chunk_segments = []
                        chunk_joins = []
                        for i in range(chunk_start, chunk_end):
                            item, path = resolved_segments[i]
                            left_frames = item.transition_frames
                            base_ms = round(item.duration_frames * 1000 / plan.fps)
                            source_in = (0 if item.kind == "gap" else
                                         round(left_frames * 1000 / plan.fps) if item.kind == "image" else
                                         int(item.source_in_ms or 0))
                            chunk_segments.append(video_segment(i, Segment(
                                path=path, src_in_ms=source_in, src_out_ms=source_in + base_ms,
                                vfilter=item.vfilter,
                            )))

                            # Add join to next segment (except for last segment in chunk)
                            if i < chunk_end - 1:
                                next_item, _ = resolved_segments[i + 1]
                                # Find the join between these segments in the original list
                                original_index = resolved_segments.index((item, path))
                                if original_index < len(actual_joins):
                                    chunk_joins.append(actual_joins[original_index])

                        # Calculate chunk timing
                        vs_segments = [item[0] for item in resolved_segments[chunk_start:chunk_end]]
                        chunk_start_frames = sum(       # frames of every chunk BEFORE this one
                            item[0].duration_frames for item in resolved_segments[:chunk_start]
                        )
                        chunk_duration_frames = sum(
                            vs.duration_frames for vs in vs_segments
                        )

                        # Create ASS filter for titles in this chunk
                        chunk_ass_filter = None
                        if plan.text_overlays:
                            # Filter and shift text overlays for this chunk
                            chunk_overlays = []
                            for overlay in plan.text_overlays:
                                overlay_start_frame = overlay.start_ms * plan.fps // 1000
                                overlay_end_frame = overlay.end_ms * plan.fps // 1000
                                chunk_start_frame_abs = chunk_start_frames
                                chunk_end_frame_abs = chunk_start_frame_abs + chunk_duration_frames

                                # Check if overlay intersects with this chunk
                                if overlay_end_frame > chunk_start_frame_abs and overlay_start_frame < chunk_end_frame_abs:
                                    # Calculate fade settings for chunk edges
                                    fade_in_ms = overlay.fade_in_ms if overlay.fade_in_ms is not None else overlay.fade_ms
                                    fade_out_ms = overlay.fade_out_ms if overlay.fade_out_ms is not None else overlay.fade_ms

                                    # If overlay starts before chunk, no fade-in at chunk start
                                    if overlay_start_frame <= chunk_start_frame_abs:
                                        fade_in_ms = 0
                                    # If overlay ends after chunk, no fade-out at chunk end
                                    if overlay_end_frame >= chunk_end_frame_abs:
                                        fade_out_ms = 0

                                    # Create adjusted overlay for this chunk
                                    adjusted_start = max(0, (overlay_start_frame - chunk_start_frame_abs) * 1000 // plan.fps)
                                    adjusted_end = min(chunk_duration_frames * 1000 // plan.fps,
                                                       (overlay_end_frame - chunk_start_frame_abs) * 1000 // plan.fps)
                                    adjusted_overlay = TextOverlay(
                                        text=overlay.text,
                                        start_ms=adjusted_start,
                                        end_ms=adjusted_end,
                                        x=overlay.x,
                                        y=overlay.y,
                                        size_pct=overlay.size_pct,
                                        color=overlay.color,
                                        outline_px=overlay.outline_px,
                                        bold=overlay.bold,
                                        fade_in_ms=fade_in_ms,
                                        fade_out_ms=fade_out_ms,
                                        font=overlay.font,
                                        animation=overlay.animation,
                                        accent=overlay.accent,
                                        elapsed_ms=overlay.elapsed_ms + max(
                                            0, (chunk_start_frame_abs - overlay_start_frame) * 1000 // plan.fps),
                                    )
                                    chunk_overlays.append(adjusted_overlay)

                            if chunk_overlays:
                                ass_path = temp_dir / f"overlays_{chunk_idx:04d}.ass"
                                _write_ass(ass_path, chunk_overlays, plan.width, plan.height)
                                chunk_ass_filter = _ass_filter(Path(ass_path.name))

                        # Render the chunk
                        chunk_path = temp_dir / f"chunk_{chunk_idx:04d}.mp4"
                        render_sequence_to_file(
                            chunk_segments, chunk_joins, chunk_path, width=plan.width,
                            height=plan.height, fps=plan.fps, audio_path=None,
                            crf=selected.crf, preset=selected.preset, input_seeking=True, max_kbps=selected.max_kbps,
                            final_filter=chunk_ass_filter, cwd=temp_dir, cancel=cancelled,
                        )
                        return chunk_path

                    # Process chunks in parallel
                    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="editor-render-chunk") as executor:
                        # Submit all chunks
                        future_to_idx = {
                            executor.submit(process_chunk, i, start, end): i
                            for i, (start, end) in enumerate(chunks)
                        }

                        # Collect results in order
                        chunk_results = [None] * len(chunks)
                        for future in as_completed(future_to_idx):
                            idx = future_to_idx[future]
                            try:
                                chunk_results[idx] = future.result()
                            except Exception as e:
                                failed.set()
                                for f in future_to_idx:
                                    f.cancel()
                                raise e

                    # Verify all chunks were processed successfully
                    chunk_videos = []
                    for chunk_result in chunk_results:
                        if chunk_result is None:
                            raise EditorRenderError("Chunk processing failed")
                        chunk_videos.append(chunk_result)

                    # Join chunks with concat demuxer
                    concat_list_path = temp_dir / "concat_list.txt"
                    with open(concat_list_path, 'w') as f:
                        for video_path in chunk_videos:
                            # Use relative paths from temp_dir
                            relative_path = video_path.relative_to(temp_dir)
                            f.write(f"file '{relative_path}'\n")

                    final_video_path = temp_dir / "video.mp4"
                    cmd = [FFMPEG_EXE, "-y", "-fflags", "+bitexact", *FFMPEG_NETWORK_SAFE[1:],
                           "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
                           "-c", "copy", str(final_video_path.with_suffix('.partial.mp4'))]
                    run_ffmpeg_atomic(cmd, final_video_path, cancel=cancelled, label="chunk concat mux", cwd=temp_dir)

                    timings["video"] = float(time.perf_counter() - began)
                    return final_video_path

                self._check_cancel(cancel)
                self._emit(progress, 0.1, "Rendering audio and video")
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="editor-render") as pool:
                    audio_future = pool.submit(audio_job)
                    video_future = pool.submit(video_job)
                    futures = {audio_future, video_future}
                    while futures:
                        done, futures = wait(futures, return_when=FIRST_COMPLETED)
                        for future in done:
                            error = future.exception()
                            if error is not None:
                                failed.set()
                                for other in futures:
                                    other.cancel()
                                if isinstance(error, InterruptedError):
                                    raise RenderCancelled("render cancelled") from error
                                raise error
                    final_audio = audio_future.result()
                    video_path = video_future.result()

                self._check_cancel(cancel)
                self._emit(progress, 0.72, "Muxing")
                mux_started = time.perf_counter()
                muxed = temp_dir / "muxed.mp4"
                mux_cmd = [FFMPEG_EXE, "-y", "-fflags", "+bitexact", *FFMPEG_NETWORK_SAFE[1:],
                           "-i", str(video_path), "-i", str(final_audio),
                           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                           "-c:a", "copy", "-flags:v", "+bitexact",
                           "-flags:a", "+bitexact", "-map_metadata", "-1",
                           "-movflags", "+faststart", str(muxed)]
                run_ffmpeg_atomic(mux_cmd, muxed, cancel=cancel, label="render mux")
                timings["mux"] = float(time.perf_counter() - mux_started)

                self._check_cancel(cancel)
                self._emit(progress, 0.86, "Verifying render")
                verify_started = time.perf_counter()
                report = verify_render(
                    muxed, expected_width=plan.width, expected_height=plan.height,
                    expected_fps=plan.fps, expected_duration_ms=plan.duration_ms,
                    expect_audio=True, target_lufs=selected.target_lufs,
                    true_peak_db=selected.true_peak_db,
                )
                timings["verify"] = float(time.perf_counter() - verify_started)
                self._check_cancel(cancel)
                partial = out.with_name(f"{out.stem}.{os.getpid()}.partial{out.suffix}")
                try:
                    shutil.copyfile(muxed, partial)
                    os.replace(partial, out)
                except BaseException:
                    partial.unlink(missing_ok=True)
                    raise
            timings["total"] = float(time.perf_counter() - total_started)
            self._emit(progress, 1.0, "Render complete")
            return RenderResult(
                out_path=out, duration_frames=plan.duration_frames,
                duration_ms=plan.duration_ms, video_clip_order=plan.video_clip_order,
                audio_clip_count=plan.audio_clip_count, report=report,
                encoder=encoder, timings=timings, audio=audio_report,
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
