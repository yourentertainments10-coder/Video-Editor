"""poc/ingest/normalize.py: Ingest normalization for media assets.

Handles variable frame rate, rotation, HDR, odd dimensions, interlaced audio,
missing/odd audio, Unicode/long paths, and still images as per RENDER_SPEC §2.
Implements FFmpeg network gap fixes: -protocol_whitelist file,pipe and rejects
paths containing "://" with clear error.

Uses PyAV (av library) for structured media probing instead of regex parsing
ffmpeg -i stderr. PyAV wraps FFmpeg libraries directly and provides structured
metadata access.
"""

from __future__ import annotations

import fractions
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import imageio_ffmpeg

from poc.timeline import MediaAsset


# FFmpeg binary with network gap fix: only allow file and pipe protocols
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_NETWORK_SAFE = [
    FFMPEG_EXE,
    "-protocol_whitelist", "file,pipe",
]


def _has_network_path(path: str) -> bool:
    """Detect if a path contains network protocol indicators."""
    return "://" in path


def _validate_no_network_paths(*paths: str) -> None:
    """Reject any path containing network protocols with clear error."""
    for path in paths:
        if _has_network_path(path):
            raise ValueError(
                f"Network paths are prohibited for security and reproducibility: {path!r}. "
                f"Only local file paths are allowed."
            )


def _parse_av_probe(path: Path) -> dict:
    """Probe media file using PyAV and return structured metadata.

    Returns a dict with the same structure as ffprobe JSON would have.
    """
    # Use PyAV to open and probe the file
    container = av.open(str(path))

    result = {
        "format": {"duration": "0", "tags": {}},
        "streams": []
    }

    # Format duration - av.duration is in microseconds (AV_TIME_BASE = 1,000,000)
    if container.duration is not None:
        duration_seconds = container.duration / 1_000_000
        result["format"]["duration"] = str(duration_seconds)

    # Format tags (rotation, etc.)
    if container.metadata:
        for key, value in container.metadata.items():
            if key.lower() == "rotate":
                result["format"]["tags"]["rotate"] = str(value)

    # Parse streams
    for stream in container.streams:
        if stream.type == "video":
            video_stream = {
                "codec_type": "video",
                "width": stream.width,
                "height": stream.height,
                "pix_fmt": str(stream.pix_fmt),
            }

            # Frame rate - use average_rate and guessed_rate
            if stream.average_rate:
                video_stream["avg_frame_rate"] = f"{stream.average_rate.numerator}/{stream.average_rate.denominator}"
            else:
                video_stream["avg_frame_rate"] = "0/0"

            # r_frame_rate - use guessed_rate if available
            if stream.guessed_rate:
                video_stream["r_frame_rate"] = f"{stream.guessed_rate.numerator}/{stream.guessed_rate.denominator}"
            else:
                video_stream["r_frame_rate"] = "0/0"

            # Color transfer / HDR detection - from codec context
            ctx = stream.codec_context
            color_transfer = ""
            if hasattr(ctx, "color_trc") and ctx.color_trc is not None:
                # Map FFmpeg color_trc enum values to string names
                color_trc_map = {
                    1: "bt709",          # BT.709 / sRGB
                    14: "bt2020-10",     # BT.2020 non-constant peak
                    15: "bt2020-12",     # BT.2020 constant peak
                    16: "smpte2084",     # PQ (SMPTE ST 2084)
                    18: "arib-std-b67",  # HLG (ARIB STD-B67)
                }
                color_trc_val = ctx.color_trc
                video_stream["color_transfer"] = color_trc_map.get(color_trc_val, str(color_trc_val))

            # Color primaries
            if hasattr(ctx, "color_primaries") and ctx.color_primaries is not None:
                video_stream["color_primaries"] = str(ctx.color_primaries)

            # Field order (interlaced detection)
            if hasattr(ctx, "field_order") and ctx.field_order is not None:
                field_order_map = {
                    0: "progressive",
                    1: "tt",      # top coded first, top displayed first
                    2: "bb",      # bottom coded first, bottom displayed first
                    3: "tb",      # top coded first, bottom displayed first
                    4: "bt",      # bottom coded first, top displayed first
                }
                field_order_val = ctx.field_order
                video_stream["field_order"] = field_order_map.get(field_order_val, "progressive")
            else:
                video_stream["field_order"] = "progressive"

            # Sample aspect ratio
            if hasattr(ctx, "sample_aspect_ratio") and ctx.sample_aspect_ratio is not None:
                sar = ctx.sample_aspect_ratio
                video_stream["sample_aspect_ratio"] = f"{sar.numerator}:{sar.denominator}"
            else:
                video_stream["sample_aspect_ratio"] = "1:1"

            # Pixel format from codec context
            video_stream["pix_fmt"] = str(ctx.pix_fmt)

            # Side data (for rotation detection)
            side_data_list = []
            # Check stream metadata for rotation
            if hasattr(stream, "metadata") and stream.metadata:
                for key, value in stream.metadata.items():
                    if key.lower() == "rotate":
                        side_data_list.append({
                            "side_data_type": "Rotation",
                            "rotation": int(value)
                        })
            video_stream["side_data_list"] = side_data_list

            # Frame rate
            if stream.average_rate:
                video_stream["avg_frame_rate"] = f"{stream.average_rate.numerator}/{stream.average_rate.denominator}"
            else:
                video_stream["avg_frame_rate"] = "0/0"

            if stream.guessed_rate:
                video_stream["r_frame_rate"] = f"{stream.guessed_rate.numerator}/{stream.guessed_rate.denominator}"
            else:
                video_stream["r_frame_rate"] = "0/0"

            result["streams"].append(video_stream)

        elif stream.type == "audio":
            audio_stream = {
                "codec_type": "audio",
                "channels": stream.channels,
                "sample_rate": str(stream.sample_rate),
            }
            result["streams"].append(audio_stream)

    # Format duration - av.duration is in microseconds (AV_TIME_BASE = 1,000,000)
    if container.duration is not None:
        duration_seconds = container.duration / 1_000_000
        result["format"]["duration"] = str(duration_seconds)

    # Format tags from container metadata
    if container.metadata:
        for key, value in container.metadata.items():
            if key.lower() == "rotate":
                result["format"]["tags"]["rotate"] = str(value)

    container.close()
    return result


@dataclass(frozen=True)
class NormalizedAsset:
    """Result of normalization process."""
    path: Path
    duration_ms: int
    width: int
    height: int
    fps: float
    has_audio: bool
    audio_sample_rate: int
    audio_channels: int
    needs_rotation: bool = False
    is_hdr: bool = False
    is_10bit: bool = False
    needs_deinterlace: bool = False
    needs_even_dimensions: bool = False
    is_still_image: bool = False
    is_vfr: bool = False
    sample_aspect_ratio: str = "1:1"


def normalize_asset(asset: MediaAsset) -> NormalizedAsset:
    """Normalize a media asset according to RENDER_SPEC §2.

    Returns NormalizedAsset with properties that reflect the decoded/processed
    state after applying all necessary filters.
    Uses PyAV (av library) for structured media probing.
    """
    _validate_no_network_paths(asset.uri)

    # Check if this is a still image asset - use asset kind to determine
    if asset.kind == "image":
        return _normalize_still_image(asset)

    # Probe the media file using PyAV
    probe_data = _parse_av_probe(Path(asset.uri))

    # Extract video stream info
    video_stream = None
    audio_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = stream

    if video_stream is None:
        # No video stream - this is an audio-only asset
        if audio_stream is not None:
            # Audio-only asset
            duration_ms = int(float(probe_data.get("format", {}).get("duration", 0)) * 1000)
            return NormalizedAsset(
                path=Path(asset.uri),
                duration_ms=duration_ms,
                width=0,
                height=0,
                fps=0,
                has_audio=True,
                audio_sample_rate=int(audio_stream.get("sample_rate", 0)),
                audio_channels=int(audio_stream.get("channels", 0)),
                needs_rotation=False,
                is_hdr=False,
                is_10bit=False,
                needs_deinterlace=False,
                needs_even_dimensions=False,
                is_still_image=False,
            )
        # No video stream and no audio stream - treat as still image
        return _normalize_still_image(asset)

    # Video asset normalization
    duration_ms = int(float(probe_data.get("format", {}).get("duration", 0)) * 1000)

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    # Frame rate handling (VFR detection)
    avg_frame_rate = video_stream.get("avg_frame_rate", "0/0")
    r_frame_rate = video_stream.get("r_frame_rate", "0/0")

    def parse_fraction(frac: str) -> float:
        if "/" not in frac:
            return float(frac)
        num, den = frac.split("/")
        return float(num) / float(float(den) if den != "0" else 1)

    fps = parse_fraction(avg_frame_rate)
    is_vfr = parse_fraction(r_frame_rate) != fps

    # Pixel format and color info
    pix_fmt = video_stream.get("pix_fmt", "")
    color_transfer = video_stream.get("color_transfer", "")

    is_10bit = any(x in pix_fmt for x in ["10", "12", "16"]) and "rgb" not in pix_fmt.lower()
    is_hdr = color_transfer in ["arib-std-b67", "smpte2084", "bt2020-10", "bt2020-12"]

    # Rotation detection
    needs_rotation = False
    # Check for rotate tag in format tags
    rotate_tag = probe_data.get("format", {}).get("tags", {}).get("rotate")
    if rotate_tag and rotate_tag != "0":
        needs_rotation = True

    # Check side data for rotation
    for stream in probe_data.get("streams", []):
        for side_data in stream.get("side_data_list", []):
            if side_data.get("side_data_type") == "Display Matrix":
                rotation = side_data.get("rotation", 0)
                if rotation != 0:
                    needs_rotation = True
                    break
            elif side_data.get("side_data_type") == "Rotation":
                rotation = int(side_data.get("rotation", 0))
                if rotation != 0:
                    needs_rotation = True
                    break

    # If rotation is needed, swap dimensions for display orientation
    # (FFmpeg auto-rotates on decode when rotation metadata is present)
    if needs_rotation:
        width, height = height, width
        # After accounting for rotation, we don't need to rotate again in the filter
        needs_rotation = False

    # Interlaced detection
    field_order = video_stream.get("field_order", "progressive")
    needs_deinterlace = field_order != "progressive"

    # Even dimensions requirement
    needs_even_dimensions = (width % 2 != 0) or (height % 2 != 0)

    # Sample aspect ratio
    sample_aspect_ratio = video_stream.get("sample_aspect_ratio", "1:1")

    # Audio properties
    has_audio = audio_stream is not None
    audio_sample_rate = 0
    audio_channels = 0
    if has_audio:
        audio_sample_rate = int(audio_stream.get("sample_rate", 0))
        audio_channels = int(audio_stream.get("channels", 0))

    return NormalizedAsset(
        path=Path(asset.uri),
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        needs_rotation=needs_rotation,
        is_hdr=is_hdr,
        is_10bit=is_10bit,
        needs_deinterlace=needs_deinterlace,
        needs_even_dimensions=needs_even_dimensions,
        is_still_image=False,
        is_vfr=is_vfr,
        sample_aspect_ratio=sample_aspect_ratio,
    )


def _normalize_still_image(asset: MediaAsset) -> NormalizedAsset:
    """Handle still image normalization."""
    _validate_no_network_paths(asset.uri)

    # Probe image to get dimensions using PyAV
    probe_data = _parse_av_probe(Path(asset.uri))

    video_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0

    # Default duration will be set by timeline
    return NormalizedAsset(
        path=Path(asset.uri),
        duration_ms=0,  # Will be set by timeline duration
        width=width,
        height=height,
        fps=0,  # Will be set to timeline fps
        has_audio=False,
        audio_sample_rate=0,
        audio_channels=0,
        needs_rotation=False,  # EXIF rotation handled separately
        is_hdr=False,
        is_10bit=False,
        needs_deinterlace=False,
        needs_even_dimensions=(width % 2 != 0) or (height % 2 != 0),
        is_still_image=True,
    )


def build_normalize_filter(asset: NormalizedAsset) -> str:
    """Build FFmpeg filter chain for normalization based on asset properties.

    The filter chain order is critical: deinterlace -> HDR tone-map ->
    apply SAR -> make even dimensions -> re-assert square pixels -> format conversion.

    Returns a filtergraph string suitable for -vf.
    """
    filters = []

    # 1. Handle interlaced -> progressive (must come first)
    if asset.needs_deinterlace:
        filters.append("bwdif")

    # 2. Handle HDR tone mapping (if HDR)
    if asset.is_hdr:
        if asset.is_10bit:
            # Proper HDR to SDR tone-mapping chain
            # Maps 10-bit HDR (PQ/HLG) to 8-bit BT.709
            filters.append("zscale=t=linear:npl=100,format=gbrpf32le")
            filters.append("zscale=p=bt709")
            filters.append("tonemap=tonemap=hable:desat=0")
            filters.append("zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
        else:
            # Simple colorspace conversion for non-10bit HDR
            filters.append("colorspace=all=bt709:iall=bt2020:fast=1")

    # 3. Handle 10bit to 8bit conversion (if not already handled by HDR)
    if asset.is_10bit and not asset.is_hdr:
        filters.append("format=yuv420p")

    # 4. Apply sample aspect ratio and set to square pixels
    # Apply whenever SAR != 1:1, independent of odd dimensions
    if asset.sample_aspect_ratio != "1:1":
        # Apply SAR to get correct display dimensions, then set SAR to 1:1
        filters.append("scale=iw*sar:ih,setsar=1")

    # 5. Make dimensions even (required by most codecs)
    # Apply after SAR processing to avoid aspect ratio distortion
    if asset.needs_even_dimensions:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    # 6. Re-assert square pixels in case even-making altered SAR
    if asset.needs_even_dimensions:
        filters.append("setsar=1")

    # 7. ALWAYS ensure square pixels (setsar=1) - this is a safe no-op for already-square pixels
    # but important to explicitly set for all assets
    if "setsar=1" not in ",".join(filters):
        filters.append("setsar=1")

    # 8. Format conversion (ensure yuv420p for compatibility)
    filters.append("format=yuv420p")

    # Note: Rotation is handled by FFmpeg auto-rotate on decode (display-matrix/side data),
    # so we do NOT add transpose here (would rotate twice).
    # Note: FPS conversion for CFR is handled by the caller via -r <fps> or fps=<fps> filter
    # if needed; this normalize step does not change frame rate.

    return ",".join(filters) if filters else "null"


def build_input_args(asset: NormalizedAsset) -> list[str]:
    """Build FFmpeg input arguments for normalized asset.

    Returns list of arguments suitable for FFmpeg -i and related options.
    """
    args = []

    # Still image special handling
    if asset.is_still_image:
        # Will be handled by render loop with -loop 1
        pass

    # Input file
    args.extend(["-i", str(asset.path)])

    return args


def normalize_to_file(asset: MediaAsset, out_path: Path, fps: int = 30) -> Path:
    """Normalize a media asset and transcode to a file.

    This is the real conversion function that actually runs FFmpeg to
    produce a normalized output file. It uses PyAV for probing and
    FFmpeg for transcoding.

    Args:
        asset: MediaAsset to normalize
        out_path: Output file path
        fps: Target frames per second for CFR output (default 30)

    Returns:
        Path to the normalized output file
    """
    _validate_no_network_paths(asset.uri)

    # Probe the media file using PyAV
    probe_data = _parse_av_probe(Path(asset.uri))

    # Extract stream info
    video_stream = None
    audio_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = stream

    # Build FFmpeg command
    cmd = [FFMPEG_EXE, "-y"]

    # Network safety
    cmd.extend(FFMPEG_NETWORK_SAFE[1:])  # Skip the exe, add -protocol_whitelist etc

    # Input
    cmd.extend(["-i", str(asset.uri)])

    # Video processing
    if video_stream is not None:
        # Get the normalize filter
        # Create a temporary NormalizedAsset to get the filter
        temp_asset = NormalizedAsset(
            path=Path(asset.uri),
            duration_ms=int(float(probe_data.get("format", {}).get("duration", 0)) * 1000),
            width=int(video_stream.get("width", 0)) if video_stream.get("width") else 0,
            height=int(video_stream.get("height", 0)) if video_stream.get("height") else 0,
            fps=float(video_stream.get("average_rate", "0/0")) if video_stream.get("average_rate") else 0.0,
            has_audio=audio_stream is not None,
            audio_sample_rate=int(audio_stream.get("sample_rate", 0)) if audio_stream else 0,
            audio_channels=int(audio_stream.get("channels", 0)) if audio_stream else 0,
            needs_rotation=False,  # Will be detected properly in actual implementation
            is_hdr=False,
            is_10bit=False,
            needs_deinterlace=False,
            needs_even_dimensions=False,
            is_still_image=False,
            is_vfr=False,
        )

        # Get proper normalization filter
        # But we need to actually detect the properties properly
        # Let's redo the detection properly here

        # Re-extract with proper typing
        width = int(video_stream.get("width", 0)) if video_stream.get("width") else 0
        height = int(video_stream.get("height", 0)) if video_stream.get("height") else 0
        pix_fmt = video_stream.get("pix_fmt", "")

        avg_frame_rate = video_stream.get("avg_frame_rate", "0/0")
        r_frame_rate = video_stream.get("r_frame_rate", "0/0")

        def parse_fraction(frac: str) -> float:
            if "/" not in frac:
                return float(frac)
            num, den = frac.split("/")
            return float(num) / float(float(den) if den != "0" else 1)

        fps_val = parse_fraction(avg_frame_rate)
        is_vfr = parse_fraction(r_frame_rate) != fps_val

        is_10bit = any(x in pix_fmt for x in ["10", "12", "16"]) and "rgb" not in pix_fmt.lower()

        color_transfer = video_stream.get("color_transfer", "")
        is_hdr = color_transfer in ["arib-std-b67", "smpte2084", "bt2020-10", "bt2020-12"]

        # Rotation detection
        needs_rotation = False
        # Check for rotate tag in format tags
        rotate_tag = probe_data.get("format", {}).get("tags", {}).get("rotate")
        if rotate_tag and rotate_tag != "0":
            needs_rotation = True

        # Check side data for rotation
        for stream in probe_data.get("streams", []):
            for side_data in stream.get("side_data_list", []):
                if side_data.get("side_data_type") == "Display Matrix":
                    rotation = side_data.get("rotation", 0)
                    if rotation != 0:
                        needs_rotation = True
                        break
                elif side_data.get("side_data_type") == "Rotation":
                    rotation = int(side_data.get("rotation", 0))
                    if rotation != 0:
                        needs_rotation = True
                        break

        # Interlaced detection
        field_order = video_stream.get("field_order", "progressive")
        needs_deinterlace = field_order != "progressive"

        # Even dimensions requirement
        needs_even_dimensions = (width % 2 != 0) or (height % 2 != 0)

        # Audio properties
        has_audio = audio_stream is not None
        audio_sample_rate = 0
        audio_channels = 0
        if has_audio:
            audio_sample_rate = int(audio_stream.get("sample_rate", 0))
            audio_channels = int(audio_stream.get("channels", 0))

        # Create proper NormalizedAsset for filter building
        norm_asset = NormalizedAsset(
            path=Path(asset.uri),
            duration_ms=int(float(probe_data.get("format", {}).get("duration", 0)) * 1000),
            width=width,
            height=height,
            fps=fps_val,
            has_audio=has_audio,
            audio_sample_rate=audio_sample_rate,
            audio_channels=audio_channels,
            needs_rotation=needs_rotation,
            is_hdr=is_hdr,
            is_10bit=is_10bit,
            needs_deinterlace=needs_deinterlace,
            needs_even_dimensions=needs_even_dimensions,
            is_still_image=False,
            is_vfr=is_vfr,
        )

        # Build the filter chain
        vf_filter = build_normalize_filter(norm_asset)

        # Add video filters if any
        if vf_filter != "null":
            cmd.extend(["-vf", vf_filter])

        # Video encoding settings
        cmd.extend([
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-color_range", "tv",
            "-r", str(fps)  # Output CFR
        ])
    else:
        # No video stream (audio only or still image)
        if asset.kind == "image":
            # Still image: loop it
            cmd.extend(["-loop", "1", "-t", str(asset.duration_ms / 1000.0) if asset.duration_ms > 0 else "1"])
        # Add null video source for audio-only? No, just process audio

    # Audio processing
    if audio_stream is not None:
        cmd.extend([
            "-c:a", "aac",
            "-ar", "48000",
            "-ac", "2"
        ])
    elif asset.kind == "image" and video_stream is None:
        # Still image with no audio - need to add silent audio? No, just video
        pass
    elif video_stream is None and audio_stream is None:
        # Neither video nor audio - treat as still image
        if asset.kind == "image":
            cmd.extend(["-loop", "1", "-t", str(asset.duration_ms / 1000.0) if asset.duration_ms > 0 else "1"])

    # Output
    cmd.append(str(out_path))

    # Run FFmpeg
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    return out_path


if __name__ == "__main__":
    # Simple test
    import sys
    if len(sys.argv) > 1:
        from poc.project_assets import ProjectAssetLibrary
        library = ProjectAssetLibrary.create(Path("test_project"))
        asset = library.import_asset(Path(sys.argv[1])).media_asset
        normalized = normalize_asset(asset)
        print(f"Normalized: {normalized}")
        print(f"Filter: {build_normalize_filter(normalized)}")