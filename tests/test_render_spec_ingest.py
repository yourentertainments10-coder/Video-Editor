"""Tests for RENDER_SPEC §2 ingest normalization requirements."""

import tempfile
from pathlib import Path

import pytest

from poc.ingest.normalize import normalize_asset, NormalizedAsset
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import MediaAsset


def test_variable_frame_rate_detection():
    """Test that VFR is detected correctly."""
    # This will be implemented when we have real VFR test assets
    # For now, we'll test the logic with mocked data
    pass


def test_rotation_metadata_handling():
    """Test that rotation metadata is handled correctly."""
    # This will be implemented when we have real rotation test assets
    pass


def test_hdr_10bit_handling():
    """Test that HDR and 10-bit video is handled correctly."""
    # This will be implemented when we have real HDR test assets
    pass


def test_odd_dimensions_handling():
    """Test that odd dimensions are handled correctly."""
    # This will be implemented when we have real odd dimension test assets
    pass


def test_interlaced_handling():
    """Test that interlaced video is handled correctly."""
    # This will be implemented when we have real interlaced test assets
    pass


def test_missing_odd_audio_handling():
    """Test that missing/odd audio is handled correctly."""
    # This will be implemented when we have real audio test assets
    pass


def test_unicode_long_paths_handling(tmp_path):
    """Test that Unicode and long Windows paths are handled."""
    # Test Unicode path
    unicode_dir = tmp_path / "测试视频"
    unicode_dir.mkdir()
    unicode_path = unicode_dir / "视频.mp4"
    unicode_path.write_text("dummy")

    # Test that our validation doesn't break on Unicode paths
    # (actual FFmpeg handling will be tested later)
    asset = MediaAsset(
        asset_id="unicode-test",
        kind="video",
        uri=str(unicode_path),
        duration_ms=5000
    )

    # Should not raise on validation
    from poc.ingest.normalize import _validate_no_network_paths
    _validate_no_network_paths(str(unicode_path))

    # Test long path (Windows-style)
    long_path = str(tmp_path / ("A" * 250 + ".mp4"))
    Path(long_path).write_text("dummy")
    _validate_no_network_paths(long_path)


def test_still_image_handling(tmp_path):
    """Test that still images are handled correctly."""
    # Create a simple PNG
    img_path = tmp_path / "test.png"
    # Minimal valid PNG (1x1 black pixel)
    png_data = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
        b'\x00\x00\x00\x0bIDAT'
        b'\x78\x9c\x63\x60\x00\x00\x00\x02\x00\x01'
        b'\x2d\x04\xe2\x2b\x31\x31\x00\x00\x00\x00\x00\x00\x00\x00'
        b'\xaeB`\x82'
    )
    img_path.write_bytes(png_data)

    library = ProjectAssetLibrary.create(tmp_path / "project", project_id="test")
    imported = library.import_asset(img_path)
    asset = imported.media_asset

    # Should not raise
    normalized = normalize_asset(asset)
    assert isinstance(normalized, NormalizedAsset)
    assert normalized.is_still_image == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])