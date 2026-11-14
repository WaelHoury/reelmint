"""Main script for generating YouTube videos from text prompts.

Usage:
    python generate_video.py --prompt "explain photosynthesis" --duration 1.0 --output video.mp4
    python generate_video.py --batch examples/batch-topics.txt --duration 0.5 --output out/
"""

import argparse
import threading
import time
from pathlib import Path
import os
from dotenv import load_dotenv
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from scene_planner import ScenePlanner, create_planner
from tts_generator import TTSGenerator
from video_searcher import VideoSearcher
from app.media.multi_source_searcher import MultiSourceSearcher
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
                   pre_selected_clips: list = None,
                   tts_selection: dict = None,
                   subtitles_enabled: bool = None,
                   subtitle_style: str = None,
                   loop_mode: bool = None) -> Path:
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
        tts_selection: Optional dict with {provider, voice_key, label} for TTS provider routing

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
        temp_dir = Path(os.getenv("TEMP_DIR") or "temp") / f"video_{output_path.stem}"
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
            planner = create_planner(narration_style=narration_style, provider=llm_provider)

            matcher = CLIPMatcher(
                model_name=os.getenv("CLIP_MODEL", "ViT-B-32"),
                pretrained=os.getenv("CLIP_PRETRAINED", "laion2b_s34b_b79k"),
                cache_dir=Path(os.getenv("CACHE_DIR", "cache")),
                ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
                ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
            )

        # TTS setup — use provider registry if tts_selection is provided,
        # otherwise fall back to legacy TTSGenerator
        _use_provider_registry = bool(tts_selection and tts_selection.get("voice_key"))
        tts = None
        tts_registry = None
        tts_provider_name = None
        tts_voice_id = None

        if _use_provider_registry:
            from app.tts import get_tts_registry
            from app.tts.registry import parse_voice_key
            tts_registry = get_tts_registry()
            tts_provider_name, tts_voice_id = parse_voice_key(tts_selection["voice_key"])
            requested_provider = (tts_selection.get("provider") or "").strip()
            if requested_provider and requested_provider != tts_provider_name:
                raise ValueError(
                    "tts_selection provider does not match the provider encoded in voice_key"
                )
            print(f"  TTS: provider={tts_provider_name}, voice={tts_voice_id}")
        else:
            tts = TTSGenerator(
                tts_engine=os.getenv("TTS_ENGINE", "kokoro"),
                piper_executable=os.getenv("PIPER_EXECUTABLE", "piper.exe" if os.name == "nt" else "piper"),
                model_path=os.getenv("PIPER_MODEL_PATH"),
                kokoro_voice=os.getenv("KOKORO_VOICE", "af_heart"),
                kokoro_speed=float(os.getenv("KOKORO_SPEED", "1.0")),
                kokoro_lang=os.getenv("KOKORO_LANG", "a"),
                orpheus_voice=os.getenv("ORPHEUS_VOICE", "tara"),
                chatterbox_exaggeration=float(os.getenv("CHATTERBOX_EXAGGERATION", "0.5")),
                chatterbox_cfg=float(os.getenv("CHATTERBOX_CFG", "0.5")),
                chatterbox_voice_ref=os.getenv("CHATTERBOX_VOICE_REF", ""),
                ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg")
            )

        pexels_searcher = VideoSearcher(api_key=os.getenv("PEXELS_API_KEY"))

        pixabay_searcher = None
        enable_pixabay = os.getenv("ENABLE_PIXABAY", "false").lower() == "true"
        enable_image_fallback = os.getenv("ENABLE_IMAGE_FALLBACK", "false").lower() == "true"
        # Pixabay is also needed for the image fallback, and to re-fetch
        # Pixabay clips the user picked in the web wizard.
        if enable_pixabay or enable_image_fallback or pre_selected_clips:
            pixabay_key = os.getenv("PIXABAY_API_KEY", "")
            if pixabay_key:
                from app.media.pixabay_search import PixabaySearcher
                pixabay_searcher = PixabaySearcher(api_key=pixabay_key)

        searcher = MultiSourceSearcher(
            pexels_searcher=pexels_searcher,
            pixabay_searcher=pixabay_searcher,
            enable_pixabay=enable_pixabay,
            enable_image_fallback=enable_image_fallback,
        )

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
        wps_override = os.getenv("WORDS_PER_SECOND", "").strip()
        if wps_override:
            words_per_second = float(wps_override)
        elif _use_provider_registry:
            from app.core.config import get_words_per_second as _get_wps
            words_per_second = _get_wps(provider=tts_provider_name)
        else:
            current_tts_engine = os.getenv("TTS_ENGINE", "kokoro").lower()
            if current_tts_engine == "kokoro":
                kokoro_speed = float(os.getenv("KOKORO_SPEED", "1.0"))
                words_per_second = 3.0 * kokoro_speed
            elif current_tts_engine == "chatterbox":
                words_per_second = 2.5
            else:
                words_per_second = 3.0

        # Per-video settings win; .env values are the defaults.
        if loop_mode is None:
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

        if _use_provider_registry:
            tts_result = tts_registry.generate(
                text=plan.narration,
                provider_name=tts_provider_name,
                voice_id=tts_voice_id,
                output_dir=temp_dir,
                with_timestamps=True,
            )
            raw_path = tts_result.audio_path
            heuristic_timestamps = tts_result.word_timestamps or []
        else:
            raw_path, heuristic_timestamps = tts.generate_with_timestamps(plan.narration, narration_raw)

        assembler.normalize_audio(raw_path, narration_audio)
        if raw_path.exists() and raw_path != narration_audio:
            raw_path.unlink()

        total_duration = assembler.get_duration(narration_audio)
        print(f"  Narration: {total_duration:.1f}s, {len(heuristic_timestamps)} words")

        # Get accurate word timestamps from ASR (faster-whisper) on the
        # normalized audio — the same file that gets muxed into the video.
        # Falls back to heuristic timestamps if ASR is unavailable.
        from tts_generator import transcribe_with_word_timestamps, whisper_language_for_kokoro

        # Tell Whisper the narration language when we know it (Kokoro voices
        # encode it); otherwise it auto-detects.
        if _use_provider_registry:
            whisper_language = (whisper_language_for_kokoro(tts_voice_id)
                                if tts_provider_name == "kokoro" else None)
        else:
            whisper_language = getattr(tts, "whisper_language", None)

        whisper_model = os.getenv("WHISPER_MODEL", "base")
        print(f"  Extracting word timestamps via ASR (model={whisper_model})...")
        asr_timestamps = transcribe_with_word_timestamps(narration_audio,
                                                          whisper_model=whisper_model,
                                                          language=whisper_language)

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

            # Last resort: generate heuristic timestamps from text + audio duration
            # when both ASR and provider timestamps are empty (e.g. Fish TTS)
            if not word_timestamps and plan.narration.strip():
                words = plan.narration.split()
                total_weight = sum(TTSGenerator._get_word_weight(w) for w in words)
                t = 0.0
                word_timestamps = []
                for word in words:
                    weight = TTSGenerator._get_word_weight(word)
                    word_dur = total_duration * (weight / total_weight) if total_weight > 0 else 0
                    word_timestamps.append({
                        'word': word,
                        'start': t,
                        'end': t + word_dur,
                    })
                    t += word_dur
                print(f"  Generated heuristic timestamps from text ({len(word_timestamps)} words, {total_duration:.1f}s)")

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
                provider = clip_data.get('provider') or 'Pexels'
                media_type = clip_data.get('media_type') or 'video'
                is_image = media_type == 'image'

                print(f"\n  Clip {i+1}/{num_clips}: Downloading ({provider} {media_type} ID: {video_id})...")

                # Fetch fresh media info by ID, fallback to stored preview URL
                media_info = None
                if video_id:
                    try:
                        media_info = searcher.get_video_by_id(
                            video_id, provider=provider, media_type=media_type)
                    except Exception as e:
                        print(f"    Warning: {provider} lookup failed: {e}")
                        media_info = None

                def _image_ext(url):
                    path = (url or '').split('?', 1)[0].lower()
                    for ext in ('.jpg', '.jpeg', '.png', '.webp'):
                        if path.endswith(ext):
                            return ext
                    return '.jpg'

                if is_image:
                    source_url = media_info.download_url if media_info else preview_url
                    download_path = clip_dir / f"selected{_image_ext(source_url)}"
                else:
                    download_path = clip_dir / "selected.mp4"

                if media_info:
                    searcher.download(media_info, download_path)
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
                    raise ValueError(f"No media ID or URL for clip {i}")

                # Track asset with the clip's actual provider/license
                if provider == 'Pixabay':
                    license_name = 'Pixabay License'
                else:
                    license_name = 'Pexels License'
                asset_info = {
                    'url': clip_data.get('author_url', ''),
                    'author': clip_data.get('author') or 'Unknown',
                    'provider': provider,
                    'license': license_name,
                }
                with clip_lock:
                    tracker.add_asset(asset_info, i)

                trimmed_path = clip_dir / "trimmed.mp4"
                if is_image:
                    # Still image: render with a Ken Burns effect
                    from video_assembler import KEN_BURNS_EFFECTS
                    effect = KEN_BURNS_EFFECTS[i % len(KEN_BURNS_EFFECTS)]
                    assembler.image_to_video(
                        download_path, trimmed_path,
                        duration_sec=clip_duration,
                        orientation=orientation,
                        effect=effect,
                        color_grade_filter=color_grade_filter,
                    )
                    print(f"  Clip {i+1}/{num_clips} done (image, {effect})")
                    return trimmed_path

                # Process clip: normalize + color grade + trim
                assembler.process_clip(
                    download_path, trimmed_path,
                    duration_sec=clip_duration,
                    color_grade_filter=color_grade_filter,
                    orientation=orientation
                )

                print(f"  Clip {i+1}/{num_clips} done")
                return trimmed_path

            with ThreadPoolExecutor(max_workers=4) as clip_pool:
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
            from app.media.clip_picker import ClipReservations, find_candidates, media_key
            # Clips already used in this video: no shot repeats another's footage.
            used_clips = ClipReservations()
            clip_topic = prompt or (script_text or "")[:160]
            candidates_count = int(os.getenv("CLIP_CANDIDATES", "5"))

            clip_results = [None] * num_clips

            def _download_candidate(args):
                vid_info, dl_path = args
                searcher.download(vid_info, dl_path)
                return {'path': dl_path, 'info': vid_info}

            def _reserve_first(infos):
                """First of ``infos`` not yet used in this video (reused only if all are)."""
                for info in infos:
                    if used_clips.try_reserve(media_key(info)):
                        return info
                print("    Warning: every candidate is already used in this video; reusing one")
                used_clips.reserve(media_key(infos[0]))
                return infos[0]

            def _process_clip(i, clip):
                clip_dir = temp_dir / f"clip_{i}"
                clip_dir.mkdir(exist_ok=True)

                print(f"\n  Clip {i+1}/{num_clips}: Searching for {clip.keywords}...")
                # Unused candidates from the first rung of the broadening ladder
                # (all keywords -> page 2 -> fewer keywords -> description/topic
                # nouns -> Pixabay video/image) that has any.
                candidates, rung = find_candidates(
                    searcher, clip, orientation,
                    min_duration=clip_duration,
                    reservations=used_clips,
                    topic=clip_topic,
                    limit=candidates_count,
                    label=f"{i+1}/{num_clips}",
                )
                if rung and rung != "full_keywords":
                    print(f"    Broadened search: found footage on rung '{rung}'")

                if not candidates:
                    raise ValueError(f"No videos found for clip {i} keywords: {clip.keywords}")

                if getattr(candidates[0], 'media_type', 'video') == 'image':
                    # Image fallback: still image, converted with Ken Burns
                    from video_assembler import KEN_BURNS_EFFECTS
                    best_image = _reserve_first(candidates)
                    image_ext = '.jpg' if 'jpeg' in best_image.download_url.lower() or 'jpg' in best_image.download_url.lower() else '.png'
                    image_path = clip_dir / f"fallback_image{image_ext}"
                    searcher.download_image(best_image, image_path)

                    effect = KEN_BURNS_EFFECTS[i % len(KEN_BURNS_EFFECTS)]
                    trimmed_path = clip_dir / "trimmed.mp4"
                    assembler.image_to_video(
                        image_path, trimmed_path,
                        duration_sec=clip_duration,
                        orientation=orientation,
                        effect=effect,
                        color_grade_filter=color_grade_filter,
                    )

                    with clip_lock:
                        tracker.add_asset(best_image.to_dict(), i)

                    print(f"  Clip {i+1}/{num_clips} done (image fallback, {effect})")
                    return trimmed_path

                # Prefer clips at least as long as the slot so short clips
                # don't make the video shorter than the narration
                long_enough = [v for v in candidates if (getattr(v, 'duration', 0) or 0) >= clip_duration]
                available_videos = long_enough or candidates

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
                scores = matcher.score_videos(video_paths, score_text) or {}

                if scores:
                    ranked_paths = sorted(video_paths, key=lambda p: scores.get(p, float('-inf')), reverse=True)
                else:
                    # Every candidate failed embedding: keep download order
                    print("    Warning: CLIP scoring failed for all candidates, using first download")
                    ranked_paths = video_paths
                info_by_path = {d['path']: d['info'] for d in downloaded}

                # Best-scoring clip that no other shot has claimed meanwhile
                best_info = _reserve_first([info_by_path[p] for p in ranked_paths])
                best_path = next(p for p in ranked_paths if info_by_path[p] is best_info)
                best_score = scores.get(best_path, 0.0) if scores else 0.0

                print(f"    Best match: score={best_score:.3f}")

                # Track asset (thread-safe)
                with clip_lock:
                    tracker.add_asset(best_info.to_dict(), i)

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

            with ThreadPoolExecutor(max_workers=4) as clip_pool:
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
        outro_pad = 0.0
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
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
                trimmed_videos[-1] = extended_path
                # Pad the audio by the same amount so -shortest keeps the outro
                outro_pad = outro_duration

        # Step 4: Assemble final video
        _step_times['3_clips'] = time.time() - _t0
        _t0 = time.time()
        print("\n[4/5] Assembling final video...")
        _report(75, "Assembling final video...")

        transition_type = os.getenv("TRANSITION_TYPE", "crossfade")

        # Add background music if enabled
        music_file = None
        final_audio = narration_audio
        if os.getenv("ENABLE_BACKGROUND_MUSIC", "true").lower() == "true":
            print("  Mixing background music...")

            from music_selector import MusicSelector
            from audio_mixer import AudioMixer

            selector = MusicSelector(music_dir=Path(os.getenv("MUSIC_DIR", "music")))
            music_mood = os.getenv("MUSIC_MOOD", None)

            # prompt is None in CLI --script mode; fall back to the narration
            music_query = prompt or plan.narration or ""
            try:
                music_file = selector.select_music(music_query, mood=music_mood)
                print(f"    Selected music: {music_file.name} (mood: {music_mood or selector._detect_mood(music_query)})")

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
            except Exception as e:
                # Music is optional: never let it kill a render
                music_file = None
                final_audio = narration_audio
                print(f"    Warning: background music failed: {e}")
                print(f"    Continuing without background music")

        # Prepare subscribe overlay if enabled
        needs_overlay = os.getenv("ENABLE_SUBSCRIBE_OVERLAY", "false").lower() == "true"
        needs_subtitles = (subtitles_enabled if subtitles_enabled is not None
                           else os.getenv("ENABLE_SUBTITLES", "true").lower() == "true")

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
        sub_gen = None
        if needs_subtitles:
            print("  Generating subtitles...")

            sub_gen = SubtitleGenerator(
                style=subtitle_style or os.getenv("SUBTITLE_STYLE", "youtube"),
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

        # Single-pass: concat clips + mux audio + subtitles + fade (no intermediate encode)
        from effects_engine import EffectsEngine
        finalizer = EffectsEngine(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe")
        )

        effects_summary = ["xfade transitions"]
        if subscribe_video_path:
            effects_summary.append("overlay")
        if subtitle_file:
            effects_summary.append("subtitles")
        if fade_dur > 0:
            effects_summary.append(f"fade-out ({fade_dur}s)")

        print(f"  Assembling {len(trimmed_videos)} clips ({' + '.join(effects_summary)}) in single pass...")

        finalizer.concat_and_finalize(
            clips=trimmed_videos,
            audio_path=final_audio,
            output_path=output_path,
            transition=transition_type,
            transition_duration=transition_duration,
            subtitle_path=subtitle_file,
            subscribe_video=subscribe_video_path,
            subscribe_params=subscribe_params,
            fade_duration=fade_dur,
            audio_pad_duration=outro_pad,
        )

        # Save intermediate data for subtitle re-generation and editor
        if intermediate_callback:
            intermediate_callback({
                'temp_dir': str(temp_dir),
                'final_audio': str(final_audio),
                'narration_audio': str(narration_audio),
                'music_file': str(music_file) if music_file else None,
                'word_timestamps': word_timestamps,
                'trimmed_clips': [str(p) for p in original_trimmed_videos],
                'clip_duration': clip_duration,
                'transition_duration': transition_duration,
                # What the render above actually used, so the editor can
                # rebuild the same video (loop copy, freeze-frame outro,
                # transition type, audio padding and fade timing).
                'render': {
                    'final_clips': [str(p) for p in trimmed_videos],
                    'transition_type': transition_type,
                    'outro_duration': outro_pad,
                    'loop_mode': bool(loop_mode),
                    'fade_duration': fade_dur,
                    'subtitle_style': sub_gen.style_name if sub_gen else None,
                    'subtitle_overrides': dict(sub_gen.custom_overrides) if sub_gen else None,
                    'subtitle_settings': sub_gen.resolved_settings() if sub_gen else None,
                },
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


# ── Batch mode (--batch FILE) ──────────────────────────────────


def parse_batch_file(text: str) -> list:
    """Parse a batch file into [{"input_type": "prompt"|"script", "text": str}].

    If any line is exactly ``---`` the file holds full scripts separated by
    ``---`` lines. Otherwise it holds one topic per line; blank lines and
    lines starting with ``#`` are ignored.
    """
    lines = (text or "").splitlines()
    if any(line.strip() == "---" for line in lines):
        entries, current = [], []
        for line in lines + ["---"]:
            if line.strip() == "---":
                script = "\n".join(current).strip()
                if script:
                    entries.append({"input_type": "script", "text": script})
                current = []
            else:
                current.append(line)
        return entries

    entries = []
    for line in lines:
        topic = line.strip()
        if topic and not topic.startswith("#"):
            entries.append({"input_type": "prompt", "text": topic})
    return entries


def batch_output_path(output_dir: Path, index: int, total: int, title: str) -> Path:
    """``<dir>/<NN>-<sanitized title>.mp4`` (1-based, zero-padded)."""
    from utils import sanitize_filename
    width = max(2, len(str(total)))
    return Path(output_dir) / f"{index:0{width}d}-{sanitize_filename(title, max_length=80)}.mp4"


def _cli_words_per_second() -> float:
    """Speaking rate used to estimate a script's duration (mirrors the TTS engine)."""
    wps_override = os.getenv("WORDS_PER_SECOND", "").strip()
    if wps_override:
        return float(wps_override)
    tts_engine = os.getenv("TTS_ENGINE", "kokoro").lower()
    if tts_engine == "kokoro":
        return 3.0 * float(os.getenv("KOKORO_SPEED", "1.0"))
    if tts_engine == "chatterbox":
        return 2.5
    return 3.0


def run_batch(entries: list, output_dir: Path, duration_minutes, orientation: str,
              temp_dir: Path = None, keep_temp: bool = False, render=None) -> list:
    """Render batch entries one at a time; a failure is reported and the run continues.

    Returns one result dict per entry: {index, title, status, output, error, seconds}.
    """
    from app.utils.helpers import derive_video_title
    from script_loader import estimate_duration_from_script

    render = render or generate_video
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(entries)
    wps = _cli_words_per_second()
    results = []

    for index, entry in enumerate(entries, start=1):
        title = derive_video_title(entry["input_type"], entry["text"])
        out_path = batch_output_path(output_dir, index, total, title)
        print(f"\n{'#' * 70}\n[{index}/{total}] {title}\n{'#' * 70}")
        started = time.time()
        try:
            if entry["input_type"] == "script":
                minutes = estimate_duration_from_script(entry["text"], wps)
                prompt, script_text = None, entry["text"]
            else:
                minutes = duration_minutes
                prompt, script_text = entry["text"], None
            render(
                prompt=prompt,
                duration_minutes=minutes,
                output_path=out_path,
                orientation=orientation,
                temp_dir=(Path(temp_dir) / f"{index:02d}") if temp_dir else None,
                keep_temp=keep_temp,
                script_text=script_text,
            )
            results.append({"index": index, "title": title, "status": "done",
                            "output": str(out_path), "error": None,
                            "seconds": time.time() - started})
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"\nERROR in item {index}: {e}")
            import traceback
            traceback.print_exc()
            results.append({"index": index, "title": title, "status": "failed",
                            "output": None, "error": str(e).splitlines()[0] if str(e) else type(e).__name__,
                            "seconds": time.time() - started})
    return results


def print_batch_summary(results: list) -> None:
    print(f"\n{'=' * 70}\nBATCH SUMMARY\n{'=' * 70}")
    print(f"{'#':>3}  {'Status':<7} {'Time':>7}  Output / error")
    for r in results:
        detail = r["output"] if r["status"] == "done" else r["error"]
        print(f"{r['index']:>3}  {r['status']:<7} {r['seconds']:>6.0f}s  {detail}")
    failed = sum(1 for r in results if r["status"] != "done")
    print(f"{'-' * 70}\n{len(results) - failed} rendered, {failed} failed\n{'=' * 70}")


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

    input_group.add_argument(
        "--batch",
        type=str,
        metavar="FILE",
        help="Render many videos one at a time: FILE holds one topic per line, or full "
             "scripts separated by lines containing only '---'. --output is then a directory."
    )

    parser.add_argument(
        "--duration",
        type=float,
        required=False,
        help="Video duration in minutes (required for --prompt and for topics in --batch; "
             "optional for --script - auto-calculated from word count)"
    )

    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output video file path (e.g., video.mp4), or output directory with --batch"
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
        words_per_second = _cli_words_per_second()

        # Estimate duration if not provided
        if duration_minutes is None:
            duration_minutes = estimate_duration_from_script(script_text, words_per_second)
            print(f"Estimated duration: {duration_minutes:.2f} minutes ({len(script_text.split())} words at {words_per_second:.1f} wps)")
        else:
            print(f"Using user-specified duration: {duration_minutes} minutes")

    elif args.batch:
        try:
            batch_entries = parse_batch_file(Path(args.batch).read_text(encoding="utf-8"))
        except OSError as e:
            print(f"Error reading batch file: {e}")
            return 1
        if not batch_entries:
            print(f"Error: no topics or scripts found in {args.batch}")
            return 1
        if duration_minutes is None and any(e["input_type"] == "prompt" for e in batch_entries):
            print("Error: --duration is required when the batch file contains topics")
            return 1
        if Path(args.output).suffix.lower() == ".mp4":
            print("Error: with --batch, --output must be a directory (e.g. out/)")
            return 1
        kind = "scripts" if batch_entries[0]["input_type"] == "script" else "topics"
        print(f"Loaded {len(batch_entries)} {kind} from {args.batch}")

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
    elif llm_provider == "anthropic":
        required_vars.append("ANTHROPIC_API_KEY")

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

    if args.batch:
        results = run_batch(
            batch_entries,
            output_dir=Path(args.output),
            duration_minutes=duration_minutes,
            orientation=orientation,
            temp_dir=Path(args.temp_dir) if args.temp_dir else None,
            keep_temp=args.keep_temp,
        )
        print_batch_summary(results)
        return 1 if any(r["status"] != "done" for r in results) else 0

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
