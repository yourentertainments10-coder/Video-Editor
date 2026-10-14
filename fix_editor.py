with open('poc/render/editor.py', 'r') as f:
    content = f.read()

old = '''                    def process_chunk(chunk_idx, chunk_segments, chunk_joins):
                        if not chunk_segments:
                            return None

                        # Calculate chunk timing
                        chunk_start_frames = sum(s.duration_frames for s in chunk_segments[:chunk_idx]) if chunk_idx > 0 else 0
                        chunk_duration_frames = sum(s.duration_frames for s in chunk_segments)

                        # Create ASS filter for titles in this chunk
                        chunk_ass_filter = None
                        if plan.text_overlays:
                            # Filter and shift text overlays for this chunk
                            chunk_overlays = []
                            for overlay in plan.text_overlays:
                                overlay_start_frame = overlay.start_ms * plan.fps // 1000
                                overlay_end_frame = overlay.end_ms * plan.fps // 1000
                                chunk_start_frame_abs = chunk_start_frames
                                chunk_end_frame_abs = chunk_start_frame_abs + chunk_duration_frames

                                # Check if overlay intersects with this chunk
                                if overlay_end_frame > chunk_start_frame_abs and overlay_start_frame < chunk_end_frame_abs:
                                    # Calculate fade settings for chunk edges
                                    fade_in_ms = overlay.fade_ms
                                    fade_out_ms = overlay.fade_ms

                                    # If overlay starts before chunk, no fade-in at chunk start
                                    if overlay_start_frame <= chunk_start_frame_abs:
                                        fade_in_ms = 0
                                    # If overlay ends after chunk, no fade-out at chunk end
                                    if overlay_end_frame >= chunk_end_frame_abs:
                                        fade_out_ms = 0

                                    # Create adjusted overlay for this chunk
                                    adjusted_overlay = TextOverlay(
                                        text=overlay.text,
                                        start_ms=max(0, (overlay_start_frame - chunk_start_frame_abs) * 1000 // plan.fps),
                                        end_ms=min(chunk_duration_frames * 1000 // plan.fps,
                                                   (overlay_end_frame - chunk_start_frame_abs) * 1000 // plan.fps),
                                        x=overlay.x,
                                        y=overlay.y,
                                        size_pct=overlay.size_pct,
                                        color=overlay.color,
                                        outline_px=overlay.outline_px,
                                        bold=overlay.bold,
                                        fade_ms=min(fade_in_ms, fade_out_ms),  # Use minimum to be safe
                                        font=overlay.font
                                    )
                                    chunk_overlays.append(adjusted_overlay)

                            if chunk_overlays:
                                ass_path = temp_dir / f"overlays_{chunk_idx:04d}.ass"
                                _write_ass(ass_path, chunk_overlays, plan.width, plan.height)
                                chunk_ass_filter = _ass_filter(Path(ass_path.name))

                        # Render the chunk
                        chunk_path = temp_dir / f"chunk_{chunk_idx:04d}.mp4"
                        render_sequence_to_file(
                            chunk_segments, chunk_joins, chunk_path, width=plan.width,
                            height=plan.height, fps=plan.fps, audio_path=None,
                            crf=selected.crf, preset=selected.preset, input_seeking=True,
                            final_filter=chunk_ass_filter, cwd=temp_dir, cancel=cancelled,
                        )
                        return chunk_path'''

new = '''                    def process_chunk(chunk_idx, chunk_start, chunk_end):
                        if chunk_end <= chunk_start:
                            return None

                        # Build Segment and Join lists for this chunk
                        chunk_segments = []
                        chunk_joins = []
                        for i in range(chunk_start, chunk_end):
                            item, path = resolved_segments[i]
                            left_frames = item.transition_frames
                            base_ms = round(item.duration_frames * 1000 / plan.fps)
                            source_in = (0 if item.kind == "gap" else
                                         round(left_frames * 1000 / plan.fps) if item.kind == "image" else
                                         int(item.source_in_ms or 0))
                            chunk_segments.append(Segment(
                                path=path, src_in_ms=source_in, src_out_ms=source_in + base_ms,
                                vfilter=item.vfilter,
                            ))

                            # Add join to next segment (except for last segment in chunk)
                            if i < chunk_end - 1:
                                next_item, _ = resolved_segments[i + 1]
                                # Find the join between these segments in the original list
                                original_index = resolved_segments.index((item, path))
                                if original_index < len(actual_joins):
                                    chunk_joins.append(actual_joins[original_index])

                        # Calculate chunk timing
                        chunk_start_frames = sum(
                            item.duration_frames for _, item in resolved_segments[chunk_start:chunk_idx]
                        ) if chunk_idx > 0 else 0
                        chunk_duration_frames = sum(
                            item.duration_frames for _, item in resolved_segments[chunk_start:chunk_end]
                        )

                        # Create ASS filter for titles in this chunk
                        chunk_ass_filter = None
                        if plan.text_overlays:
                            # Filter and shift text overlays for this chunk
                            chunk_overlays = []
                            for overlay in plan.text_overlays:
                                overlay_start_frame = overlay.start_ms * plan.fps // 1000
                                overlay_end_frame = overlay.end_ms * plan.fps // 1000
                                chunk_start_frame_abs = chunk_start_frames
                                chunk_end_frame_abs = chunk_start_frame_abs + chunk_duration_frames

                                # Check if overlay intersects with this chunk
                                if overlay_end_frame > chunk_start_frame_abs and overlay_start_frame < chunk_end_frame_abs:
                                    # Calculate fade settings for chunk edges
                                    fade_in_ms = overlay.fade_ms
                                    fade_out_ms = overlay.fade_ms

                                    # If overlay starts before chunk, no fade-in at chunk start
                                    if overlay_start_frame <= chunk_start_frame_abs:
                                        fade_in_ms = 0
                                    # If overlay ends after chunk, no fade-out at chunk end
                                    if overlay_end_frame >= chunk_end_frame_abs:
                                        fade_out_ms = 0

                                    # Create adjusted overlay for this chunk
                                    adjusted_overlay = TextOverlay(
                                        text=overlay.text,
                                        start_ms=max(0, (overlay_start_frame - chunk_start_frame_abs) * 1000 // plan.fps),
                                        end_ms=min(chunk_duration_frames * 1000 // plan.fps,
                                                   (overlay_end_frame - chunk_start_frame_abs) * 1000 // plan.fps),
                                        x=overlay.x,
                                        y=overlay.y,
                                        size_pct=overlay.size_pct,
                                        color=overlay.color,
                                        outline_px=overlay.outline_px,
                                        bold=overlay.bold,
                                        fade_ms=min(fade_in_ms, fade_out_ms),  # Use minimum to be safe
                                        font=overlay.font
                                    )
                                    chunk_overlays.append(adjusted_overlay)

                            if chunk_overlays:
                                ass_path = temp_dir / f"overlays_{chunk_idx:04d}.ass"
                                _write_ass(ass_path, chunk_overlays, plan.width, plan.height)
                                chunk_ass_filter = _ass_filter(Path(ass_path.name))

                        # Render the chunk
                        chunk_path = temp_dir / f"chunk_{chunk_idx:04d}.mp4"
                        render_sequence_to_file(
                            chunk_segments, chunk_joins, chunk_path, width=plan.width,
                            height=plan.height, fps=plan.fps, audio_path=None,
                            crf=selected.crf, preset=selected.preset, input_seeking=True,
                            final_filter=chunk_ass_filter, cwd=temp_dir, cancel=cancelled,
                        )
                        return chunk_path'''

if old not in content:
    print("OLD STRING NOT FOUND")
else:
    content = content.replace(old, new)
    with open('poc/render/editor.py', 'w') as f:
        f.write(content)
    print("Replaced successfully")