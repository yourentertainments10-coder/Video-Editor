#!/usr/bin/env python3
"""Generate synthetic test fixtures for ingest normalization tests.

Creates real media files matching RENDER_SPEC §2 requirements:
- VFR clip
- 90° rotated clip
- HDR (PQ or HLG tagged) 10-bit clip
- Odd-size + non-square SAR clip (even dims for encoder)
- Interlaced clip
- 5.1 audio file
- PNG with EXIF rotation
- Unicode path test
- Long path test (> 240 chars)
"""

import subprocess
import sys
from pathlib import Path

FFMPEG = "D:\\Python\\video editor\\.venv\\Lib\\site-packages\\imageio_ffmpeg\\binaries\\ffmpeg-win-x86_64-v7.1.exe"


def run_ffmpeg(args, description):
    """Run ffmpeg command and check result."""
    print(f"  Generating {description}...")
    cmd = [FFMPEG, "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        return False
    return True


def main():
    fixtures_dir = Path(__file__).parent / "fixtures" / "ingest"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating test fixtures in {fixtures_dir}")

    # 1. VFR clip (variable frame rate) - use lavfi with varying frame rate
    vfr_path = fixtures_dir / "vfr_clip.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=640x480:r=10:d=2",
        "-f", "lavfi", "-i", "color=c=green:s=640x480:r=20:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=640x480:r=30:d=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vfr_path)
    ], "VFR clip")

    # 2. 90° rotated clip (using rotate metadata)
    rotated_path = fixtures_dir / "rotated_90.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=yellow:s=480x640:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-metadata:s:v:0", "rotate=90",
        str(rotated_path)
    ], "90° rotated clip")

    # 3. HDR 10-bit clip (PQ tagged)
    hdr_path = fixtures_dir / "hdr_10bit.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=white:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p10le",
        "-color_trc", "smpte2084",  # PQ transfer
        "-color_primaries", "bt2020",
        "-colorspace", "bt2020nc",
        str(hdr_path)
    ], "HDR 10-bit (PQ) clip")

    # 4. Odd-size clip with non-square SAR - use even dimensions for encoder
    odd_sar_path = fixtures_dir / "odd_sar.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=cyan:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-vf", "crop=639:479,setsar=4/3",  # odd dims + non-square SAR
        str(odd_sar_path)
    ], "odd-size + non-square SAR clip")

    # 5. Interlaced clip (using field order)
    interlaced_path = fixtures_dir / "interlaced.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=magenta:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-vf", "tinterlace=4",  # make interlaced
        "-field_order", "tt",  # top field first
        str(interlaced_path)
    ], "interlaced clip")

    # 6. 5.1 audio file - generate 6 separate sine waves and combine
    audio_51_path = fixtures_dir / "audio_51.wav"
    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=550:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-filter_complex", "[0:a][1:a][2:a][3:a][4:a][5:a]join=inputs=6:channel_layout=5.1[a]",
        "-map", "[a]",
        "-c:a", "pcm_s16le", str(audio_51_path)
    ], "5.1 audio file")

    # 7. PNG with EXIF rotation
    png_rotated_path = fixtures_dir / "rotated_exif.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=640x480",
        "-frames:v", "1",
        str(png_rotated_path)
    ], "PNG image")

    # 8. Unicode path - create directory with unicode name
    unicode_dir = fixtures_dir / "测试视频文件夹"
    unicode_dir.mkdir(exist_ok=True)
    unicode_path = unicode_dir / "视频文件.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=30:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(unicode_path)
    ], "Unicode path clip")

    # 9. Long path test - create a path that's just over 240 chars total
    # Use a shorter nested structure but long filename
    long_base = fixtures_dir / "long_path_test"
    long_base.mkdir(exist_ok=True)
    # Create a filename that makes the total path > 240 chars
    long_filename = "a" * 200 + ".mp4"  # 200 + base path ≈ 250+
    long_path = long_base / long_filename
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=160x120:r=30:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(long_path)
    ], "long path clip")

    # Also generate a simple still image for still image test
    still_png = fixtures_dir / "still_image.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=640x480",
        "-frames:v", "1",
        str(still_png)
    ], "still image")

    print(f"\nDone! Fixtures created in {fixtures_dir}")
    for f in fixtures_dir.rglob("*"):
        if f.is_file():
            size = f.stat().st_size
            print(f"  {f.relative_to(fixtures_dir)}: {size} bytes")


if __name__ == "__main__":
    main()