"""Tests for FFmpeg network gap fix implementation."""

import pytest

from poc.ingest.normalize import _validate_no_network_paths, FFMPEG_NETWORK_SAFE
from poc.execute.editor_render import FFMPEG_NETWORK_SAFE as EXECUTE_FFMPEG_NETWORK_SAFE


def test_ffmpeg_network_safe_includes_protocol_whitelist():
    """Test that FFmpeg command includes protocol whitelist."""
    assert "-protocol_whitelist" in FFMPEG_NETWORK_SAFE
    assert "file,pipe" in FFMPEG_NETWORK_SAFE


def test_execute_ffmpeg_network_safe_includes_protocol_whitelist():
    """Test that execute FFmpeg command includes protocol whitelist."""
    assert "-protocol_whitelist" in EXECUTE_FFMPEG_NETWORK_SAFE
    assert "file,pipe" in EXECUTE_FFMPEG_NETWORK_SAFE


def test_validate_no_network_paths_rejects_http():
    """Test that HTTP paths are rejected."""
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("http://example.com/video.mp4")


def test_validate_no_network_paths_rejects_https():
    """Test that HTTPS paths are rejected."""
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("https://example.com/video.mp4")


def test_validate_no_network_paths_rejects_ftp():
    """Test that FTP paths are rejected."""
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("ftp://example.com/video.mp4")


def test_validate_no_network_paths_rejects_rtsp():
    """Test that RTSP paths are rejected."""
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("rtsp://example.com/video.mp4")


def test_validate_no_network_paths_allows_local_paths():
    """Test that local paths are allowed."""
    # These should not raise
    _validate_no_network_paths("relative/path.mp4")
    _validate_no_network_paths("/absolute/path.mp4")
    _validate_no_network_paths("C:\\Windows\\path.mp4")
    _validate_no_network_paths("\\\\server\\share\\file.mp4")  # UNC path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])