"""Main script for generating YouTube videos from text prompts.

Usage:
    python generate_video.py --prompt "explain photosynthesis" --duration 1.0 --output video.mp4
"""

import argparse
import threading
import time
from pathlib import Path
import os
from dotenv import load_dotenv
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from scene_planner import ScenePlanner
from tts_generator import TTSGenerator
from video_searcher import VideoSearcher
from clip_matcher import CLIPMatcher
from video_assembler import VideoAssembler
from license_tracker import LicenseTracker
from subtitle_generator import SubtitleGenerator


def generate_video(prompt: str, duration_minutes: float, output_path: Path,
                   orientation: str = 'landscape',
                   temp_dir: Path = None, keep_temp: bool = False,
                   script_text: str = None,
                   progress_callback=None,
                   narration_style: str = None,
                   subtitle_settings: dict = None,
                   intermediate_callback=None,
                   pre_selected_clips: list = None) -> Path:
    """Generate video from text prompt or script.

    Args:
        prompt: Topic/description for video (used if script_text is None)
        duration_minutes: Target duration in minutes
        output_path: Where to save final video
        orientation: Video orientation ('landscape', 'portrait', 'square', or 'any')
        temp_dir: Temporary working directory
        keep_temp: Keep temporary files after completion
        script_text: Optional user-provided narration script (bypasses LLM narration generation)
        progress_callback: Optional function(percent: int, step: str) called at each stage
        narration_style: Optional narration style override (viral, informative, funny, etc.)
        subtitle_settings: Optional dict to override subtitle style (font, font_size, color, etc.)
        intermediate_callback: Optional function(data: dict) called with intermediate file paths for re-generation
        pre_selected_clips: Optional list of clip dicts from wizard (skips LLM + CLIP scoring)

    Returns:
        Path to generated video file

    Raises:
        Exception: If any step fails
    """
    def _report(percent, step):
        if progress_callback:
            progress_callback(percent, step)

    output_path = Path(output_path)

    # Setup temp directory
    if temp_dir is None:
        temp_dir = Path("temp") / f"video_{output_path.stem}"
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    if script_text:
        print(f"GENERATING VIDEO FROM SCRIPT")
        print(f"Script length: {len(script_text.split())} words")
    else:
        print(f"GENERATING VIDEO: {prompt}")
    print(f"Duration: {duration_minutes} minutes")
    print(f"Orientation: {orientation}")
    print(f"Output: {output_path}")
    print(f"{'='*70}\n")

    try:
        # Initialize components
        print("Initializing components...")

        llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()

        narration_style = narration_style or os.getenv("NARRATION_STYLE", "viral").lower()

        # Skip ScenePlanner and CLIPMatcher when clips are pre-selected (saves LLM + model load time)
        planner = None
        matcher = None
        if not pre_selected_clips:
            if llm_provider == "openrouter":
                planner = ScenePlanner(
                    provider="openrouter",
                    model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001"),
                    base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                    api_key=os.getenv("OPENROUTER_API_KEY"),
                    narration_style=narration_style,
                )
            else:
                planner = ScenePlanner(
                    provider="ollama",
                    model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                    narration_style=narration_style,
                )

            matcher = CLIPMatcher(
                model_name=os.getenv("CLIP_MODEL", "ViT-B-32"),
                pretrained=os.getenv("CLIP_PRETRAINED", "laion2b_s34b_b79k"),
                cache_dir=Path(os.getenv("CACHE_DIR", "cache")),
                ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
                ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
            )

        tts = TTSGenerator(
            tts_engine=os.getenv("TTS_ENGINE", "kokoro"),
            # Piper settings
            piper_executable=os.getenv("PIPER_EXECUTABLE", "piper.exe"),
            model_path=os.getenv("PIPER_MODEL_PATH"),
            # Kokoro settings
            kokoro_voice=os.getenv("KOKORO_VOICE", "af_heart"),
            kokoro_speed=float(os.getenv("KOKORO_SPEED", "1.0")),
            kokoro_lang=os.getenv("KOKORO_LANG", "a"),
            # Orpheus settings
            orpheus_voice=os.getenv("ORPHEUS_VOICE", "tara"),
            # Chatterbox settings
            chatterbox_exaggeration=float(os.getenv("CHATTERBOX_EXAGGERATION", "0.5")),
            chatterbox_cfg=float(os.getenv("CHATTERBOX_CFG", "0.5")),
            chatterbox_voice_ref=os.getenv("CHATTERBOX_VOICE_REF", ""),
            # Common settings
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg")
        )

        searcher = VideoSearcher(api_key=os.getenv("PEXELS_API_KEY"))

        assembler = VideoAssembler(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
        )

        tracker = LicenseTracker()

        # Step 1: Generate video plan (continuous narration + visual clips)
        _step_times = {}
        _t0 = time.time()
        print("\n[1/5] Generating video plan...")
        _report(10, "Generating video plan...")

        # Adjust words-per-second target based on TTS engine and speed settings.
        # Manual override takes priority; otherwise use engine-specific defaults.
        # Chatterbox tends to speak faster, so fewer words = more natural pacing.
        current_tts_engine = os.getenv("TTS_ENGINE", "kokoro").lower()
        wps_override = os.getenv("WORDS_PER_SECOND", "").strip()
        if wps_override:
            words_per_second = float(wps_override)
        elif current_tts_engine == "kokoro":
            kokoro_speed = float(os.getenv("KOKORO_SPEED", "1.0"))
            words_per_second = 3.0 * kokoro_speed
        elif current_tts_engine == "chatterbox":
            words_per_second = 2.5  # Chatterbox speaks faster; fewer words = more natural
        else:
            words_per_second = 3.0

        loop_mode = os.getenv("ENABLE_VIDEO_LOOP", "false").lower() == "true"

        if pre_selected_clips and script_text:
            # Clips already selected in wizard — skip LLM call entirely
            from scene_planner import VisualClip, VideoPlan
            print("  Using pre-selected clips from wizard (skipping LLM)")
            plan_clips = [
                VisualClip(
                    keywords=c.get('keywords', ['stock', 'footage']),
                    visual_description=c.get('visual_description', 'stock footage')
                )
                for c in pre_selected_clips
            ]
            plan = VideoPlan(narration=script_text, clips=plan_clips)
        elif script_text:
            # User provided script - use as-is, generate only visual clips
            print("  Using user-provided script (no LLM narration modification)")
            plan = planner.generate_clips_from_script(script_text, duration_minutes)
        else:
            # Generate both narration and clips from prompt
            plan = planner.generate_continuous(prompt, duration_minutes,
                                               words_per_second=words_per_second,
                                               loop_mode=loop_mode)

        # Print token usage report (OpenRouter only)
        if not pre_selected_clips and llm_provider == "openrouter":
            print(planner.get_token_report())

        # Print plan preview with Unicode-safe encoding for Windows
        if not pre_selected_clips:
            try:
                print(planner.preview_continuous_plan(plan))
            except UnicodeEncodeError:
                preview = planner.preview_continuous_plan(plan)
                print(preview.encode('ascii', errors='replace').decode('ascii'))
        else:
            print(f"  Script: {len(script_text.split())} words")
            print(f"  Clips: {len(pre_selected_clips)} pre-selected")

        # Save LLM output to text file
        output_txt = Path(output_path).with_suffix('.txt')
        try:
            with open(output_txt, 'w', encoding='utf-8') as f:
                f.write("=" * 70 + "\n")
                f.write("VIDEO PLAN" + (" (pre-selected clips)" if pre_selected_clips else " (LLM)") + "\n")
                f.write("=" * 70 + "\n\n")

                f.write("NARRATION SCRIPT:\n")
                f.write("-" * 70 + "\n")
                f.write(plan.narration + "\n\n")

                f.write("=" * 70 + "\n")
                f.write(f"VISUAL CLIPS ({len(plan.clips)} clips):\n")
                f.write("=" * 70 + "\n\n")

                for i, clip in enumerate(plan.clips, 1):
                    f.write(f"Clip {i}:\n")
                    f.write(f"  Keywords: {', '.join(clip.keywords)}\n")
                    f.write(f"  Visual: {clip.visual_description}\n\n")

            print(f"  Saved LLM output to: {output_txt}")
        except Exception as e:
            print(f"  Warning: Could not save LLM output to file: {e}")

        # Step 2: Generate continuous narration audio with word timestamps
        _step_times['1_plan'] = time.time() - _t0
        _t0 = time.time()
        print("\n[2/5] Generating narration audio...")
        _report(25, "Generating narration audio...")
        narration_raw = temp_dir / "narration_raw.wav"
        narration_audio = temp_dir / "narration.wav"

        raw_path, heuristic_timestamps = tts.generate_with_timestamps(plan.narration, narration_raw)
        assembler.normalize_audio(raw_path, narration_audio)
        if raw_path.exists() and raw_path != narration_audio:
            raw_path.unlink()

        total_duration = assembler.get_duration(narration_audio)
        print(f"  Narration: {total_duration:.1f}s, {len(heuristic_timestamps)} words")

        # Get accurate word timestamps from ASR (faster-whisper) on the
        # normalized audio — the same file that gets muxed into the video.
        # Falls back to heuristic timestamps if ASR is unavailable.
        from tts_generator import transcribe_with_word_timestamps

        whisper_model = os.getenv("WHISPER_MODEL", "base")
        print(f"  Extracting word timestamps via ASR (model={whisper_model})...")
        asr_timestamps = transcribe_with_word_timestamps(narration_audio,
                                                          whisper_model=whisper_model)

        if asr_timestamps:
            word_timestamps = asr_timestamps
            print(f"  ASR: {len(word_timestamps)} words detected")
        else:
            # Fallback: use heuristic timestamps with time-scaling + offset
            word_timestamps = heuristic_timestamps
            if word_timestamps and word_timestamps[-1]['end'] > 0:
                raw_duration = word_timestamps[-1]['end']
                time_scale = total_duration / raw_duration if raw_duration > 0 else 1.0
                for wt in word_timestamps:
                    wt['start'] *= time_scale
                    wt['end'] *= time_scale

            subtitle_offset = float(os.getenv("SUBTITLE_OFFSET", "0.15"))
            if subtitle_offset != 0 and word_timestamps:
                for wt in word_timestamps:
                    wt['start'] = max(0.0, wt['start'] + subtitle_offset)
                    wt['end'] = wt['end'] + subtitle_offset
            print(f"  Using heuristic timestamps (offset={subtitle_offset}s)")

        # Step 3: Search, download, and score video clips
        _step_times['2_tts_asr'] = time.time() - _t0
        _t0 = time.time()
        enable_color_grading = os.getenv("ENABLE_COLOR_GRADING", "true").lower() == "true"
        color_preset = os.getenv("COLOR_PRESET", "balanced")

        # Pre-compute color grade filter once
        color_grade_filter = None
        if enable_color_grading:
            from effects_engine import EffectsEngine
            effects = EffectsEngine(
                ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
                ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
            )
            custom_lut = os.getenv("CUSTOM_LUT_FILE", "")
            color_grade_filter = effects.get_color_filter(
                preset=color_preset,
                custom_lut=Path(custom_lut) if custom_lut else None
            )

        # Calculate clip duration accounting for crossfade transition overlap.
        # With N clips and (N-1) crossfade transitions of T seconds each,
        # the final video duration = sum(clip_durations) - (N-1)*T.
        # To match the audio: clip_duration = (total_duration + (N-1)*T) / N.
        num_clips = len(plan.clips)
        transition_duration = float(os.getenv("TRANSITION_DURATION", "0.5"))
        transition_overlap = (num_clips - 1) * transition_duration if num_clips > 1 else 0
        clip_duration = (total_duration + transition_overlap) / num_clips

        print(f"  {num_clips} clips x {clip_duration:.1f}s each (incl. {transition_overlap:.1f}s transition overlap)")

        clip_lock = threading.Lock()

        if pre_selected_clips:
            # Fast path: download pre-selected clips directly (no search, no CLIP scoring)
            print("\n[3/5] Downloading selected clips...")
            _report(35, "Downloading selected clips...")

            clip_results = [None] * num_clips

            def _process_preselected_clip(i, clip_data):
                clip_dir = temp_dir / f"clip_{i}"
                clip_dir.mkdir(exist_ok=True)

                video_id = clip_data.get('video_id')
                preview_url = clip_data.get('video_preview_url')
                keywords = clip_data.get('keywords', [])

                print(f"\n  Clip {i+1}/{num_clips}: Downloading (Pexels ID: {video_id})...")

                # Fetch fresh video info by ID, fallback to stored preview URL
                download_path = clip_dir / "selected.mp4"
                video_info = None

                if video_id:
                    video_info = searcher.get_video_by_id(video_id)

                if video_info:
                    searcher.download(video_info, download_path)
                elif preview_url:
                    # Fallback: download directly from stored URL
                    import requests as _requests
                    print(f"    Using cached preview URL...")
                    resp = _requests.get(preview_url, stream=True, timeout=30)
                    resp.raise_for_status()
                    with open(download_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                else:
                    raise ValueError(f"No video ID or URL for clip {i}")

                # Track asset
                asset_info = {
                    'url': clip_data.get('author_url', ''),
                    'author': clip_data.get('author', 'Unknown'),
                    'provider': 'Pexels',
                    'license': 'Pexels License'
                }
                with clip_lock:
                    tracker.add_asset(asset_info, i)

                # Process clip: normalize + color grade + trim
                trimmed_path = clip_dir / "trimmed.mp4"
                assembler.process_clip(
                    download_path, trimmed_path,
                    duration_sec=clip_duration,
                    color_grade_filter=color_grade_filter,
                    orientation=orientation
                )

                print(f"  Clip {i+1}/{num_clips} done")
                return trimmed_path

            with ThreadPoolExecutor(max_workers=2) as clip_pool:
                futures = {
                    clip_pool.submit(_process_preselected_clip, i, clip_data): i
                    for i, clip_data in enumerate(pre_selected_clips)
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    clip_results[idx] = future.result()

            trimmed_videos = clip_results

        else:
            # Full path: search Pexels, download candidates, score with CLIP
            print("\n[3/5] Searching and scoring videos...")
            _report(35, "Searching and scoring videos...")
            used_video_ids = set()
            candidates_count = int(os.getenv("CLIP_CANDIDATES", "5"))

            clip_results = [None] * num_clips

            def _download_candidate(args):
                vid_info, dl_path = args
                searcher.download(vid_info, dl_path)
                return {'path': dl_path, 'info': vid_info}

            def _process_clip(i, clip):
                clip_dir = temp_dir / f"clip_{i}"
                clip_dir.mkdir(exist_ok=True)

                print(f"\n  Clip {i+1}/{num_clips}: Searching for {clip.keywords}...")
                videos = searcher.search(clip.keywords, max_results=20, orientation=orientation)

                if not videos:
                    print(f"    Warning: No videos found, trying broader search...")
                    videos = searcher.search(clip.keywords[:1], max_results=20, orientation=orientation)

                if not videos:
                    raise ValueError(f"No videos found for clip {i} keywords: {clip.keywords}")

                # Filter out duplicates (thread-safe read)
                with clip_lock:
                    available_videos = [v for v in videos if v.id not in used_video_ids]
                if not available_videos:
                    available_videos = videos

                # Download candidates in parallel
                downloaded = []
                with ThreadPoolExecutor(max_workers=4) as dl_pool:
                    dl_futures = []
                    for j, video_info in enumerate(available_videos[:candidates_count]):
                        download_path = clip_dir / f"candidate_{j}.mp4"
                        dl_futures.append(dl_pool.submit(_download_candidate, (video_info, download_path)))

                    for future in as_completed(dl_futures):
                        try:
                            downloaded.append(future.result())
                        except Exception as e:
                            print(f"    Warning: Download failed: {e}")

                if not downloaded:
                    raise ValueError(f"Failed to download any videos for clip {i}")

                # Score with CLIP (frame extraction parallelized internally)
                score_text = f"{clip.visual_description}. {' '.join(clip.keywords)}"
                video_paths = [d['path'] for d in downloaded]

                print(f"    Scoring {len(video_paths)} candidates with CLIP...")
                scores = matcher.score_videos(video_paths, score_text)

                best_path = max(scores, key=scores.get)
                best_score = scores[best_path]
                best_info = next(d['info'] for d in downloaded if d['path'] == best_path)

                print(f"    Best match: score={best_score:.3f}")

                # Track asset (thread-safe)
                with clip_lock:
                    tracker.add_asset(best_info.to_dict(), i)
                    used_video_ids.add(best_info.id)

                # Single-pass: normalize + color grade + trim
                trimmed_path = clip_dir / "trimmed.mp4"
                assembler.process_clip(
                    best_path, trimmed_path,
                    duration_sec=clip_duration,
                    color_grade_filter=color_grade_filter,
                    orientation=orientation
                )

                # Clean up candidates
                for d in downloaded:
                    if d['path'] != best_path and d['path'].exists():
                        d['path'].unlink()

                print(f"  Clip {i+1}/{num_clips} done")
                return trimmed_path

            with ThreadPoolExecutor(max_workers=2) as clip_pool:
                futures = {
                    clip_pool.submit(_process_clip, i, clip): i
                    for i, clip in enumerate(plan.clips)
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    clip_results[idx] = future.result()

            trimmed_videos = clip_results

        # Save original trimmed clips BEFORE loop/outro modification (for editor persistence)
        original_trimmed_videos = list(trimmed_videos)

        # Loop mode: replace last clip with a copy of the first clip
        if loop_mode and len(trimmed_videos) >= 2:
            print(f"\n  Loop mode: replacing last clip with first clip for seamless loop...")
            loop_clip_dir = temp_dir / f"clip_{num_clips - 1}"
            loop_clip_dir.mkdir(exist_ok=True)
            loop_clip_path = loop_clip_dir / "loop_copy.mp4"
            shutil.copy(trimmed_videos[0], loop_clip_path)
            trimmed_videos[-1] = loop_clip_path

        # Non-loop: extend last clip by freezing its final frame (avoids abrupt cut)
        if not loop_mode:
            outro_duration = float(os.getenv("VIDEO_OUTRO_DURATION", "1.0"))
            if outro_duration > 0 and trimmed_videos:
                print(f"\n  Adding {outro_duration}s outro (freeze-frame) to last clip...")
                last_clip_dir = temp_dir / f"clip_{num_clips - 1}"

                last_video = trimmed_videos[-1]
                extended_path = last_clip_dir / "extended_outro.mp4"

                import subprocess
                from app.core.config import settings as _cfg
                cmd = [
                    os.getenv("FFMPEG_PATH", "ffmpeg"),
                    '-i', str(last_video),
                    '-vf', f'tpad=stop_mode=clone:stop_duration={outro_duration}',
                    '-c:v', 'libx264',
                    '-preset', _cfg.ffmpeg_intermediate_preset,
                    '-crf', str(_cfg.ffmpeg_intermediate_crf),
                    '-pix_fmt', 'yuv420p',
                    '-threads', str(_cfg.ffmpeg_threads),
                    '-an',
                    '-y',
                    str(extended_path)
                ]
                subprocess.run(cmd, capture_output=True, check=True)
                trimmed_videos[-1] = extended_path

        # Step 4: Assemble final video
        _step_times['3_clips'] = time.time() - _t0
        _t0 = time.time()
        print("\n[4/5] Assembling final video...")
        _report(75, "Assembling final video...")

        # Concatenate video clips with transitions
        transition_type = os.getenv("TRANSITION_TYPE", "crossfade")

        print(f"  Concatenating {len(trimmed_videos)} clips with {transition_type} transitions...")
        video_no_audio = temp_dir / "video_no_audio.mp4"
        assembler.concat_videos_with_transitions(
            trimmed_videos,
            video_no_audio,
            transition=transition_type,
            transition_duration=transition_duration
        )

        # Add background music if enabled
        music_file = None
        final_audio = narration_audio
        if os.getenv("ENABLE_BACKGROUND_MUSIC", "true").lower() == "true":
            print("  Mixing background music...")

            from music_selector import MusicSelector
            from audio_mixer import AudioMixer

            selector = MusicSelector(music_dir=Path(os.getenv("MUSIC_DIR", "music")))
            music_mood = os.getenv("MUSIC_MOOD", None)

            try:
                music_file = selector.select_music(prompt, mood=music_mood)
                print(f"    Selected music: {music_file.name} (mood: {selector._detect_mood(prompt)})")

                mixer = AudioMixer(
                    ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
                    ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
                )
                mixed_audio = temp_dir / "audio_final.wav"

                music_volume = float(os.getenv("MUSIC_VOLUME", "0.15"))
                mixer.mix_with_background_music(
                    narration_audio, music_file, mixed_audio,
                    music_volume=music_volume
                )
                final_audio = mixed_audio
            except ValueError as e:
                print(f"    Warning: {e}")
                print(f"    Continuing without background music")

        # Prepare subscribe overlay if enabled
        needs_overlay = os.getenv("ENABLE_SUBSCRIBE_OVERLAY", "false").lower() == "true"
        needs_subtitles = os.getenv("ENABLE_SUBTITLES", "true").lower() == "true"

        subscribe_video_path = None
        subscribe_params = None
        if needs_overlay:
            subscribe_path = Path(os.getenv("SUBSCRIBE_VIDEO_PATH", "subscribe.mp4"))
            if not subscribe_path.is_absolute():
                subscribe_path = Path(__file__).parent / subscribe_path
            if subscribe_path.exists():
                subscribe_video_path = subscribe_path
                subscribe_params = {
                    'orientation': orientation,
                    'scale': float(os.getenv("SUBSCRIBE_SCALE", "0")) or None,
                    'margin_bottom': int(os.getenv("SUBSCRIBE_MARGIN_BOTTOM", "50")),
                    'chromakey_color': os.getenv("SUBSCRIBE_CHROMAKEY_COLOR", "0x00FF00"),
                    'chromakey_similarity': float(os.getenv("SUBSCRIBE_CHROMAKEY_SIMILARITY", "0.3")),
                    'chromakey_blend': float(os.getenv("SUBSCRIBE_CHROMAKEY_BLEND", "0.15")),
                }
            else:
                print(f"    Warning: Subscribe video not found at {subscribe_path}, skipping overlay")

        # Generate subtitle file if enabled
        subtitle_file = None
        if needs_subtitles:
            print("  Generating subtitles...")

            sub_gen = SubtitleGenerator(
                style=os.getenv("SUBTITLE_STYLE", "youtube"),
                orientation=orientation,
                custom_overrides=subtitle_settings,
            )
            subtitle_file = temp_dir / "subtitles.ass"

            if word_timestamps:
                if orientation in ('portrait', 'square'):
                    print(f"  Using karaoke-style subtitles (phrase grouping with highlight)...")
                    sub_gen.generate_karaoke_ass(word_timestamps, subtitle_file)
                else:
                    print(f"  Using phrase-based subtitles (2-3 words, accurate sync)...")
                    sub_gen.generate_phrase_ass(word_timestamps, subtitle_file)
            else:
                import re as _re
                sentences = _re.split(r'(?<=[.!?])\s+', plan.narration.strip())
                sentences = [s for s in sentences if s.strip()]

                if sentences:
                    print(f"  Using sentence-based subtitles (no ASR timestamps)...")
                    total_chars = sum(len(s) for s in sentences)
                    subtitle_scenes = []
                    current_time = 0.0

                    for sentence in sentences:
                        duration = total_duration * (len(sentence) / total_chars) if total_chars > 0 else 0
                        subtitle_scenes.append({
                            'narration': sentence,
                            'start_time': current_time,
                            'duration': duration
                        })
                        current_time += duration

                    sub_gen.generate_ass(subtitle_scenes, subtitle_file)
                else:
                    sub_gen.generate_ass([{
                        'narration': plan.narration,
                        'start_time': 0.0,
                        'duration': total_duration
                    }], subtitle_file)

        # Determine fade-out duration
        fade_dur = 0
        if not loop_mode:
            fade_dur = float(os.getenv("FADE_OUT_DURATION", "1.0"))

        # Finalize: mux audio + overlay + subtitles + fade in a single FFmpeg pass
        from effects_engine import EffectsEngine
        finalizer = EffectsEngine(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
        )

        effects_summary = []
        if subscribe_video_path:
            effects_summary.append("overlay")
        if subtitle_file:
            effects_summary.append("subtitles")
        if fade_dur > 0:
            effects_summary.append(f"fade-out ({fade_dur}s)")

        if effects_summary:
            print(f"  Finalizing video (mux + {' + '.join(effects_summary)}) in single pass...")
        else:
            print("  Muxing audio and video...")

        finalizer.finalize_video(
            video_no_audio=video_no_audio,
            audio_path=final_audio,
            output_path=output_path,
            subtitle_path=subtitle_file,
            subscribe_video=subscribe_video_path,
            subscribe_params=subscribe_params,
            fade_duration=fade_dur,
        )

        # Save intermediate data for subtitle re-generation and editor
        if intermediate_callback:
            intermediate_callback({
                'temp_dir': str(temp_dir),
                'video_no_audio': str(video_no_audio),
                'final_audio': str(final_audio),
                'narration_audio': str(narration_audio),
                'music_file': str(music_file) if music_file else None,
                'word_timestamps': word_timestamps,
                'trimmed_clips': [str(p) for p in original_trimmed_videos],
                'clip_duration': clip_duration,
                'transition_duration': transition_duration,
            })

        # Generate credits
        _step_times['4_assemble'] = time.time() - _t0
        _t0 = time.time()
        print("\n[5/5] Generating credits...")
        _report(95, "Generating credits...")
        credits_path = output_path.parent / f"{output_path.stem}_credits.txt"
        tracker.export_credits_txt(credits_path)

        credits_json = output_path.parent / f"{output_path.stem}_credits.json"
        tracker.export_credits_json(credits_json)

        _step_times['5_credits'] = time.time() - _t0

        # Print timing breakdown to identify bottlenecks
        total_time = sum(_step_times.values())
        print(f"\n{'='*70}")
        print("TIMING BREAKDOWN:")
        print(f"  [1] LLM Plan:        {_step_times.get('1_plan', 0):6.1f}s")
        print(f"  [2] TTS + ASR:       {_step_times.get('2_tts_asr', 0):6.1f}s")
        print(f"  [3] Clip Search/DL:  {_step_times.get('3_clips', 0):6.1f}s")
        print(f"  [4] Assemble/Encode: {_step_times.get('4_assemble', 0):6.1f}s")
        print(f"  [5] Credits:         {_step_times.get('5_credits', 0):6.1f}s")
        print(f"  {'─'*35}")
        print(f"  TOTAL:               {total_time:6.1f}s")
        print(f"{'='*70}")
        print("SUCCESS!")
        print(f"Video: {output_path}")
        print(f"Credits: {credits_path}")
        print(f"{'='*70}\n")

        return output_path

    finally:
        # Cleanup
        if not keep_temp and temp_dir.exists():
            print("\nCleaning up temporary files...")
            shutil.rmtree(temp_dir)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate YouTube videos from text prompts using local AI"
    )

    # Create mutually exclusive group for prompt vs script
    input_group = parser.add_mutually_exclusive_group(required=True)

    input_group.add_argument(
        "--prompt",
        type=str,
        help="Topic or description for the video (LLM generates narration)"
    )

    input_group.add_argument(
        "--script",
        type=str,
        help="Narration script: path to .txt file OR inline text (used as-is, no LLM modification)"
    )

    parser.add_argument(
        "--duration",
        type=float,
        required=False,
        help="Video duration in minutes (required for --prompt, optional for --script - auto-calculated from word count)"
    )

    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output video file path (e.g., video.mp4)"
    )

    parser.add_argument(
        "--temp-dir",
        type=str,
        default=None,
        help="Temporary working directory (default: temp/video_<name>)"
    )

    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary files after completion"
    )

    parser.add_argument(
        "--orientation",
        type=str,
        choices=["landscape", "portrait", "square", "any"],
        default=None,
        help="Video orientation: landscape (horizontal), portrait (vertical), square, or any (default: from .env or landscape)"
    )

    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Handle script vs prompt input
    script_text = None
    duration_minutes = args.duration

    if args.script:
        # Load script from file or inline text
        from script_loader import load_script, estimate_duration_from_script

        try:
            script_input = load_script(args.script)
            script_text = script_input.text

            if script_input.source == 'file':
                print(f"Loaded script from file: {script_input.file_path}")
            else:
                print(f"Using inline script ({len(script_text.split())} words)")

        except (FileNotFoundError, ValueError) as e:
            print(f"Error loading script: {e}")
            return 1

        # Calculate words_per_second for duration estimation
        current_tts_engine = os.getenv("TTS_ENGINE", "kokoro").lower()
        wps_override = os.getenv("WORDS_PER_SECOND", "").strip()
        if wps_override:
            words_per_second = float(wps_override)
        elif current_tts_engine == "kokoro":
            kokoro_speed = float(os.getenv("KOKORO_SPEED", "1.0"))
            words_per_second = 3.0 * kokoro_speed
        elif current_tts_engine == "chatterbox":
            words_per_second = 2.5
        else:
            words_per_second = 3.0

        # Estimate duration if not provided
        if duration_minutes is None:
            duration_minutes = estimate_duration_from_script(script_text, words_per_second)
            print(f"Estimated duration: {duration_minutes:.2f} minutes ({len(script_text.split())} words at {words_per_second:.1f} wps)")
        else:
            print(f"Using user-specified duration: {duration_minutes} minutes")

    else:
        # Using --prompt, duration is required
        if duration_minutes is None:
            print("Error: --duration is required when using --prompt")
            return 1

    # Validate environment
    required_vars = ["PEXELS_API_KEY"]

    # Check provider-specific requirements
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if llm_provider == "openrouter":
        required_vars.append("OPENROUTER_API_KEY")

    tts_engine = os.getenv("TTS_ENGINE", "kokoro").lower()
    if tts_engine == "piper":
        required_vars.append("PIPER_MODEL_PATH")

    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}")
        print("Please configure .env file (see .env.example)")
        return 1

    # Get orientation from args or .env
    orientation = args.orientation if args.orientation else os.getenv("VIDEO_ORIENTATION", "landscape")

    # Generate video
    try:
        generate_video(
            prompt=args.prompt,
            duration_minutes=duration_minutes,
            output_path=Path(args.output),
            orientation=orientation,
            temp_dir=Path(args.temp_dir) if args.temp_dir else None,
            keep_temp=args.keep_temp,
            script_text=script_text
        )
        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
