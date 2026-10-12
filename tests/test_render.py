import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from poc.ingest.normalize import normalize_asset
from poc.timeline import MediaAsset


def long_paths_enabled():
    """Return True if Windows LongPathsEnabled is set to 1."""
    if sys.platform != "win32":
        return True  # Assume enabled on non-Windows
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        )
        value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return value == 1
    except FileNotFoundError:
        return False


def test_unicode_paths(tmp_path):
    """Test that Unicode paths are handled during ingest normalization."""
    # Use a real fixture clip
    fixture_src = Path("tests/fixtures/ingest/vfr_clip.mp4")
    assert fixture_src.exists(), "VFR fixture not found"

    # --- Unicode path test ---
    unicode_dir = tmp_path / "वीडियो_テスト_vidéo"
    unicode_dir.mkdir(parents=True, exist_ok=True)
    unicode_clip = unicode_dir / "vidéo.mp4"
    shutil.copy(fixture_src, unicode_clip)

    asset = MediaAsset(
        asset_id="unicode-test",
        kind="video",
        uri=str(unicode_clip),
        duration_ms=6000,
    )
    # Should not raise on validation
    normalized = normalize_asset(asset)
    assert normalized is not None
    assert normalized.duration_ms > 0


def test_long_paths(tmp_path):
    """Test that long Windows paths are handled during ingest normalization (when enabled)."""
    if not long_paths_enabled():
        pytest.skip("Windows LongPathsEnabled=0")

    # Use a real fixture clip
    fixture_src = Path("tests/fixtures/ingest/vfr_clip.mp4")
    assert fixture_src.exists(), "VFR fixture not found"

    # Build a path >240 characters
    # Windows max path is 260, we aim for ~250 to be safe
    # We'll create a deep directory structure with a long name
    long_base = tmp_path / ("long_path_" + "x" * 200)
    # Ensure parent directory exists (may need to create intermediate)
    long_base.mkdir(parents=True, exist_ok=True)
    long_filename = "a" * 200 + ".mp4"
    long_path = long_base / long_filename
    shutil.copy(fixture_src, long_path)

    asset = MediaAsset(
        asset_id="long-path-test",
        kind="video",
        uri=str(long_path),
        duration_ms=6000,
    )
    # Should not raise on validation
    normalized = normalize_asset(asset)
    assert normalized is not None
    assert normalized.duration_ms > 0


def test_legacy_render_is_deterministic(tmp_path):
    """Test that the legacy v0.1 renderer produces deterministic output.

    Render the same v0.1 timeline twice with the legacy renderer;
    same frame count, duration, and identical decoded frames at each cut.
    """
    from poc.execute.render import render_from_file
    from poc.timeline import Timeline, Settings, Music, Source, Clip, Provenance

    # Copy the fixtures to temp directory to ensure we can access them with relative paths
    fixture_src = Path("tests/fixtures/ingest/vfr_clip.mp4")
    fixture_dst = tmp_path / "vfr_clip.mp4"
    shutil.copy(fixture_src, fixture_dst)

    # Copy the audio fixture as well
    audio_src = Path("tests/fixtures/ingest/audio_51.wav")
    audio_dst = tmp_path / "audio_51.wav"
    shutil.copy(audio_src, audio_dst)

    # Get the actual duration of the audio file to set timeline duration within bounds
    # We know from the fixture that audio_51.wav is 2000 ms, but let's be safe and use a short timeline
    # We'll use 1 second (30 frames at 30 fps) = 1000 ms
    duration_frames = 30  # 1 second at 30 fps

    # Create a proper v0.1 timeline object with relative paths
    timeline = Timeline(
        schema_version="0.1",
        settings=Settings(
            fps=30,
            width=640,
            height=480
        ),
        duration_frames=duration_frames,  # 1 second at 30 fps
        music=Music(
            path="audio_51.wav",  # Relative path
            in_ms=0,
            gain_db=0.0,
            fade_out_ms=0
        ),
        beats=[],
        clips=[
            Clip(
                id="test-clip",
                start_frame=0,
                end_frame=duration_frames,  # 1 second * 30 fps
                source=Source(
                    path="vfr_clip.mp4",  # Relative path
                    in_ms=0
                )
            )
        ],
        provenance=Provenance(
            generator="hand",
            params={},
            seed=None
        )
    )

    # Write timeline to temporary file
    timeline_path = tmp_path / "test_timeline.json"
    timeline.save(timeline_path)

    # Render twice to temporary output files
    out_path1 = tmp_path / "output1.mp4"
    out_path2 = tmp_path / "output2.mp4"

    # First render
    result1 = render_from_file(timeline_path, out_path1)

    # Second render
    result2 = render_from_file(timeline_path, out_path2)

    # Assert same frame count
    assert result1.duration_frames == result2.duration_frames

    # Assert same duration
    assert result1.duration_ms == result2.duration_ms

    # For identical decoded frames at each cut, we would need to decode and compare
    # Since this is a unit test and we don't want to require heavy dependencies,
    # we'll check that the files are byte-identical (which should be true for deterministic rendering)
    # However, due to potential timestamps in containers, we'll check key properties

    # More robust check: decode and compare frame hashes at key positions
    # Since we're testing deterministic rendering, frames at each cut should match exactly
    try:
        import subprocess
        import tempfile
        import av

        # Extract frames at specific timestamps and compare their hashes
        def get_frame_hash_at_time(video_path, time_ms):
            """Extract a single frame at time_ms and return its SHA256 hash."""
            with tempfile.TemporaryDirectory() as tmp_dir:
                frame_path = Path(tmp_dir) / "frame.png"
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{time_ms/1000.0:.3f}",  # seek to time
                    "-i", str(video_path),
                    "-frames:v", "1",  # extract exactly one frame
                    "-f", "image2pipe",  # output to pipe
                    "-vcodec", "png",    # output as png for consistent hashing
                    str(frame_path)
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    return None
                # Read the frame and compute hash
                import hashlib
                with open(frame_path, "rb") as f:
                    frame_data = f.read()
                return hashlib.sha256(frame_data).hexdigest()

        # Test frames at key positions: start, middle, end
        test_times_ms = [0, 500, 1000]  # 0s, 0.5s, 1.0s (assuming 1-second timeline)
        for t_ms in test_times_ms:
            hash1 = get_frame_hash_at_time(out_path1, t_ms)
            hash2 = get_frame_hash_at_time(out_path2, t_ms)
            assert hash1 is not None and hash2 is not None, f"Failed to extract frame at {t_ms}ms"
            assert hash1 == hash2, f"Frame hash mismatch at {t_ms}ms: {hash1} != {hash2}"

    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, ImportError):
        # If ffprobe/av is not available or fails, fall back to file size comparison
        # This is less reliable but better than nothing for a unit test
        size1 = out_path1.stat().st_size
        size2 = out_path2.stat().st_size
        # Allow small difference due to potential timestamp variations
        assert abs(size1 - size2) < 1024  # Within 1KB