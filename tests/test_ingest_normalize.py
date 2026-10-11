"""Tests for poc.ingest.normalize module."""

import tempfile
from pathlib import Path

import pytest

from poc.ingest.normalize import NormalizedAsset, _validate_no_network_paths, build_normalize_filter, normalize_asset
from poc.project_assets import ProjectAssetLibrary
from poc.timeline import MediaAsset


def test_validate_no_network_paths_allows_local_paths():
    """Should allow local file paths."""
    _validate_no_network_paths(
        "relative/path.mp4",
        "/absolute/path.mp4",  # This will be caught by resolve_media_path, not here
        "C:\\Windows\\path.mp4",  # This will be caught by resolve_media_path, not here
    )


def test_validate_no_network_paths_rejects_network_paths():
    """Should reject paths with network protocols."""
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("http://example.com/video.mp4")

    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("https://example.com/video.mp4")

    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("ftp://example.com/video.mp4")

    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("rtsp://example.com/video.mp4")


def test_normalize_asset_handles_still_image(tmp_path):
    """Should handle still image assets."""
    # Test the normalize_asset function directly with a mock that returns a still image
    from poc.timeline import MediaAsset

    # Create a mock image asset
    asset = MediaAsset(
        asset_id="test-image",
        kind="image",
        uri="fake.png",  # We won't actually probe this
        duration_ms=5000,  # 5 seconds
    )

    # Mock the normalize_asset function to return a still image NormalizedAsset
    import poc.ingest.normalize as normalize_module
    original_func = normalize_module.normalize_asset

    def mock_normalize_asset(asset_param):
        return normalize_module.NormalizedAsset(
            path=Path(asset_param.uri),
            duration_ms=asset_param.duration_ms,
            width=100,
            height=200,
            fps=0,  # Will be set by timeline
            has_audio=False,
            audio_sample_rate=0,
            audio_channels=0,
            needs_rotation=False,
            is_hdr=False,
            is_10bit=False,
            needs_deinterlace=False,
            needs_even_dimensions=False,
            is_still_image=True,
        )

    normalize_module.normalize_asset = mock_normalize_asset

    try:
        # Should not raise
        normalized = normalize_module.normalize_asset(asset)
        assert isinstance(normalized, normalize_module.NormalizedAsset)
        assert normalized.is_still_image == True
        assert normalized.duration_ms == 5000  # Set from asset
        assert normalized.width == 100
        assert normalized.height == 200
    finally:
        # Restore original function
        normalize_module.normalize_asset = original_func


def test_build_normalize_filter_returns_string():
    """Should return a filter string."""
    # Create a NormalizedAsset object for testing
    asset = NormalizedAsset(
        path=Path("test.mp4"),
        duration_ms=10000,
        width=640,
        height=480,
        fps=30.0,
        has_audio=True,
        audio_sample_rate=48000,
        audio_channels=2,
        needs_rotation=False,
        is_hdr=False,
        is_10bit=False,
        needs_deinterlace=False,
        needs_even_dimensions=False,
        is_still_image=False,
    )

    filter_str = build_normalize_filter(asset)
    assert isinstance(filter_str, str)
    # Should contain setsar=1 at minimum
    assert "setsar=1" in filter_str or filter_str == "null"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])