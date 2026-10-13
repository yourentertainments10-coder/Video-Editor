#!/usr/bin/env python3
"""Generate synthetic test fixtures for ingest normalization tests.

Creates real media files matching RENDER_SPEC §2 requirements:
- VFR clip
- 90° rotated clip
- HDR (PQ or HLG tagged) 10-bit clip
- Odd-size + non-square SAR clip (even dims for encoder)
- Interlaced clip
- 5.1 audio file
- plain PNG image
- Unicode path test
- Long path test (> 240 chars)
"""

import subprocess
import sys
import struct
from pathlib import Path
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FAILED_FIXTURES = []


def run_ffmpeg(args, description):
    """Run ffmpeg command and check result."""
    print(f"  Generating {description}...")
    cmd = [FFMPEG, "-y", "-protocol_whitelist", "file,pipe", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        FAILED_FIXTURES.append(description)
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

    # 2. 90° rotated clip (using display rotation metadata)
    # First create a plain clip, then apply display rotation and copy
    plain_clip = fixtures_dir / "plain_clip.mp4"
    rotated_path = fixtures_dir / "rotated_90.mp4"
    rotated_180_path = fixtures_dir / "rotated_180.mp4"

    # Create plain clip first
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=yellow:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(plain_clip)
    ], "plain clip for rotation test")

    # Apply display rotation and copy (no re-encoding)
    run_ffmpeg([
        "-display_rotation", "90",
        "-i", str(plain_clip),
        "-c", "copy",
        str(rotated_path)
    ], "90° rotated clip (display rotation)")

    # Keep the coded dimensions and apply a 180° display matrix.
    run_ffmpeg([
        "-display_rotation", "180",
        "-i", str(plain_clip),
        "-c", "copy",
        str(rotated_180_path)
    ], "180° rotated clip (display rotation)")

    # Clean up plain clip
    try:
        plain_clip.unlink()
    except:
        pass

    # 3. HDR 10-bit clip (PQ tagged)
    hdr_path = fixtures_dir / "hdr_10bit.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=white:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p10le",
        "-color_trc", "smpte2084",  # PQ transfer (16)
        "-color_primaries", "bt2020", # BT.2020
        "-colorspace", "bt2020nc",
        "-bsf:v", "h264_metadata=colour_primaries=9:transfer_characteristics=16:matrix_coefficients=9",
        str(hdr_path)
    ], "HDR 10-bit (PQ) clip")

    # 4. Odd-size clip with non-square SAR - use odd dimensions (mkv/ffv1 allows odd sizes)
    odd_sar_path = fixtures_dir / "odd_sar.mkv"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=cyan:s=639x479:r=30:d=2",
        "-c:v", "ffv1",
        "-vf", "setsar=4/3",
        str(odd_sar_path)
    ], "odd-size + non-square SAR clip")

    # 5. Interlaced clip (using field order)
    interlaced_path = fixtures_dir / "interlaced.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=magenta:s=640x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-vf", "tinterlace=4",  # make interlaced
        "-flags", "+ilme+ildct", "-x264-params", "tff=1",
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

    # 7. Plain PNG image (PNG does not carry the EXIF orientation fixture).
    png_rotated_path = fixtures_dir / "rotated_exif.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=640x480",
        "-frames:v", "1",
        str(png_rotated_path)
    ], "PNG image")

    # EXIF-oriented JPEG used by still-image normalization tests.
    exif_fixture_code = (
        "from PIL import Image; from pathlib import Path; "
        "img=Image.new('RGB',(640,480),(255,0,0)); "
        "img.paste((0,0,255),(320,0,640,480)); "
        "exif=Image.Exif(); exif[0x0112]=6; "
        "img.save(Path(__import__('sys').argv[1]),exif=exif,quality=95)"
    )
    try:
        from PIL import Image
        img = Image.new("RGB", (640, 480), (255, 0, 0))
        img.paste((0, 0, 255), (320, 0, 640, 480))
        exif = Image.Exif()
        exif[0x0112] = 6
        img.save(fixtures_dir / "exif_rot6.jpg", exif=exif, quality=95)
    except ImportError:
        result = subprocess.run(
            [sys.executable, "-c", exif_fixture_code, str(fixtures_dir / "exif_rot6.jpg")],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            try:
                import cv2
                import numpy as np
                image = np.zeros((480, 640, 3), dtype=np.uint8)
                image[:, :320] = (0, 0, 255)
                image[:, 320:] = (255, 0, 0)
                ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise RuntimeError("OpenCV could not encode JPEG")
                tiff = (b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 1)
                        + struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<H", 6)
                        + b"\0\0" + struct.pack("<I", 0))
                exif = b"Exif\0\0" + tiff
                app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
                jpeg = bytes(encoded)
                (fixtures_dir / "exif_rot6.jpg").write_bytes(jpeg[:2] + app1 + jpeg[2:])
            except Exception as exc:
                FAILED_FIXTURES.append("EXIF orientation JPEG")
                print(f"  ERROR generating EXIF orientation JPEG: {result.stderr}; {exc}")
    except Exception as exc:
        FAILED_FIXTURES.append("EXIF orientation JPEG")
        print(f"  ERROR generating EXIF orientation JPEG: {exc}")

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

    # 10. 720x480 with SAR 32:27 fixture
    sar_32_27_path = fixtures_dir / "sar_32_27.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=orange:s=720x480:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-vf", "setsar=32/27",
        str(sar_32_27_path)
    ], "720x480 SAR 32:27 fixture")

    # Also generate a simple still image for still image test
    still_png = fixtures_dir / "still_image.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=640x480",
        "-frames:v", "1",
        str(still_png)
    ], "still image")

    # 11. 1080p landscape and portrait clips for proxy sizing tests
    landscape_path = fixtures_dir / "landscape_1080p.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=30:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(landscape_path)
    ], "1080p landscape proxy fixture")

    portrait_path = fixtures_dir / "portrait_1080p.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=1080x1920:r=30:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(portrait_path)
    ], "1080p portrait proxy fixture")

    # 12. Detailed 1080p still image for Ken Burns motion tests
    pattern_path = fixtures_dir / "pattern.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=s=1920x1080",
        "-frames:v", "1", str(pattern_path)
    ], "Ken Burns pattern image")

    # 13. Landscape marker scene for vertical aspect-fit behavior tests
    marker_path = fixtures_dir / "marker_left.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:r=30:d=2",
        "-vf", "drawbox=x=200:y=440:w=200:h=200:color=white:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(marker_path)
    ], "left marker for vertical fit tests")

    # 14. Solid-color clips for centered-cut transition tests.
    for color in ("red", "green", "blue"):
        run_ffmpeg([
            "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(fixtures_dir / f"solid_{color}.mp4"),
        ], f"solid {color} transition fixture")

    # 15. Audio fixtures for sequence audio and mixing tests.
    for name, color, frequency in (("tone_a", "red", 440), ("tone_b", "green", 880)):
        run_ffmpeg([
            "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d=4",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(fixtures_dir / f"{name}.mp4"),
        ], f"{name} audio transition fixture")

    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=8",
        "-af", "volume=0.3", "-ac", "2", "-c:a", "pcm_s16le",
        str(fixtures_dir / "music_bed.wav"),
    ], "music bed")
    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=6",
        "-af", "volume='if(between(t,2,4),0.5,0)':eval=frame", "-ac", "2",
        "-c:a", "pcm_s16le", str(fixtures_dir / "voice.wav"),
    ], "voice with silent intervals")

    print(f"\nDone! Fixtures created in {fixtures_dir}")
    for f in fixtures_dir.rglob("*"):
        if f.is_file():
            size = f.stat().st_size
            # Handle Unicode characters in filenames for Windows console
            rel_path = f.relative_to(fixtures_dir)
            try:
                print(f"  {rel_path}: {size} bytes")
            except UnicodeEncodeError:
                # Fallback for Unicode characters that can't be displayed
                print(f"  [Unicode filename]: {size} bytes")

    if FAILED_FIXTURES:
        print("\nFailed fixtures:")
        for name in FAILED_FIXTURES:
            print(f"  - {name}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
