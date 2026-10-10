from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
import pytest

from poc.perceive.cache import load_or_compute
from poc.perceive.footage import FootageMap
from poc.project_assets import AssetImportError, AssetLimits, ProjectAssetLibrary
from poc.timeline.schema import EditorClip, EditorTimeline, Layer, Track, TimeBase
from poc.timeline.validate import validate_editor_timeline

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _video(path: Path, duration_s: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=64x48:rate=10", "-t", str(duration_s), str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return path


def _audio(path: Path, duration_s: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={duration_s}", "-c:a", "pcm_s16le", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return path


def _image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", np.full((24, 32, 3), (20, 80, 140), dtype=np.uint8))
    assert ok
    encoded.tofile(path)
    return path


def _library(tmp_path: Path, *, limits: AssetLimits | None = None) -> ProjectAssetLibrary:
    return ProjectAssetLibrary.create(tmp_path / "project", project_id="project-test", limits=limits)


def test_a_imports_valid_video_asset(tmp_path):
    source = _video(tmp_path / "outside" / "clip.mp4")
    asset = _library(tmp_path).import_asset(source)
    assert asset.kind == "video"
    assert asset.duration_ms is not None
    assert asset.metadata["width"] == 64
    assert asset.metadata["height"] == 48


def test_b_imports_valid_image_asset(tmp_path):
    asset = _library(tmp_path).import_asset(_image(tmp_path / "outside" / "still.png"))
    assert asset.kind == "image"
    assert asset.metadata["width"] == 32
    assert asset.metadata["height"] == 24


def test_c_imports_valid_audio_asset(tmp_path):
    asset = _library(tmp_path).import_asset(_audio(tmp_path / "outside" / "voice.wav"))
    assert asset.kind == "audio"
    assert asset.duration_ms is not None
    assert asset.metadata["has_audio"] is True


def test_d_asset_id_is_deterministic_for_same_kind_and_content(tmp_path):
    source = _image(tmp_path / "outside" / "first.png")
    copy = tmp_path / "outside" / "renamed.png"
    shutil.copyfile(source, copy)
    first = _library(tmp_path / "one").import_asset(source)
    second = _library(tmp_path / "two").import_asset(copy)
    assert first.asset_id == second.asset_id


def test_e_same_file_content_has_same_hash(tmp_path):
    source = _image(tmp_path / "outside" / "first.png")
    copy = tmp_path / "outside" / "copy.png"
    shutil.copyfile(source, copy)
    library = _library(tmp_path)
    assert library.hash_file(source) == library.hash_file(copy)


def test_f_duplicate_content_is_detected_without_duplicate_record(tmp_path):
    source = _image(tmp_path / "outside" / "image.png")
    copy = tmp_path / "outside" / "copy.png"
    shutil.copyfile(source, copy)
    library = _library(tmp_path)
    imported = library.import_asset(source)
    duplicate = library.import_asset(copy)
    assert duplicate.asset_id == imported.asset_id
    assert duplicate.duplicate is True
    assert len(library.project.assets) == 1


def test_g_different_content_has_different_hashes(tmp_path):
    one = _image(tmp_path / "outside" / "one.png")
    two = tmp_path / "outside" / "two.png"
    assert cv2.imwrite(str(two), np.full((24, 32, 3), 230, dtype=np.uint8))
    library = _library(tmp_path)
    assert library.hash_file(one) != library.hash_file(two)


def test_h_imported_metadata_is_persisted(tmp_path):
    source = _video(tmp_path / "outside" / "clip.mp4")
    library = _library(tmp_path)
    asset = library.import_asset(source)
    reopened = ProjectAssetLibrary.load(library.root)
    restored = reopened.asset(asset.asset_id)
    assert restored.content_hash == asset.content_hash
    assert restored.metadata == asset.metadata
    assert restored.source_path == str(source.resolve())


def test_i_missing_source_is_reported_actionably(tmp_path):
    library = _library(tmp_path)
    with pytest.raises(AssetImportError, match="does not exist"):
        library.import_asset(tmp_path / "outside" / "missing.mp4")


def test_j_moved_source_can_be_relinked_without_changing_identity(tmp_path):
    source = _image(tmp_path / "outside" / "before.png")
    library = _library(tmp_path)
    imported = library.import_asset(source)
    moved = tmp_path / "elsewhere" / "after.png"
    moved.parent.mkdir(parents=True)
    source.rename(moved)
    assert library.missing_sources() == [imported.asset_id]
    relinked = library.relink(imported.asset_id, moved)
    assert relinked.asset_id == imported.asset_id
    assert relinked.content_hash == imported.content_hash
    assert relinked.source_path == str(moved.resolve())
    reopened = ProjectAssetLibrary.load(library.root)
    assert reopened.asset(imported.asset_id).source_path == str(moved.resolve())
    assert reopened.missing_sources() == []


def test_k_unsupported_media_extension_is_rejected(tmp_path):
    source = tmp_path / "outside" / "document.txt"
    source.parent.mkdir(parents=True)
    source.write_text("not media", encoding="utf-8")
    with pytest.raises(AssetImportError, match="unsupported media type"):
        _library(tmp_path).import_asset(source)


def test_l_oversized_input_is_rejected_by_configured_limit(tmp_path):
    source = _image(tmp_path / "outside" / "still.png")
    library = _library(tmp_path, limits=AssetLimits(max_file_bytes=8))
    with pytest.raises(AssetImportError, match="exceeds configured limit"):
        library.import_asset(source)


def test_m_unicode_windows_style_path_is_supported(tmp_path):
    source = _image(tmp_path / "外部" / "家族の写真.png")
    asset = _library(tmp_path).import_asset(source)
    assert Path(asset.source_path).is_file()
    assert "家族の写真.png" in asset.source_path


def test_n_long_supported_path_is_supported(tmp_path):
    directory = tmp_path / "outside"
    for index in range(4):
        directory /= f"segment-{index:02d}-" + "x" * 10
    source = _image(directory / "long-image-name.png")
    asset = _library(tmp_path).import_asset(source)
    assert Path(asset.source_path).is_file()


def test_o_import_and_probe_never_modify_source_media(tmp_path):
    source = _video(tmp_path / "outside" / "source.mp4")
    before_bytes = source.read_bytes()
    before_stat = source.stat()
    _library(tmp_path).import_asset(source)
    after_stat = source.stat()
    assert source.read_bytes() == before_bytes
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (before_stat.st_size, before_stat.st_mtime_ns)


def test_p_asset_survives_project_save_and_load(tmp_path):
    source = _image(tmp_path / "outside" / "still.png")
    library = _library(tmp_path)
    asset = library.import_asset(source)
    library.save()
    reopened = ProjectAssetLibrary.load(library.root)
    assert reopened.asset(asset.asset_id) == asset


def test_q_timeline_asset_references_resolve_against_project(tmp_path):
    source = _image(tmp_path / "outside" / "still.png")
    library = _library(tmp_path)
    asset = library.import_asset(source)
    timeline = EditorTimeline(
        project_id=library.project.project_id,
        timeline_id="timeline-test",
        duration_ticks=1000,
        time_base=TimeBase(ticks_per_second=1000),
        assets=[asset.media_asset],
        layers=[Layer(layer_id="layer-visual", name="Visual", order=0)],
        tracks=[Track(track_id="track-image", kind="overlay", layer_id="layer-visual")],
        clips=[EditorClip(
            clip_id="image-clip", asset_id=asset.asset_id, track_id="track-image",
            start_tick=0, duration_ticks=1000,
        )],
    )
    library.set_timeline(timeline)
    assert library.validate_timeline() == []


def test_r_relink_preserves_referenced_timeline_identity(tmp_path):
    source = _image(tmp_path / "outside" / "before.png")
    moved = _image(tmp_path / "elsewhere" / "after.png")
    library = _library(tmp_path)
    asset = library.import_asset(source)
    timeline = EditorTimeline(
        project_id=library.project.project_id,
        timeline_id="timeline-relink",
        duration_ticks=1000,
        time_base=TimeBase(ticks_per_second=1000),
        assets=[asset.media_asset],
        layers=[Layer(layer_id="layer", name="Layer", order=0)],
        tracks=[Track(track_id="track", kind="overlay", layer_id="layer")],
        clips=[EditorClip(clip_id="clip", asset_id=asset.asset_id, track_id="track", start_tick=0, duration_ticks=1000)],
    )
    library.set_timeline(timeline)
    library.relink(asset.asset_id, moved)
    assert library.project.timeline.clips[0].asset_id == asset.asset_id
    assert library.project.timeline.asset_by_id(asset.asset_id).uri == str(moved.resolve())
    assert library.validate_timeline() == []


def test_s_cached_analysis_can_use_asset_content_hash(tmp_path):
    source = tmp_path / "outside" / "source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"media bytes")
    library = _library(tmp_path)
    content_hash = library.hash_file(source)
    calls = 0

    def compute() -> FootageMap:
        nonlocal calls
        calls += 1
        return FootageMap(
            duration_ms=1000, fps=30.0, width=64, height=48,
            sample_rate_hz=4.0, analysis_width=64, samples=[],
        )

    kwargs = dict(
        namespace="asset-test", analyzer_version="test-v1", parameters={},
        model=FootageMap, compute=compute, cache_dir=tmp_path / "cache",
        content_hash=content_hash,
    )
    load_or_compute(source, **kwargs)
    load_or_compute(source, **kwargs)
    assert calls == 1


def test_t_failed_import_does_not_leave_partial_asset_record(tmp_path):
    source = tmp_path / "outside" / "broken.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not a video")
    library = _library(tmp_path)
    with pytest.raises(AssetImportError):
        library.import_asset(source)
    reopened = ProjectAssetLibrary.load(library.root)
    assert reopened.project.assets == []


def test_imported_media_remains_external_to_project_root(tmp_path):
    source = _image(tmp_path / "outside" / "still.png")
    library = _library(tmp_path)
    library.import_asset(source)
    assert source.exists()
    assert not (library.root / "assets" / source.name).exists()
