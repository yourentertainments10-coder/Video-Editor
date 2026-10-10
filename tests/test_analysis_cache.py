from __future__ import annotations

from poc.perceive import footage
from poc.perceive.cache import load_or_compute
from poc.perceive.footage import FootageMap


def _footage_map(duration_ms: int = 1000) -> FootageMap:
    return FootageMap(
        duration_ms=duration_ms,
        fps=30.0,
        width=320,
        height=240,
        sample_rate_hz=4.0,
        analysis_width=320,
        samples=[],
    )


def test_analysis_cache_hits_and_invalidates_on_content_or_parameters(tmp_path):
    source = tmp_path / "media.bin"
    source.write_bytes(b"first content")
    calls = 0

    def compute() -> FootageMap:
        nonlocal calls
        calls += 1
        return _footage_map()

    kwargs = {
        "namespace": "footage",
        "analyzer_version": "test-v1",
        "parameters": {"width": 320},
        "model": FootageMap,
        "compute": compute,
        "cache_dir": tmp_path / "cache",
    }
    assert load_or_compute(source, **kwargs) == _footage_map()
    assert load_or_compute(source, **kwargs) == _footage_map()
    assert calls == 1

    changed_parameters = {**kwargs, "parameters": {"width": 640}}
    load_or_compute(source, **changed_parameters)
    assert calls == 2

    source.write_bytes(b"changed content")
    load_or_compute(source, **kwargs)
    assert calls == 3


def test_footage_analyzer_reuses_cached_map_and_respects_parameter_changes(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"synthetic media bytes")
    calls = 0

    def fake_analysis(*args, **kwargs) -> FootageMap:
        nonlocal calls
        calls += 1
        return _footage_map()

    monkeypatch.setattr(footage, "_analyze_footage_uncached", fake_analysis)
    cache_dir = tmp_path / "cache"
    footage.analyze_footage(source, cache_dir=cache_dir)
    footage.analyze_footage(source, cache_dir=cache_dir)
    assert calls == 1

    footage.analyze_footage(source, analysis_width=160, cache_dir=cache_dir)
    assert calls == 2
