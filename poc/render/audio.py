"""Offline, deterministic audio rendering for transition sequences."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from collections.abc import Callable, Sequence

import av

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.render.transitions import Join, Segment, _validate
from poc.render._process import run_ffmpeg_atomic


def _seconds(value: float) -> str:
    return f"{value:.12g}"


def _probe_audio(path: Path) -> bool:
    with av.open(str(path)) as container:
        return bool(container.streams.audio)


def _wav_output_args() -> list[str]:
    return ["-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-bitexact"]


def render_audio_track(
    segments: list[Segment],
    joins: list[Join],
    out_path: Path,
    *,
    fps: int = 30,
    sample_rate: int = 48000,
    cut_fade_ms: int = 10,
    segment_audio_options: Sequence[dict] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Render timeline audio with sample-accurate length and video-matched joins."""
    if sample_rate != 48000:
        raise ValueError("audio output is fixed at 48000 Hz")
    if cut_fade_ms < 0:
        raise ValueError("cut_fade_ms must be nonnegative")
    media, base_frames, join_frames = _validate(segments, joins, 2, 2, fps)
    if segment_audio_options is not None and len(segment_audio_options) != len(segments):
        raise ValueError("segment_audio_options must match segments")
    segment_audio_options = segment_audio_options or [{} for _ in segments]
    input_has_audio = [_probe_audio(Path(segment.path)) for segment in segments]
    cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:]]
    for index, segment in enumerate(segments):
        left_frames = join_frames[index - 1] // 2 if index > 0 else 0
        right_frames = join_frames[index] // 2 if index < len(joins) else 0
        start_ms = segment.src_in_ms - left_frames * 1000 / fps
        clip_frames = left_frames + base_frames[index] + right_frames
        cmd.extend([
            "-ss", _seconds(start_ms / 1000), "-t", _seconds(clip_frames / fps),
            "-i", str(segment.path),
        ])
        if not input_has_audio[index]:
            cmd.extend([
                "-f", "lavfi", "-t", _seconds(clip_frames / fps),
                "-i", "anullsrc=r=48000:cl=stereo",
            ])

    # Track each source input index (silent clips have a separate lavfi input).
    source_audio_indices = []
    next_input = 0
    for has_audio in input_has_audio:
        source_audio_indices.append(next_input if has_audio else next_input + 1)
        next_input += 1 if has_audio else 2

    graph = []
    for i, (segment, (_, normalized)) in enumerate(zip(segments, media)):
        left_frames = join_frames[i - 1] // 2 if i > 0 else 0
        right_frames = join_frames[i] // 2 if i < len(joins) else 0
        clip_frames = left_frames + base_frames[i] + right_frames
        label = f"a{i}"
        filters = [
            f"atrim=start=0:duration={_seconds(clip_frames / fps)}",
            "asetpts=PTS-STARTPTS", f"aresample={sample_rate}",
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
        ]
        options = segment_audio_options[i]
        if options.get("muted"):
            filters.append("volume=0")
        else:
            gain_db = float(options.get("gain_db", 0.0))
            if gain_db:
                filters.append(f"volume={_seconds(gain_db)}dB")
            pan = float(options.get("pan", 0.0))
            if pan < 0:
                filters.append(f"pan=stereo|c0=c0|c1={_seconds(1+pan)}*c1")
            elif pan > 0:
                filters.append(f"pan=stereo|c0={_seconds(1-pan)}*c0|c1=c1")
            fade_in = int(options.get("fade_in_ms", 0)) / 1000
            fade_out = int(options.get("fade_out_ms", 0)) / 1000
            if fade_in:
                filters.append(f"afade=t=in:st={_seconds(left_frames/fps)}:d={_seconds(fade_in)}")
            if fade_out:
                fade_start = max(0, (left_frames + base_frames[i]) / fps - fade_out)
                filters.append(f"afade=t=out:st={_seconds(fade_start)}:d={_seconds(fade_out)}")
        if i > 0 and joins[i - 1].kind == "cut" and cut_fade_ms:
            filters.append(f"afade=t=in:st=0:d={_seconds(cut_fade_ms / 1000)}")
        if i < len(joins) and joins[i].kind == "cut" and cut_fade_ms:
            fade_start = max(0, clip_frames / fps - cut_fade_ms / 1000)
            filters.append(f"afade=t=out:st={_seconds(fade_start)}:d={_seconds(cut_fade_ms / 1000)}")
        graph.append(f"[{source_audio_indices[i]}:a:0]{','.join(filters)}[{label}]")

    current = "a0"
    for i, join in enumerate(joins):
        nxt, out = f"a{i + 1}", f"aseq{i}"
        if join.kind == "cut":
            graph.append(f"[{current}][{nxt}]concat=n=2:v=0:a=1[{out}]")
        else:
            duration = join_frames[i] / fps
            if join.kind == "fade_black":
                half_frames = join_frames[i] // 2
                half = half_frames / fps
                elapsed = sum(base_frames[:i + 1]) / fps
                # Fade to silence at the cut, then bring the incoming clip up.
                graph.append(
                    f"[{current}]atrim=duration={_seconds(elapsed)},asetpts=PTS-STARTPTS,"
                    f"afade=t=out:st={_seconds(elapsed-half)}:d={_seconds(half)}[afout{i}];"
                    f"[{nxt}]atrim=start={_seconds(half)},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={_seconds(half)}[afin{i}];"
                    f"[afout{i}][afin{i}]concat=n=2:v=0:a=1[{out}]"
                )
            else:
                graph.append(
                    f"[{current}][{nxt}]acrossfade=d={_seconds(duration)}:c1=tri:c2=tri[{out}]"
                )
        current = out

    graph.append(f"[{current}]atrim=duration={_seconds(sum(base_frames)/fps)},"
                 "asetpts=PTS-STARTPTS[outa]")
    cmd.extend([
        "-filter_complex", ";".join(graph), "-map", "[outa]",
        *_wav_output_args(), str(Path(out_path)),
    ])
    return run_ffmpeg_atomic(cmd, Path(out_path), cancel=cancel, label="audio track render")


def mix_with_music(
    track_wav: Path,
    music_path: Path,
    out_path: Path,
    *,
    music_in_ms: int = 0,
    duck: bool = True,
    duck_db: float = -10.0,
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    if min(music_in_ms, fade_in_ms, fade_out_ms) < 0:
        raise ValueError("trim and fade durations must be nonnegative")
    with av.open(str(track_wav)) as c:
        track_duration = float(c.duration or 0) / 1_000_000
    if track_duration <= 0:
        raise ValueError("track_wav must have a known positive duration")
    duration = _seconds(track_duration)
    filters = [
        "[0:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"atrim=duration={duration},asetpts=PTS-STARTPTS[track]",
        f"[1:a]atrim=start={_seconds(music_in_ms/1000)}:duration={duration},asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[music0]",
    ]
    if duck:
        # A low threshold and steep ratio make the requested attenuation hold
        # once the sidechain rises above the voice bed's noise floor.
        threshold = max(0.0001, 0.01 * 10 ** (duck_db / 20))
        filters.append(
            f"[music0][track]sidechaincompress=threshold={threshold:.8g}:ratio=20:attack=20:release=250[music]"
        )
    else:
        filters.append("[music0]anull[music]")
    filters.append("[track][music]amix=inputs=2:duration=first:normalize=0[mix0]")
    fades = []
    if fade_in_ms:
        fades.append(f"afade=t=in:st=0:d={_seconds(fade_in_ms/1000)}")
    if fade_out_ms:
        fades.append(f"afade=t=out:st={_seconds(max(0, track_duration-fade_out_ms/1000))}:d={_seconds(fade_out_ms/1000)}")
    filters.append(f"[mix0]{','.join(fades) if fades else 'anull'},atrim=duration={duration}[outa]")
    cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:], "-i", str(track_wav), "-i", str(music_path),
           "-filter_complex", ";".join(filters), "-map", "[outa]", *_wav_output_args(), str(Path(out_path))]
    return run_ffmpeg_atomic(cmd, Path(out_path), cancel=cancel, label="music mix")


def loudness_normalize(
    in_path: Path,
    out_path: Path,
    *,
    target_lufs: float = -14.0,
    true_peak_db: float = -1.0,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    common = [FFMPEG_EXE, "-hide_banner", "-nostats", *FFMPEG_NETWORK_SAFE[1:], "-i", str(in_path)]
    from poc.render._process import run_process
    pass1 = run_process(
        [*common, "-af", f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11:print_format=json",
         "-f", "null", "-"], cancel=cancel,
    )
    if pass1.returncode:
        raise RuntimeError(f"FFmpeg loudness measurement failed: {pass1.stderr}")
    matches = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", pass1.stderr, flags=re.S)
    if not matches:
        raise RuntimeError(f"FFmpeg loudnorm did not return measurement JSON: {pass1.stderr}")
    measured = json.loads(matches[-1])
    filt = (
        f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary"
    )
    cmd = [FFMPEG_EXE, "-y", "-hide_banner", *FFMPEG_NETWORK_SAFE[1:], "-i", str(in_path),
           "-af", filt, *_wav_output_args(), str(Path(out_path))]
    return run_ffmpeg_atomic(cmd, Path(out_path), cancel=cancel, label="loudness normalization")
