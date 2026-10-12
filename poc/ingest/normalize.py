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

import subprocess
import math
import os
from dataclasses import dataclass
from pathlib import Path

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


def _exif_orientation(path: str | Path) -> int:
    """Return a local image's EXIF orientation, defaulting safely to 1."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            orientation = int(image.getexif().get(0x0112, 1))
        return orientation if orientation in range(1, 9) else 1
    except ImportError:
        import struct

        try:
            data = Path(path).read_bytes()
            if not data.startswith(b"\xff\xd8"):
                return 1
            offset = 2
            while offset + 4 <= len(data):
                if data[offset] != 0xFF:
                    break
                marker = data[offset + 1]
                length = int.from_bytes(data[offset + 2:offset + 4], "big")
                payload = data[offset + 4:offset + 2 + length]
                if marker == 0xE1 and payload.startswith(b"Exif\0\0"):
                    tiff = payload[6:]
                    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else None
                    if endian is None:
                        return 1
                    ifd = struct.unpack_from(endian + "I", tiff, 4)[0]
                    entries = struct.unpack_from(endian + "H", tiff, ifd)[0]
                    for index in range(entries):
                        entry = ifd + 2 + index * 12
                        tag, kind, count = struct.unpack_from(endian + "HHI", tiff, entry)
                        if tag == 0x0112 and kind == 3 and count >= 1:
                            value = struct.unpack_from(endian + "H", tiff, entry + 8)[0]
                            return value if value in range(1, 9) else 1
                    return 1
                if marker in (0xDA, 0xD9):
                    break
                offset += 2 + length
        except (OSError, ValueError, struct.error):
            pass
        return 1
    except (OSError, ValueError, TypeError):
        return 1


def _parse_av_probe(path: Path) -> dict:
    """Probe media file using PyAV and return structured metadata.

    Returns a dict with the same structure as ffprobe JSON would have.
    """
    result = {"format": {"duration": "0", "tags": {}}, "streams": []}
    with av.open(str(path)) as container:
        if container.duration is not None:
            result["format"]["duration"] = str(container.duration / 1_000_000)
        for key, value in (container.metadata or {}).items():
            if key.lower() == "rotate":
                result["format"]["tags"]["rotate"] = str(value)

        for stream in container.streams:
            if stream.type == "video":
                ctx = stream.codec_context
                video_stream = {
                    "codec_type": "video", "width": stream.width,
                    "height": stream.height, "pix_fmt": str(ctx.pix_fmt),
                    "avg_frame_rate": (
                        f"{stream.average_rate.numerator}/{stream.average_rate.denominator}"
                        if stream.average_rate else "0/0"
                    ),
                    "r_frame_rate": (
                        f"{stream.guessed_rate.numerator}/{stream.guessed_rate.denominator}"
                        if stream.guessed_rate else "0/0"
                    ),
                }
                color_trc_map = {
                    1: "bt709", 14: "bt2020-10", 15: "bt2020-12",
                    16: "smpte2084", 18: "arib-std-b67",
                }
                if getattr(ctx, "color_trc", None) is not None:
                    video_stream["color_transfer"] = color_trc_map.get(
                        ctx.color_trc, str(ctx.color_trc)
                    )
                if getattr(ctx, "color_primaries", None) is not None:
                    video_stream["color_primaries"] = str(ctx.color_primaries)
                field_order_map = {0: "progressive", 1: "progressive", 2: "tt", 3: "bb", 4: "tb", 5: "bt"}
                video_stream["field_order"] = field_order_map.get(
                    getattr(ctx, "field_order", 0), "progressive"
                )
                sar = getattr(stream, "sample_aspect_ratio", None)
                video_stream["sample_aspect_ratio"] = (
                    f"{sar.numerator}:{sar.denominator}" if sar else "1:1"
                )
                side_data_list = []
                for key, value in (stream.metadata or {}).items():
                    if key.lower() == "rotate":
                        side_data_list.append({"side_data_type": "Rotation", "rotation": int(value)})
                if hasattr(ctx, "coded_side_data"):
                    csd = ctx.coded_side_data
                    if isinstance(csd, dict) and "display_matrix" in csd:
                        import struct
                        try:
                            raw_bytes = csd["display_matrix"]
                            if len(raw_bytes) >= 36:
                                matrix = struct.unpack("<9i", raw_bytes[:36])
                                angle = round(math.degrees(math.atan2(matrix[3], matrix[0]))) % 360
                                if angle in (90, 180, 270):
                                    side_data_list.append({"side_data_type": "Display Matrix", "rotation": angle})
                        except (TypeError, ValueError, struct.error):
                            pass
                video_stream["side_data_list"] = side_data_list
                result["streams"].append(video_stream)
            elif stream.type == "audio":
                result["streams"].append({
                    "codec_type": "audio", "channels": stream.channels,
                    "sample_rate": str(stream.sample_rate),
                })
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
    rotation_degrees: int = 0


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

    # Rotation detection. Keep the source display angle and expose display
    # dimensions, swapping only for quarter turns.
    rotation_degrees = 0
    rotate_tag = probe_data.get("format", {}).get("tags", {}).get("rotate")
    if rotate_tag:
        try:
            rotation_degrees = int(round(float(rotate_tag))) % 360
        except (TypeError, ValueError):
            pass

    for stream in probe_data.get("streams", []):
        for side_data in stream.get("side_data_list", []):
            if side_data.get("side_data_type") in ("Display Matrix", "Rotation"):
                try:
                    rotation = int(round(float(side_data.get("rotation", 0)))) % 360
                except (TypeError, ValueError):
                    continue
                if rotation:
                    rotation_degrees = rotation
                    break

    if rotation_degrees in (90, 270):
        width, height = height, width
    # FFmpeg applies display matrix rotation during decode automatically.
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
        rotation_degrees=rotation_degrees,
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
    orientation = _exif_orientation(asset.uri)
    if orientation in (5, 6, 7, 8):
        width, height = height, width

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

    if asset.is_still_image:
        orientation = _exif_orientation(asset.path)
        orientation_filters = {
            2: ["hflip"],
            3: ["hflip", "vflip"],
            4: ["vflip"],
            5: ["transpose=1", "hflip"],
            6: ["transpose=1"],
            7: ["transpose=1", "vflip"],
            8: ["transpose=2"],
        }
        filters.extend(orientation_filters.get(orientation, []))

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
    # Check if dimensions need to be made even after SAR application
    needs_even_after_sar = False
    if asset.sample_aspect_ratio != "1:1":
        # Parse SAR to calculate if resulting dimensions would be odd
        try:
            sar_parts = asset.sample_aspect_ratio.split(":")
            if len(sar_parts) == 2:
                sar_num, sar_den = int(sar_parts[0]), int(sar_parts[1])
                # Calculate display dimensions after SAR application
                display_width = asset.width * sar_num / sar_den
                display_height = asset.height
                # Check if either dimension is odd (would need to be made even)
                if display_width % 2 != 0 or display_height % 2 != 0:
                    needs_even_after_sar = True
        except (ValueError, ZeroDivisionError):
            # If we can't parse SAR, fall back to original check
            needs_even_after_sar = asset.needs_even_dimensions
    else:
        # No SAR adjustment, use original check
        needs_even_after_sar = asset.needs_even_dimensions

    if needs_even_after_sar:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    # 6. Re-assert square pixels in case even-making altered SAR
    if needs_even_after_sar:
        filters.append("setsar=1")

    # 7. ALWAYS ensure square pixels (setsar=1) - this is a safe no-op for already-square pixels
    # but important to explicitly set for all assets
    if "setsar=1" not in ",".join(filters):
        filters.append("setsar=1")

    # 8. Format conversion (ensure yuv420p for compatibility)
    filters.append("format=yuv420p")

    # Video display-matrix rotation is applied by FFmpeg during decode.
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

    # Handle still images FIRST - they need -loop 1 -framerate -t BEFORE -i
    if asset.kind == "image":
        return _normalize_still_image_to_file(asset, out_path, fps)

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

    # Get normalized asset properties using normalize_asset()
    normalized = normalize_asset(asset)

    # Build FFmpeg command
    cmd = [FFMPEG_EXE, "-y"]

    # Network safety
    cmd.extend(FFMPEG_NETWORK_SAFE[1:])  # Skip the exe, add -protocol_whitelist etc

    # Input
    cmd.extend(["-i", str(asset.uri)])

    # Video processing
    if video_stream is not None:
        # Build the filter chain
        vf_filter = build_normalize_filter(normalized)

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
    # Audio-only sources have no video processing options.

    # Audio processing
    if audio_stream is not None:
        cmd.extend([
            "-c:a", "aac",
            "-ar", "48000",
            "-ac", "2"
        ])
    # Output
    cmd.append(str(out_path))

    # Run FFmpeg
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
    cmd[-1] = str(partial_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")
        os.replace(partial_path, out_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise

    return out_path


def _normalize_still_image_to_file(asset: MediaAsset, out_path: Path, fps: int) -> Path:
    """Encode a still image as CFR video for its timeline duration."""
    duration = max(asset.duration_ms, 1) / 1000.0
    normalized = normalize_asset(asset)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:], "-noautorotate",
        "-loop", "1", "-framerate", str(fps), "-t", str(duration),
        "-i", str(asset.uri),
        "-vf", build_normalize_filter(normalized),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
        "-movflags", "+faststart",
    ]
    partial_path = out_path.with_name(
        f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}"
    )
    cmd.append(str(partial_path))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")
        os.replace(partial_path, out_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise
    return out_path


def ken_burns_to_file(
    asset: MediaAsset,
    out_path: Path,
    *,
    duration_ms: int,
    fps: int = 30,
    out_w: int = 1920,
    out_h: int = 1080,
    start_zoom: float = 1.0,
    end_zoom: float = 1.2,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> Path:
    """Render a still image with a smooth, frame-indexed Ken Burns crop."""
    import numpy as np

    if asset.kind != "image":
        raise ValueError("Ken Burns input must be a still image asset")
    _validate_no_network_paths(asset.uri)
    if duration_ms <= 0 or fps <= 0 or out_w <= 0 or out_h <= 0:
        raise ValueError("Duration, frame rate, and output dimensions must be positive")
    if out_w % 2 or out_h % 2:
        raise ValueError("Output dimensions must be even for yuv420p encoding")
    if not all(math.isfinite(value) and value >= 1.0 for value in (start_zoom, end_zoom)):
        raise ValueError("Ken Burns zoom values must be finite and at least 1.0")
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (focus_x, focus_y)):
        raise ValueError("Focus coordinates must be between 0 and 1")

    with av.open(str(asset.uri)) as source:
        image = next(source.decode(video=0), None)
        if image is None:
            raise ValueError(f"Image contains no decodable video frame: {asset.uri}")
        source_rgb = image.to_ndarray(format="rgb24")

    orientation = _exif_orientation(asset.uri)
    if orientation == 2:
        source_rgb = source_rgb[:, ::-1]
    elif orientation == 3:
        source_rgb = source_rgb[::-1, ::-1]
    elif orientation == 4:
        source_rgb = source_rgb[::-1]
    elif orientation == 5:
        source_rgb = np.rot90(source_rgb, k=3)[:, ::-1]
    elif orientation == 6:
        source_rgb = np.rot90(source_rgb, k=3)
    elif orientation == 7:
        source_rgb = np.rot90(source_rgb, k=3)[::-1]
    elif orientation == 8:
        source_rgb = np.rot90(source_rgb, k=1)
    source_rgb = np.ascontiguousarray(source_rgb)

    # Resize to a 2x cover canvas first. This leaves room for zooming in while
    # preserving the requested output aspect ratio at every frame.
    source_h, source_w = source_rgb.shape[:2]
    canvas_w, canvas_h = out_w * 2, out_h * 2
    cover_scale = max(canvas_w / source_w, canvas_h / source_h)
    cover_w = max(canvas_w, math.ceil(source_w * cover_scale))
    cover_h = max(canvas_h, math.ceil(source_h * cover_scale))
    cover_frame = av.VideoFrame.from_ndarray(source_rgb, format="rgb24")
    cover_rgb = cover_frame.reformat(
        width=cover_w, height=cover_h, format="rgb24"
    ).to_ndarray(format="rgb24")
    offset_x = (cover_w - canvas_w) // 2
    offset_y = (cover_h - canvas_h) // 2
    canvas = cover_rgb[offset_y:offset_y + canvas_h, offset_x:offset_x + canvas_w]

    frame_count = max(1, round(duration_ms * fps / 1000))
    duration_s = duration_ms / 1000.0
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_name(
        f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}"
    )
    cmd = [
        FFMPEG_EXE, "-y", "-loglevel", "error", *FFMPEG_NETWORK_SAFE[1:],
        "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{out_w}x{out_h}", "-framerate", str(fps),
        "-i", "pipe:0", "-an", "-frames:v", str(frame_count),
        "-t", str(duration_s), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv", str(partial_path),
    ]
    encoder = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        assert encoder.stdin is not None
        for index in range(frame_count):
            progress = index / max(frame_count - 1, 1)
            zoom = start_zoom + (end_zoom - start_zoom) * progress
            crop_w = max(2, min(canvas_w, round(canvas_w / zoom)))
            crop_h = max(2, min(canvas_h, round(canvas_h / zoom)))
            crop_x = round(focus_x * (canvas_w - crop_w))
            crop_y = round(focus_y * (canvas_h - crop_h))
            crop = canvas[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
            scaled = av.VideoFrame.from_ndarray(crop, format="rgb24").reformat(
                width=out_w, height=out_h, format="rgb24"
            )
            encoder.stdin.write(scaled.to_ndarray(format="rgb24").tobytes())
        encoder.stdin.close()
        stderr = encoder.stderr.read() if encoder.stderr else b""
        returncode = encoder.wait()
    except BaseException:
        if encoder.poll() is None:
            encoder.kill()
        encoder.communicate()
        partial_path.unlink(missing_ok=True)
        raise
    if returncode != 0:
        partial_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg Ken Burns encoding failed: {stderr.decode(errors='replace')}")
    os.replace(partial_path, out_path)
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
