"""Basic tests for Step 3a + 3b functionality."""

import pytest

def test_ingest_normalize_import():
    """Test that ingest normalize module can be imported."""
    from poc.ingest.normalize import normalize_asset, _validate_no_network_paths
    assert callable(normalize_asset)
    assert callable(_validate_no_network_paths)

def test_execute_editor_render_import():
    """Test that execute editor_render module can be imported."""
    from poc.execute.editor_render import build_editor_render_plan, render_editor_timeline
    assert callable(build_editor_render_plan)
    assert callable(render_editor_timeline)

def test_validate_no_network_paths_basic():
    """Test the network path validation function."""
    from poc.ingest.normalize import _validate_no_network_paths

    # Should allow local paths
    _validate_no_network_paths("relative/path.mp4")
    _validate_no_network_paths("/absolute/path.mp4")
    _validate_no_network_paths("C:\\Windows\\path.mp4")

    # Should reject network paths
    import pytest
    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("http://example.com/video.mp4")

    with pytest.raises(ValueError, match="Network paths are prohibited"):
        _validate_no_network_paths("https://example.com/video.mp4")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])