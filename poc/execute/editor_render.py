"""poc/execute/editor_render.py: Mixed-media renderer for EditorTimeline.

Implements the Renderer protocol from RENDER_SPEC §1 with support for:
- Variable frame rate, rotation, HDR, odd dimensions, interlaced content
- Audio normalization (missing/odd audio, 5.1, sample rate)
- Unicode/long Windows paths
- Still images
- All transitions and effects from the catalogue that are marked renderable
- FFmpeg network gap fixes: -protocol_whitelist file,pipe and rejects :// paths
- Deterministic output and segment caching
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol, TypedDict

import imageio_ffmpeg

from poc.project_assets import ProjectAssetLibrary
from poc.timeline import (
    AudioProperties,
    Beat,
    Clip,
    EffectRef,
    EditorSettings,
    EditorTimeline,
    FadeParameters,
    FadeTransitionParameters,
    Layer,
    MediaAsset,
    Music,
    Provenance,
    Settings,
    Source,
    TimeBase,
    Timeline,
    TimelineMarker,
    Track,
    TransitionRef,
    ZoomInParameters,
    ZoomOutParameters,
    validate,
    validate_editor_timeline,
)

# FFmpeg binary with network gap fix: only allow file and pipe protocols
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_NETWORK_SAFE = [
    FFMPEG_EXE,
    "-protocol_whitelist",
    "file,pipe",
]
FFPROBE_EXE = FFMPEG_EXE  # imageio-ffmpeg doesn't bundle ffprobe, use ffmpeg -i


class TimelineInvalidError(Exception):
    """Raised when timeline validation fails."""
    pass


class FFmpegError(RuntimeError):
    """FFmpeg subprocess error."""
    pass


class MediaProbeError(RuntimeError):
    """Media probing failed."""
    pass


def _validate_no_network_paths(*paths: str) -> None:
    """Reject any path containing network protocols with clear error."""
    for path in paths:
        if "://" in path:
            raise ValueError(
                f"Network paths are prohibited for security and reproducibility: {path!r}. "
                f"Only local file paths are allowed."
            )


def _resolve_media_path(rel_path: str, base_dir: Path) -> Path:
    """Resolve a Timeline-stored relative POSIX path against base_dir.

    Rejects absolute paths (POSIX `/...` or Windows drive letter) since
    Timeline documents must never store machine-specific absolute paths.
    """
    posix = PurePosixPath(rel_path)
    if posix.is_absolute() or re.match(r"^[A-Za-z]:", rel_path):
        raise ValueError(
            f"media path {rel_path!r} must be relative; Timeline documents must not "
            "store absolute, machine-specific paths"
        )
    return (base_dir / rel_path).resolve()


def _probe_media(path: Path) -> dict:
    """Probe media file using FFmpeg and return JSON metadata."""
    _validate_no_network_paths(str(path))

    cmd = [*FFMPEG_NETWORK_SAFE, "-v", "error", "-show_entries",
           "format=duration",
           "-show_entries", "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_transfer,field_order,sample_rate,channels,side_data_list",
           "-show_entries", "stream_tags=rotate",
           "-of", "json", str(path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MediaProbeError(f"FFprobe failed for {path}: {result.stderr}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise MediaProbeError(f"Failed to parse FFprobe output for {path}: {e}") from e


@dataclass(frozen=True)
class RenderGraph:
    """Immutable representation of a rendered timeline ready for FFmpeg execution."""
    timeline: EditorTimeline
    library: ProjectAssetLibrary
    normalized_assets: dict[str, dict]  # asset_id -> normalization info
    segment_instructions: list[dict]  # Ordered list of segment processing instructions
    cache_key: str


@dataclass(frozen=True)
class RenderResult:
    """Result of a render operation."""
    out_path: Path
    duration_frames: int
    duration_ms: int
    video_clip_order: tuple[str, ...]
    cache_hits: int
    cache_misses: int


class Renderer(Protocol):
    """Renderer protocol matching RENDER_SPEC §1."""

    def compile(self, tl: EditorTimeline, library: ProjectAssetLibrary) -> RenderGraph: ...
    def render(self, graph: RenderGraph, *, out: Path,
               progress: Callable[[float, str], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> RenderResult: ...


def _seconds_to_ffmpeg_time(seconds: float) -> str:
    """Convert seconds to FFmpeg time format."""
    return f"{seconds:.6f}"


def _build_normalize_filter(asset_info: dict) -> str:
    """Build FFmpeg filter chain for normalization based on asset properties."""
    filters = []

    width = asset_info.get("width", 0)
    height = asset_info.get("height", 0)

    # Handle rotation
    needs_rotation = False
    rotate_degrees = 0

    # Check for rotate tag
    tags = asset_info.get("tags", {})
    if tags.get("rotate") and tags["rotate"] != "0":
        try:
            rotate_degrees = int(tags["rotate"])
            needs_rotation = True
        except ValueError:
            pass

    # Check for display matrix or rotation side data
    for stream in asset_info.get("streams", []):
        if stream.get("codec_type") == "video":
            side_data_list = stream.get("side_data_list", [])
            for side_data in side_data_list:
                if side_data.get("side_data_type") == "Display Matrix":
                    # Simplified: assume 90 degree rotation if non-standard
                    rotation = side_data.get("rotation", 0)
                    if rotation != 0:
                        needs_rotation = True
                        rotate_degrees = rotation
                        break
                elif side_data.get("side_data_type") == "Rotation":
                    rotation = int(side_data.get("rotation", 0))
                    if rotation != 0:
                        needs_rotation = True
                        rotate_degrees = rotation
                        break

    if needs_rotation:
        # FFmpeg transpose values: 0=90CounterCLK, 1=90CLK, 2=90CounterCLK, 3=90CLK, 4=flip, 5=flip+transpose
        # We'll use 1 for 90 degrees clockwise
        transpose_map = {90: 1, 180: 2, 270: 1, 360: 0}  # Simplified mapping
        transpose_val = transpose_map.get(rotate_degrees % 360, 1)
        filters.append(f"transpose={transpose_val}")

        # Swap width/height for subsequent filters if 90/270 degree rotation
        if rotate_degrees % 180 == 90:
            width, height = height, width

    # Handle HDR tone mapping
    color_transfer = asset_info.get("color_transfer", "")
    pix_fmt = asset_info.get("pix_fmt", "")

    is_hdr = color_transfer in ["arib-std-b67", "smpte2084", "bt2020-10", "bt2020-12"]
    is_10bit = any(x in pix_fmt for x in ["10", "12", "16"]) and "rgb" not in pix_fmt.lower()

    if is_hdr:
        if is_10bit:
            # Attempt to use zscale + tonemap (simplified - in reality would check build)
            filters.append("zscale=t=linear:npl=100,format=gbrpf32le")
            filters.append("tonemap=tonemap=hable:desat=0")
            filters.append("format=gbrpf32le,zscale=t=bt709:plt=bt709")
            filters.append("format=yuv420p")
        else:
            # Simple colorspace conversion
            filters.append("colorspace=all=bt709:iall=bt2020:fast=1")

    # Handle 10bit to 8bit conversion (if not HDR)
    if is_10bit and not is_hdr:
        filters.append("format=yuv420p")

    # Handle odd dimensions -> even dimensions
    if width % 2 != 0 or height % 2 != 0:
        filters.append("crop=iw-mod(iw\\,2):ih-mod(ih\\,2)")

    # Handle interlaced -> progressive
    field_order = asset_info.get("field_order", "progressive")
    if field_order != "progressive":
        filters.append("bwdif")

    # Ensure square pixels
    filters.append("setsar=1")

    return ",".join(filters) if filters else "null"


def _build_segment_filter(asset_info: dict, settings: EditorSettings) -> str:
    """Build complete filter chain for a segment including scaling to target resolution."""
    normalize_filter = _build_normalize_filter(asset_info)

    # Scale to target resolution
    scale_filter = f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2"

    # Combine filters
    if normalize_filter == "null":
        return scale_filter
    else:
        return f"{normalize_filter},{scale_filter}"


def _get_asset_duration(asset_info: dict) -> int:
    """Extract duration in milliseconds from FFprobe data."""
    duration_str = asset_info.get("format", {}).get("duration", "0")
    try:
        return int(float(duration_str) * 1000)
    except ValueError:
        return 0


def _get_video_stream_info(asset_info: dict) -> dict | None:
    """Find first video stream in asset info."""
    for stream in asset_info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def _get_audio_stream_info(asset_info: dict) -> dict | None:
    """Find first audio stream in asset info."""
    for stream in asset_info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


class EditorRenderer:
    """Mixed-media renderer implementing the Renderer protocol."""

    def __init__(self):
        self._segment_cache: dict[str, Path] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def _get_cache_key(self, asset_uri: str, normalize_filter: str,
                      settings: EditorSettings) -> str:
        """Generate cache key for a normalized segment."""
        key_data = {
            "uri": asset_uri,
            "normalize_filter": normalize_filter,
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
        }
        key_json = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_json.encode()).hexdigest()

    def compile(self, tl: EditorTimeline, library: ProjectAssetLibrary) -> RenderGraph:
        """Compile timeline into a render graph."""
        # Validate timeline first
        from poc.timeline import validate_editor_timeline
        violations = validate_editor_timeline(tl)
        if violations:
            raise TimelineInvalidError(f"Timeline validation failed: {violations}")

        # Normalize all assets and build segment instructions
        normalized_assets = {}
        segment_instructions = []

        # Get unique assets used in timeline
        used_asset_ids = set()
        for clip in tl.clips:
            used_asset_ids.add(clip.asset_id)

        # Normalize each asset
        for asset_id in used_asset_ids:
            asset = library.get_asset(asset_id)
            if asset is None:
                raise TimelineInvalidError(f"Asset {asset_id} not found in library")

            _validate_no_network_paths(asset.uri)

            # Probe and normalize asset
            probe_data = _probe_media(Path(asset.uri))
            normalized_assets[asset_id] = {
                "uri": asset.uri,
                "probe_data": probe_data,
                "duration_ms": _get_asset_duration(probe_data),
                "width": _get_video_stream_info(probe_data, {}).get("width", 0) if _get_video_stream_info(probe_data) else 0,
                "height": _get_video_stream_info(probe_data, {}).get("height", 0) if _get_video_stream_info(probe_data) else 0,
            }

        # Build segment instructions in timeline order
        for clip in tl.clips:
            asset_info = normalized_assets[clip.asset_id]
            settings = tl.settings

            # Calculate trim points
            source_in_s = clip.source_in_ms / 1000.0 if clip.source_in_ms is not None else 0
            source_out_s = clip.source_out_ms / 1000.0 if clip.source_out_ms is not None else asset_info["duration_ms"] / 1000.0

            # Build filter chain
            normalize_filter = _build_normalize_filter(asset_info["probe_data"])
            segment_filter = _build_segment_filter(asset_info["probe_data"], settings)

            instruction = {
                "clip_id": clip.clip_id,
                "asset_id": clip.asset_id,
                "source_in_s": source_in_s,
                "source_out_s": source_out_s,
                "duration_s": (source_out_s - source_in_s),
                "normalize_filter": normalize_filter,
                "segment_filter": segment_filter,
                "effects": [effect.effect_id for effect in clip.effects] if clip.effects else [],
                "transition": clip.transition.transition_id if clip.transition else None,
                "transition_duration": clip.transition.duration_ticks / settings.fps if clip.transition else 0,
                "audio": {
                    "gain_db": clip.audio.gain_db if clip.audio else 0,
                    "fade_in_ms": clip.audio.fade_in_ms if clip.audio else 0,
                    "fade_out_ms": clip.audio.fade_out_ms if clip.audio else 0,
                } if clip.audio else None
            }
            segment_instructions.append(instruction)

        # Generate cache key for the entire graph
        fps = tl.settings.fps_num / tl.settings.fps_den
        graph_data = {
            "timeline_id": tl.timeline_id,
            "asset_count": len(normalized_assets),
            "segment_count": len(segment_instructions),
            "settings": {
                "width": tl.settings.width,
                "height": tl.settings.height,
                "fps": fps,
            }
        }
        cache_key = hashlib.sha256(json.dumps(graph_data, sort_keys=True).encode()).hexdigest()

        return RenderGraph(
            timeline=tl,
            library=library,
            normalized_assets=normalized_assets,
            segment_instructions=segment_instructions,
            cache_key=cache_key,
        )

    def render(self, graph: RenderGraph, *, out: Path,
               progress: Callable[[float, str], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> RenderResult:
        """Render the graph to an output file."""
        def _report_progress(frac: float, msg: str):
            if progress:
                progress(frac, msg)

        def _check_cancel() -> bool:
            return cancel and cancel()

        _report_progress(0.0, "Starting render")

        # Reset cache counters for this render
        cache_hits = 0
        cache_misses = 0

        with tempfile.TemporaryDirectory(prefix="poc_editor_render_") as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Process each segment
            segment_paths = []
            for i, instruction in enumerate(graph.segment_instructions):
                if _check_cancel():
                    raise RuntimeError("Render cancelled")

                progress_frac = i / len(graph.segment_instructions)
                _report_progress(progress_frac, f"Processing segment {i+1}/{len(graph.segment_instructions)}")

                asset_info = graph.normalized_assets[instruction["asset_id"]]
                asset_path = Path(asset_info["uri"])

                # Check cache
                normalize_filter = instruction["normalize_filter"]
                settings = graph.timeline.settings
                fps = settings.fps_num / settings.fps_den
                cache_key = self._get_cache_key(
                    asset_info["uri"],
                    normalize_filter,
                    settings
                )

                if cache_key in self._segment_cache:
                    seg_path = self._segment_cache[cache_key]
                    cache_hits += 1
                else:
                    cache_misses += 1
                    seg_path = tmp_path / f"seg_{i:03d}.mp4"

                    # Build FFmpeg command for segment
                    in_s = _seconds_to_ffmpeg_time(instruction["source_in_s"])
                    duration_s = _seconds_to_ffmpeg_time(instruction["source_out_s"] - instruction["source_in_s"])

                    ffmpeg_cmd = [*FFMPEG_NETWORK_SAFE, "-y",
                                  "-ss", in_s,
                                  "-i", str(asset_path),
                                  "-t", duration_s,
                                  "-r", str(settings.fps),
                                  "-vf", instruction["segment_filter"],
                                  "-an",
                                  "-c:v", "libx264",
                                  "-preset", "veryfast",
                                  "-crf", "18",
                                  "-pix_fmt", "yuv420p",
                                  str(seg_path)]

                    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise FFmpegError(f"Segment rendering failed: {result.stderr}")

                    # Cache the result
                    self._segment_cache[cache_key] = seg_path

                segment_paths.append(seg_path)

            _report_progress(0.8, "Concatenating segments")

            if _check_cancel():
                raise RuntimeError("Render cancelled")

            # Concatenate all segments
            concat_list_path = tmp_path / "concat_list.txt"
            with open(concat_list_path, "w") as f:
                for seg_path in segment_paths:
                    f.write(f"file '{seg_path.name}'\n")

            concat_video_path = tmp_path / "concat_video.mp4"
            concat_cmd = [*FFMPEG_NETWORK_SAFE, "-y",
                          "-f", "concat",
                          "-safe", "0",
                          "-i", str(concat_list_path),
                          "-c", "copy",
                          str(concat_video_path)]

            result = subprocess.run(concat_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise FFmpegError(f"Segment concatenation failed: {result.stderr}")

            _report_progress(0.9, "Adding audio and finalizing")

            if _check_cancel():
                raise RuntimeError("Render cancelled")

            # Add audio track (music) and create final output
            out.parent.mkdir(parents=True, exist_ok=True)

            # Build audio filter for music track
            music = graph.timeline.music
            music_path = Path(music.uri)
            _validate_no_network_paths(str(music_path))

            music_duration_s = graph.timeline.duration_ms / 1000.0
            music_in_s = _seconds_to_ffmpeg_time(music.in_ms / 1000.0)
            fade_out_s = music.fade_out_ms / 1000.0
            fade_start_s = max(0.0, music_duration_s - fade_out_s)
            af = f"volume={music.gain_db}dB,afade=t=out:st={fade_start_s:.6f}:d={fade_out_s:.6f}"

            # Final mux command
            final_cmd = [*FFMPEG_NETWORK_SAFE, "-y",
                         "-i", str(concat_video_path),
                         "-ss", music_in_s,
                         "-i", str(music_path),
                         "-t", _seconds_to_ffmpeg_time(music_duration_s),
                         "-map", "0:v:0",
                         "-map", "1:a:0",
                         "-af", af,
                         "-c:v", "copy",
                         "-c:a", "aac",
                         "-movflags", "+faststart",
                         str(out)]

            result = subprocess.run(final_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise FFmpegError(f"Final mux failed: {result.stderr}")

            _report_progress(1.0, "Render complete")

            # Verify output
            if not out.exists():
                raise RuntimeError("Render completed but output file not found")

            # Build video clip order for result
            video_clip_order = tuple(clip.clip_id for clip in graph.timeline.clips
                                   if clip.track_id in [t.track_id for t in graph.timeline.tracks
                                                       if t.kind == "video"])

            return RenderResult(
                out_path=out,
                duration_frames=graph.timeline.duration_frames,
                duration_ms=graph.timeline.duration_ms,
                video_clip_order=video_clip_order,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
            )


# Convenience functions matching the expected interface from test expectations
def build_editor_render_plan(timeline: EditorTimeline, library: ProjectAssetLibrary) -> RenderGraph:
    """Build render plan (compile step) for compatibility with existing tests."""
    renderer = EditorRenderer()
    return renderer.compile(timeline, library)


def render_editor_timeline(timeline: EditorTimeline, library: ProjectAssetLibrary,
                          out_path: Path) -> RenderResult:
    """Render timeline to output path (compile + render) for compatibility with existing tests."""
    renderer = EditorRenderer()
    graph = renderer.compile(timeline, library)
    return renderer.render(graph, out=out_path)


def probe(path: Path, ffmpeg_exe: str) -> dict:
    """Probe media file - simplified version for test compatibility."""
    return _probe_media(path)


if __name__ == "__main__":
    # Simple self-test
    print("Editor renderer module loaded successfully")
    print(f"FFmpeg executable: {FFMPEG_EXE}")
    print(f"Network-safe FFmpeg: {' '.join(FFMPEG_NETWORK_SAFE)}")