"""poc/ingest/normalize.py: Ingest normalization for media assets.

Handles variable frame rate, rotation, HDR, odd dimensions, interlaced audio,
missing/odd audio, Unicode/long paths, and still images as per RENDER_SPEC §2.
Implements FFmpeg network gap fixes: -protocol_whitelist file,pipe and rejects
paths containing "://" with clear error.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def normalize_asset(asset: MediaAsset) -> NormalizedAsset:
    """Normalize a media asset according to RENDER_SPEC §2.

    Returns NormalizedAsset with properties that reflect the decoded/processed
    state after applying all necessary filters.
    """
    _validate_no_network_paths(asset.uri)

    # Probe the media file
    probe_cmd = [*FFMPEG_NETWORK_SAFE, "-v", "error", "-show_entries",
                 "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_transfer,side_data_list",
                 "-show_entries", "format=duration",
                 "-of", "json", str(asset.uri)]

    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed for {asset.uri}: {result.stderr}")

    import json
    probe_data = json.loads(result.stdout)

    # Extract video stream info
    video_stream = None
    audio_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = stream

    if video_stream is None:
        # Still image case
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
    is_hdr = color_transfer in ["arib-std-b67", "smpte2084", "smpte2084", "bt2020-10", "bt2020-12"]

    # Rotation detection
    needs_rotation = False
    display_matrix = video_stream.get("side_data_list", [])
    for side_data in display_matrix:
        if side_data.get("side_data_type") == "Display Matrix":
            # Check for rotation in display matrix
            rotation = side_data.get("rotation", 0)
            if rotation != 0:
                needs_rotation = True
                break
        elif side_data.get("side_data_type") == "Rotation":
            rotation = int(side_data.get("rotation", 0))
            if rotation != 0:
                needs_rotation = True
                break

    # Check for rotate tag in format tags
    rotate_tag = probe_data.get("format", {}).get("tags", {}).get("rotate")
    if rotate_tag and rotate_tag != "0":
        needs_rotation = True

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
    )


def _normalize_still_image(asset: MediaAsset) -> NormalizedAsset:
    """Handle still image normalization."""
    _validate_no_network_paths(asset.uri)

    # Probe image to get dimensions
    probe_cmd = [*FFMPEG_NETWORK_SAFE, "-v", "error", "-show_entries",
                 "stream=width,height", "-of", "json", str(asset.uri)]

    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed for image {asset.uri}: {result.stderr}")

    import json
    probe_data = json.loads(result.stdout)

    video_stream = probe_data.get("streams", [{}])[0]
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

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

    Returns a filtergraph string suitable for -vf.
    """
    filters = []

    # Handle rotation (if needed, adjust dimensions accordingly)
    if asset.needs_rotation:
        # For 90/270 degree rotation, swap width and height
        # Note: Actual rotation applied by transpose filter
        filters.append("transpose=1")  # 90 degrees clockwise
        # Update dimensions for subsequent filters
        # We'll handle this by checking if we need to swap in scale

    # Handle HDR tone mapping
    if asset.is_hdr:
        if asset.is_10bit:
            # Try to use zscale + tonemap if available (would need build check)
            # For now, mark as HDR but convert with colorspace
            filters.append("zscale=t=linear:npl=100,format=gbrpf32le")
            filters.append("tonemap=tonemap=hable:desat=0")
            filters.append("format=gbrpf32le,zscale=t=bt709:plt=bt709")
            filters.append("format=yuv420p")
        else:
            # Simple colorspace conversion for non-10bit HDR
            filters.append("colorspace=all=bt709:iall=bt2020:fast=1")

    # Handle 10bit to 8bit conversion (if not already handled by HDR)
    if asset.is_10bit and not asset.is_hdr:
        filters.append("format=yuv420p")

    # Handle odd dimensions -> even dimensions
    if asset.needs_even_dimensions:
        # Make dimensions even by cropping or padding
        # Using crop to make even (safer than scaling which might distort)
        filters.append("crop=iw-mod(iw\\,2):ih-mod(ih\\,2)")

    # Handle interlaced -> progressive
    if asset.needs_deinterlace:
        filters.append("bwdif")

    # Handle scaling to target resolution (will be applied later in render pipeline)
    # This normalization step prepares the asset but doesn't finalize scale
    # The render pipeline will handle final scaling to settings.width/height

    # Ensure square pixels
    filters.append("setsar=1")

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