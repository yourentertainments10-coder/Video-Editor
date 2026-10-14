from poc.timeline import (AudioProperties, EditorClip, EditorSettings, EditorTimeline, Layer, MediaAsset,
                          TimeBase, TimelineMarker, Track, TransitionRef)
def fixture(n=7, clip_ms=2000, beat_ms=500):
    assets=[MediaAsset(asset_id=f"v{i}", kind="video", uri=f"file:///D:/clips/clip_{i}.mp4", duration_ms=20000) for i in range(1,4)]
    assets.append(MediaAsset(asset_id="m", kind="audio", uri="file:///D:/music/song.mp3", duration_ms=120000))
    clips=[EditorClip(clip_id=f"c{k:03d}", asset_id=f"v{k%3+1}", track_id="video", start_tick=k*clip_ms, duration_ticks=clip_ms,
                      source_in_ms=1000+k*500, source_out_ms=1000+k*500+clip_ms, score=0.5+k/100,
                      rationale={"quality":0.8,"motion_match":0.6,"reuse":0.1,"coverage":0.4}) for k in range(n)]
    clips.append(EditorClip(clip_id="music_clip", asset_id="m", track_id="music", start_tick=0, duration_ticks=n*clip_ms,
                            source_in_ms=0, source_out_ms=n*clip_ms, audio=AudioProperties(gain_db=-3)))
    markers=[TimelineMarker(marker_id=f"b{i}", kind="beat", position_tick=i*beat_ms) for i in range(n*clip_ms//beat_ms)]
    return EditorTimeline(project_id="p", timeline_id="t", duration_ticks=n*clip_ms, time_base=TimeBase(ticks_per_second=1000),
        settings=EditorSettings(fps_num=30, fps_den=1, width=1920, height=1080), assets=assets,
        layers=[Layer(layer_id="visual", name="Visual", order=0), Layer(layer_id="audio", name="Audio", order=1)],
        tracks=[Track(track_id="video", kind="video", layer_id="visual"), Track(track_id="overlay", kind="overlay", layer_id="visual"),
                Track(track_id="voice", kind="audio", layer_id="audio"), Track(track_id="music", kind="music", layer_id="audio")],
        clips=clips, markers=markers)
