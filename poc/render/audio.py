"""Offline, deterministic audio rendering for transition sequences."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import av

from poc.ingest.normalize import FFMPEG_EXE, FFMPEG_NETWORK_SAFE
from poc.render.transitions import Join, Segment, _validate


def _seconds(value: float) -> str:
    return f"{value:.12g}"


def _run_atomic(cmd: list[str], out_path: Path, *, label: str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(f"{out_path.stem}.{os.getpid()}.partial{out_path.suffix}")
    cmd[-1] = str(partial)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"FFmpeg {label} failed: {result.stderr}")
        os.replace(partial, out_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return out_path


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
) -> Path:
    """Render timeline audio with sample-accurate length and video-matched joins."""
    if sample_rate != 48000:
        raise ValueError("audio output is fixed at 48000 Hz")
    if cut_fade_ms < 0:
        raise ValueError("cut_fade_ms must be nonnegative")
    media, base_frames, join_frames = _validate(segments, joins, 2, 2, fps)
    del base_frames
    input_has_audio = [_probe_audio(Path(segment.path)) for segment in segments]
    cmd = [FFMPEG_EXE, "-y", *FFMPEG_NETWORK_SAFE[1:]]
    for index, segment in enumerate(segments):
        cmd.extend(["-i", str(segment.path)])
        if not input_has_audio[index]:
            cmd.extend([
                "-f", "lavfi", "-t", _seconds(media[index][1].duration_ms / 1000),
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
        start_ms = segment.src_in_ms - left_frames * 1000 / fps
        end_ms = segment.src_out_ms + right_frames * 1000 / fps
        label = f"a{i}"
        filters = [
            f"atrim=start={_seconds(start_ms / 1000)}:end={_seconds(end_ms / 1000)}",
            "asetpts=PTS-STARTPTS", f"aresample={sample_rate}",
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
        ]
        if i > 0 and joins[i - 1].kind == "cut" and cut_fade_ms:
            filters.append(f"afade=t=in:st=0:d={_seconds(cut_fade_ms / 1000)}")
        if i < len(joins) and joins[i].kind == "cut" and cut_fade_ms:
            duration = max(0, end_ms - start_ms)
            fade_start = max(0, duration / 1000 - cut_fade_ms / 1000)
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
                half = join_frames[i] // 2 / fps
                # Fade to silence at the cut, then bring the incoming clip up.
                graph.append(
                    f"[{current}]afade=t=out:st=duration-{_seconds(half)}:d={_seconds(half)}[afout{i}];"
                    f"[{nxt}]afade=t=in:st=0:d={_seconds(half)}[afin{i}];"
                    f"[afout{i}][afin{i}]concat=n=2:v=0:a=1[{out}]"
                )
            else:
                graph.append(
                    f"[{current}][{nxt}]acrossfade=d={_seconds(duration)}:c1=tri:c2=tri[{out}]"
                )
        current = out

    graph.append(f"[{current}]atrim=duration={_seconds(sum(s.src_out_ms-s.src_in_ms for s in segments)/1000)},"
                 "asetpts=PTS-STARTPTS[outa]")
    cmd.extend([
        "-filter_complex", ";".join(graph), "-map", "[outa]",
        *_wav_output_args(), str(Path(out_path)),
    ])
    return _run_atomic(cmd, Path(out_path), label="audio track render")


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
    return _run_atomic(cmd, Path(out_path), label="music mix")


def loudness_normalize(
    in_path: Path,
    out_path: Path,
    *,
    target_lufs: float = -14.0,
    true_peak_db: float = -1.0,
) -> Path:
    common = [FFMPEG_EXE, "-hide_banner", "-nostats", *FFMPEG_NETWORK_SAFE[1:], "-i", str(in_path)]
    pass1 = subprocess.run(
        [*common, "-af", f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True,
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
    return _run_atomic(cmd, Path(out_path), label="loudness normalization")
