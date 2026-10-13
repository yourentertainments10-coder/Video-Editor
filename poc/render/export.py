"""Named export profiles and local post-render verification."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import av

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.render.audio import loudness_normalize, mix_with_music, render_audio_track
from poc.render.transitions import Join, Segment, _validate, render_sequence_to_file


@dataclass(frozen=True)
class ExportProfile:
    name: str
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    target_lufs: float = -14.0
    true_peak_db: float = -1.0


EXPORT_PROFILES: dict[str, ExportProfile] = {
    "youtube_1080p": ExportProfile("youtube_1080p", 1920, 1080, 30, 18, "medium", "192k"),
    "reels_9x16": ExportProfile("reels_9x16", 1080, 1920, 30, 20, "medium", "192k"),
    "square_1x1": ExportProfile("square_1x1", 1080, 1080, 30, 20, "medium", "192k"),
    "preview_540p": ExportProfile("preview_540p", 960, 540, 30, 28, "veryfast", "128k"),
}


def get_profile(name: str) -> ExportProfile:
    try:
        return EXPORT_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown export profile: {name}") from exc


@dataclass
class VerifyReport:
    ok: bool
    problems: list[str]
    measured: dict


@dataclass
class ExportResult:
    path: Path
    profile: ExportProfile
    report: VerifyReport


def _seconds(value: float) -> str:
    return f"{value:.12g}"


def _loudness(path: Path) -> tuple[float | None, float | None]:
    result = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-nostats", *FFMPEG_NETWORK_SAFE[1:], "-i", str(path),
         "-vn",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    stderr = result.stderr
    i_matches = re.findall(r"\bI:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)|-?inf)\s+LUFS", stderr)
    tp_matches = re.findall(r"\bPeak:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)|-?inf)\s+dBFS", stderr)
    try:
        integrated = float(i_matches[-1]) if i_matches and "inf" not in i_matches[-1].lower() else float("-inf")
    except ValueError:
        integrated = float("-inf")
    try:
        peak = float(tp_matches[-1]) if tp_matches and "inf" not in tp_matches[-1].lower() else float("-inf")
    except ValueError:
        peak = float("-inf")
    return integrated, peak


def _black_ranges(path: Path) -> list[dict[str, float]]:
    result = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-nostats", *FFMPEG_NETWORK_SAFE[1:], "-i", str(path),
         "-vf", "blackdetect=d=1.0:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = re.findall(r"black_start:([\d.]+)", result.stderr)
    ends = re.findall(r"black_end:([\d.]+)", result.stderr)
    return [{"start": float(start), "end": float(end)} for start, end in zip(starts, ends)]


def _color_label(value) -> str | None:
    if value is None:
        return None
    # PyAV exposes FFmpeg's color enums as integers in some versions.
    if isinstance(value, int):
        return "bt709" if value == 1 else "unspecified" if value == 2 else str(value)
    text = str(value).lower()
    return "bt709" if "bt709" in text else text


def verify_render(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: float,
    expected_duration_ms: float,
    expect_audio: bool = True,
    target_lufs: float | None = None,
    true_peak_db: float = -1.0,
) -> VerifyReport:
    path = Path(path)
    problems: list[str] = []
    measured = {
        "width": None, "height": None, "fps": None, "duration_ms": None,
        "video_codec": None, "pix_fmt": None, "color_primaries": None,
        "color_trc": None, "colorspace": None, "has_audio": False,
        "audio_rate": None, "audio_channels": None, "lufs": None,
        "true_peak": None, "av_drift_ms": None, "faststart": False,
        "black_ranges": [],
    }
    try:
        with av.open(str(path)) as container:
            video = container.streams.video[0] if container.streams.video else None
            audio = container.streams.audio[0] if container.streams.audio else None
            if video is None:
                problems.append("codec: no video stream")
                video_duration = 0.0
            else:
                measured.update({
                    "width": video.width,
                    "height": video.height,
                    "fps": float(video.average_rate) if video.average_rate else None,
                    "video_codec": video.codec_context.name,
                    "pix_fmt": video.format.name if video.format else None,
                    "color_primaries": _color_label(video.color_primaries),
                    "color_trc": _color_label(video.color_trc),
                    "colorspace": _color_label(video.colorspace),
                })
                video_duration = float(video.duration * video.time_base) if video.duration and video.time_base else float(container.duration or 0) / 1_000_000
                if (video.width, video.height) != (expected_width, expected_height):
                    problems.append(f"resolution: expected {expected_width}x{expected_height}, got {video.width}x{video.height}")
                actual_fps = measured["fps"]
                if actual_fps is None or abs(actual_fps - expected_fps) > 0.5:
                    problems.append(f"fps: expected {expected_fps}, got {actual_fps}")
                tolerance_ms = 1000 / expected_fps
                if abs(video_duration * 1000 - expected_duration_ms) > tolerance_ms + 0.5:
                    problems.append(f"duration: expected {expected_duration_ms} ms, got {video_duration*1000:.3f} ms")
                if measured["video_codec"] != "h264" or measured["pix_fmt"] != "yuv420p":
                    problems.append(f"codec: expected h264/yuv420p, got {measured['video_codec']}/{measured['pix_fmt']}")
                color_values = (measured["color_primaries"], measured["color_trc"], measured["colorspace"])
                if any(value is None or "bt709" not in value for value in color_values):
                    problems.append(f"color: expected bt709 primaries, transfer, and matrix tags; got {color_values}")
            measured["duration_ms"] = video_duration * 1000
            measured["has_audio"] = audio is not None
            if expect_audio and audio is None:
                problems.append("audio: expected an audio stream")
            elif not expect_audio and audio is not None:
                problems.append("audio: expected no audio stream")
            if audio is not None:
                measured["audio_rate"] = audio.rate
                measured["audio_channels"] = len(audio.layout.channels) if audio.layout else None
                if audio.codec_context.name != "aac" or audio.rate != 48000 or measured["audio_channels"] != 2:
                    problems.append(
                        f"audio: expected AAC 48000 Hz stereo, got {audio.codec_context.name} "
                        f"{audio.rate} Hz {measured['audio_channels']} channels"
                    )
                audio_duration = float(audio.duration * audio.time_base) if audio.duration and audio.time_base else 0.0
                measured["av_drift_ms"] = (audio_duration - video_duration) * 1000
                if abs(measured["av_drift_ms"]) > 50:
                    problems.append(f"sync: audio/video drift is {measured['av_drift_ms']:.3f} ms")
    except Exception as exc:
        problems.append(f"codec: could not probe render: {exc}")

    if measured["has_audio"]:
        integrated, peak = _loudness(path)
        measured["lufs"] = integrated
        measured["true_peak"] = peak
        if target_lufs is not None and integrated is not None and integrated > -70:
            if abs(integrated - target_lufs) > 1.0:
                problems.append(f"loudness: expected {target_lufs} LUFS, got {integrated:.2f} LUFS")
            if peak is not None and peak > true_peak_db + 0.5:
                problems.append(f"loudness: true peak {peak:.2f} dBFS exceeds {true_peak_db+0.5:.2f} dBFS")

    try:
        content = path.read_bytes()
        moov, mdat = content.find(b"moov"), content.find(b"mdat")
        measured["faststart"] = 0 <= moov < mdat
        if not measured["faststart"]:
            problems.append("faststart: moov atom does not precede mdat")
    except OSError as exc:
        problems.append(f"faststart: could not read file: {exc}")

    measured["black_ranges"] = _black_ranges(path)
    return VerifyReport(ok=not problems, problems=problems, measured=measured)


def _measure_input_i(path: Path) -> float:
    result = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-nostats", *FFMPEG_NETWORK_SAFE[1:], "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    matches = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, flags=re.S)
    if not matches:
        return float("-inf")
    try:
        value = json.loads(matches[-1])["input_i"]
        return float(value)
    except (ValueError, KeyError, TypeError):
        return float("-inf")


def export_sequence(
    segments: list[Segment],
    joins: list[Join],
    out_path: Path,
    *,
    profile: str | ExportProfile,
    music_path: Path | None = None,
    music_in_ms: int = 0,
    duck: bool = True,
) -> ExportResult:
    selected = get_profile(profile) if isinstance(profile, str) else profile
    _, base_frames, _ = _validate(segments, joins, selected.width, selected.height, selected.fps)
    expected_duration_ms = sum(base_frames) * 1000 / selected.fps
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="poc-export-") as temp_name:
        temp_dir = Path(temp_name)
        track = render_audio_track(segments, joins, temp_dir / "track.wav", fps=selected.fps)
        if music_path is not None:
            track = mix_with_music(track, music_path, temp_dir / "mixed.wav",
                                   music_in_ms=music_in_ms, duck=duck)
        if _measure_input_i(track) <= -70:
            final_audio = temp_dir / "final_audio.wav"
            shutil.copyfile(track, final_audio)
        else:
            final_audio = loudness_normalize(
                track, temp_dir / "final_audio.wav", target_lufs=selected.target_lufs,
                true_peak_db=selected.true_peak_db,
            )
        rendered = render_sequence_to_file(
            segments, joins, temp_dir / "render.mp4", width=selected.width,
            height=selected.height, fps=selected.fps, audio_path=final_audio,
            crf=selected.crf, preset=selected.preset, audio_bitrate=selected.audio_bitrate,
        )
        report = verify_render(
            rendered, expected_width=selected.width, expected_height=selected.height,
            expected_fps=selected.fps, expected_duration_ms=expected_duration_ms,
            expect_audio=True, target_lufs=selected.target_lufs,
            true_peak_db=selected.true_peak_db,
        )
        partial = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
        try:
            shutil.copyfile(rendered, partial)
            os.replace(partial, out_path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return ExportResult(path=out_path, profile=selected, report=report)
