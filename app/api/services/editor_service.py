"""Editor service — loads project data, computes timeline entries, renders changes."""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings


class EditorService:
    """Business logic for the video editor."""

    def load_project(self, project_dir: Path) -> dict:
        """Load and parse project.json from a project directory."""
        project_json = project_dir / "project.json"
        if not project_json.exists():
            raise FileNotFoundError(f"project.json not found in {project_dir}")
        with open(project_json, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_project(self, project_dir: Path, project: dict) -> None:
        """Write project.json back to disk."""
        project_json = project_dir / "project.json"
        with open(project_json, "w", encoding="utf-8") as f:
            json.dump(project, f, indent=2, ensure_ascii=False)

    def _ensure_video_no_audio(self, project_dir: Path, project: dict) -> Path:
        """Generate video_no_audio.mp4 on-demand from stored trimmed clips.

        Called when the editor or subtitle re-generation needs the intermediate
        video file but it doesn't exist (e.g., after single-pass generation).
        """
        video_no_audio = project_dir / "video_no_audio.mp4"
        if video_no_audio.exists():
            return video_no_audio

        from video_assembler import VideoAssembler

        clips = project.get("clips", [])
        clip_paths = []
        for c in clips:
            clip_file = c.get("file")
            if clip_file:
                p = project_dir / clip_file
                if p.exists():
                    clip_paths.append(p)

        if not clip_paths:
            raise ValueError("No clip files available to generate video_no_audio")

        transition_duration = project.get(
            "transition_duration",
            float(os.getenv("TRANSITION_DURATION", "0.5")),
        )

        assembler = VideoAssembler(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
        )
        assembler.concat_videos_with_transitions(
            clip_paths, video_no_audio,
            transition="crossfade",
            transition_duration=transition_duration,
        )

        # Update project manifest
        project.setdefault("output", {})["video_no_audio"] = "video_no_audio.mp4"
        self.save_project(project_dir, project)

        return video_no_audio

    def generate_preview(self, project_dir: Path, project: dict) -> Path | None:
        """Generate a subtitle-free preview video by muxing video + audio (stream copy).

        Returns path to the preview file, or None if source files are missing.
        Skips regeneration if preview is already up-to-date.
        """
        import subprocess

        preview_path = project_dir / "video_preview.mp4"
        video_no_audio_file = project.get("output", {}).get("video_no_audio")
        audio_file = (project.get("audio", {}).get("mixed_file")
                      or project.get("audio", {}).get("narration_file"))

        if not audio_file:
            return None

        audio_src = project_dir / audio_file
        if not audio_src.exists():
            return None

        # Generate video_no_audio on-demand if it doesn't exist
        if not video_no_audio_file or not (project_dir / video_no_audio_file).exists():
            try:
                self._ensure_video_no_audio(project_dir, project)
                video_no_audio_file = "video_no_audio.mp4"
            except (ValueError, Exception):
                return None

        video_src = project_dir / video_no_audio_file
        if not video_src.exists():
            return None

        # Skip regeneration if preview is newer than both sources
        if preview_path.exists():
            preview_mtime = preview_path.stat().st_mtime
            if (preview_mtime > video_src.stat().st_mtime
                    and preview_mtime > audio_src.stat().st_mtime):
                return preview_path

        ffmpeg = os.getenv("FFMPEG_PATH", "ffmpeg")
        cmd = [
            ffmpeg,
            "-i", str(video_src),
            "-i", str(audio_src),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v", "-map", "1:a",
            "-shortest",
            "-movflags", "+faststart",
            "-y", str(preview_path),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            return preview_path
        except subprocess.CalledProcessError:
            return None

    def get_editor_data(self, project_dir: Path, project: dict, stream_url: str,
                        session_id: str = None, video_id: int = None,
                        preview_stream_url: str = None) -> dict:
        """Build the full editor data response from a project manifest."""
        orientation = project.get("orientation", "landscape")
        duration = project.get("duration_seconds", 0)
        transition_duration = project.get("transition_duration", float(os.getenv("TRANSITION_DURATION", "0.5")))

        # Compute clip start/end times accounting for crossfade overlap
        clips_data = project.get("clips", [])
        clips = []
        current_time = 0.0
        for i, c in enumerate(clips_data):
            clip_dur = c.get("duration_seconds") or 0
            # For clips in the concatenated video, crossfade overlaps reduce effective duration
            if i > 0:
                current_time -= transition_duration
            start = max(0, current_time)
            end = start + clip_dur
            clips.append({
                "index": c.get("index", i),
                "keywords": c.get("keywords", []),
                "visual_description": c.get("visual_description", ""),
                "video_id": c.get("video_id"),
                "author": c.get("author"),
                "duration_seconds": clip_dur,
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "trim_start": c.get("trim_start", 0),
                "trim_end": c.get("trim_end", 0),
            })
            current_time = end

        # Compute subtitle entries from word timestamps
        word_timestamps = project.get("word_timestamps")
        if not word_timestamps and project.get("script_text"):
            word_timestamps = self._generate_heuristic_timestamps(project_dir, project)
            if word_timestamps:
                project["word_timestamps"] = word_timestamps
                self.save_project(project_dir, project)
        subtitle_entries = self.compute_subtitle_entries(word_timestamps, orientation) if word_timestamps else []

        # Audio info
        audio_data = project.get("audio", {})
        audio = {
            "narration_volume": audio_data.get("narration_volume", 1.0),
            "music_volume": audio_data.get("music_volume", 0.15),
            "has_background_music": audio_data.get("has_background_music", False),
        }

        # Subtitle settings
        sub_data = project.get("subtitles", {})
        subtitles = {
            "enabled": sub_data.get("enabled", True),
            "style": sub_data.get("style", "youtube"),
            "font": sub_data.get("font", "Arial"),
            "font_size": sub_data.get("font_size", 52),
            "color": sub_data.get("color", "#FFFFFF"),
            "outline_color": sub_data.get("outline_color", "#000000"),
            "position": sub_data.get("position", "bottom"),
            "entries": subtitle_entries,
        }

        return {
            "session_id": session_id,
            "video_id": video_id,
            "title": project.get("title", "Untitled"),
            "orientation": orientation,
            "duration_seconds": duration,
            "stream_url": stream_url,
            "preview_stream_url": preview_stream_url,
            "script_text": project.get("script_text"),
            "clips": clips,
            "audio": audio,
            "subtitles": subtitles,
            "word_timestamps": word_timestamps,
        }

    def compute_subtitle_entries(self, word_timestamps: List[Dict], orientation: str) -> List[Dict]:
        """Convert word timestamps into subtitle entries (phrase groups) for the timeline.

        Groups 2-4 words at natural speech boundaries, similar to SubtitleGenerator logic.
        """
        if not word_timestamps:
            return []

        entries = []
        current_words = []
        current_start = None
        max_words = 3 if orientation in ("portrait", "square") else 4
        max_duration = 1.5
        entry_index = 0

        for wt in word_timestamps:
            word = wt.get("word", "")
            start = wt.get("start", 0)
            end = wt.get("end", 0)

            if current_start is None:
                current_start = start

            current_words.append(word)

            # Break conditions
            should_break = False
            if len(current_words) >= max_words:
                should_break = True
            elif end - current_start >= max_duration:
                should_break = True
            elif word.rstrip().endswith((".", "!", "?", ",", ";", ":")):
                should_break = True

            if should_break:
                entries.append({
                    "index": entry_index,
                    "text": " ".join(current_words),
                    "start_time": round(current_start, 3),
                    "end_time": round(end, 3),
                })
                entry_index += 1
                current_words = []
                current_start = None

        # Remaining words
        if current_words:
            last_end = word_timestamps[-1].get("end", 0)
            entries.append({
                "index": entry_index,
                "text": " ".join(current_words),
                "start_time": round(current_start, 3),
                "end_time": round(last_end, 3),
            })

        return entries

    def render_changes(self, project_dir: Path, project: dict,
                       subtitles: dict = None, audio: dict = None,
                       replaced_clips: list = None, output_path: Path = None) -> str:
        """Render video with editor changes. Returns render type description.

        Only re-processes what changed:
          - subtitles only: regenerate ASS + finalize (~5-10s)
          - audio only: re-mix audio + finalize (~10-20s)
          - clip replacement: re-download + re-concat + finalize (~30-60s)
        """
        from subtitle_generator import SubtitleGenerator
        from effects_engine import EffectsEngine

        orientation = project.get("orientation", "landscape")

        video_no_audio_file = project.get("output", {}).get("video_no_audio")
        audio_mixed_file = project.get("audio", {}).get("mixed_file")
        audio_narration_file = project.get("audio", {}).get("narration_file")

        video_no_audio = project_dir / video_no_audio_file if video_no_audio_file else None
        audio_path = project_dir / audio_mixed_file if audio_mixed_file else None
        narration_path = project_dir / audio_narration_file if audio_narration_file else None

        # Generate video_no_audio on-demand if it doesn't exist
        if not video_no_audio or not video_no_audio.exists():
            video_no_audio = self._ensure_video_no_audio(project_dir, project)
        if not audio_path or not audio_path.exists():
            # Fall back to narration-only audio
            if narration_path and narration_path.exists():
                audio_path = narration_path
            else:
                raise ValueError("Audio source file not found in project")

        render_type_parts = []

        # Step 1: Handle clip replacements (most expensive)
        if replaced_clips:
            video_no_audio = self._reprocess_clips(project_dir, project, replaced_clips, orientation)
            render_type_parts.append("clips")

        # Step 2: Handle audio changes
        if audio:
            new_narration_vol = audio.get("narration_volume", 1.0)
            new_music_vol = audio.get("music_volume", 0.15)
            old_narration_vol = project.get("audio", {}).get("narration_volume", 1.0)
            old_music_vol = project.get("audio", {}).get("music_volume", 0.15)

            if new_narration_vol != old_narration_vol or new_music_vol != old_music_vol:
                audio_path = self._remix_audio(project_dir, project, new_narration_vol, new_music_vol)
                # Update project
                project.setdefault("audio", {})["narration_volume"] = new_narration_vol
                project["audio"]["music_volume"] = new_music_vol
                project["audio"]["mixed_file"] = "audio_final.wav"
                render_type_parts.append("audio")

        # Step 3: Handle subtitle changes
        subtitle_path = None
        if subtitles:
            if subtitles.get("enabled", True):
                subtitle_path = self._regenerate_subtitles(
                    project_dir, project, subtitles, orientation
                )
                # Update project subtitle settings
                project["subtitles"] = {
                    "enabled": True,
                    "style": subtitles.get("style", "youtube"),
                    "font": subtitles.get("font", "Arial"),
                    "font_size": subtitles.get("font_size", 52),
                    "color": subtitles.get("color", "#FFFFFF"),
                    "outline_color": subtitles.get("outline_color", "#000000"),
                    "position": subtitles.get("position", "bottom"),
                    "file": "subtitles.ass",
                }
            else:
                subtitle_path = None
                project["subtitles"]["enabled"] = False
            render_type_parts.append("subtitles")
        else:
            # Use existing subtitles
            sub_file = project.get("subtitles", {}).get("file")
            if sub_file and project.get("subtitles", {}).get("enabled", True):
                sub_path = project_dir / sub_file
                if sub_path.exists():
                    subtitle_path = sub_path

        # Step 4: Finalize
        finalizer = EffectsEngine(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
        )

        loop_mode = project.get("loop_mode", False)
        fade_dur = 0 if loop_mode else float(os.getenv("FADE_OUT_DURATION", "1.0"))

        # Check for subscribe overlay
        subscribe_video = None
        subscribe_params = None
        if os.getenv("ENABLE_SUBSCRIBE_OVERLAY", "false").lower() == "true":
            sub_path = Path(os.getenv("SUBSCRIBE_VIDEO_PATH", "subscribe.mp4"))
            if not sub_path.is_absolute():
                sub_path = Path(settings.base_dir) / sub_path
            if sub_path.exists():
                subscribe_video = sub_path
                subscribe_params = {
                    "orientation": orientation,
                    "scale": float(os.getenv("SUBSCRIBE_SCALE", "0")) or None,
                    "margin_bottom": int(os.getenv("SUBSCRIBE_MARGIN_BOTTOM", "50")),
                    "chromakey_color": os.getenv("SUBSCRIBE_CHROMAKEY_COLOR", "0x00FF00"),
                    "chromakey_similarity": float(os.getenv("SUBSCRIBE_CHROMAKEY_SIMILARITY", "0.3")),
                    "chromakey_blend": float(os.getenv("SUBSCRIBE_CHROMAKEY_BLEND", "0.15")),
                }

        finalizer.finalize_video(
            video_no_audio=video_no_audio,
            audio_path=audio_path,
            output_path=output_path,
            subtitle_path=subtitle_path,
            subscribe_video=subscribe_video,
            subscribe_params=subscribe_params,
            fade_duration=fade_dur,
        )

        # Update file size in DB
        self._update_video_file_size(project, output_path)

        # Save updated project
        self.save_project(project_dir, project)

        # Regenerate subtitle-free preview for editor
        self.generate_preview(project_dir, project)

        render_type = " + ".join(render_type_parts) if render_type_parts else "finalize"
        return render_type

    def _regenerate_subtitles(self, project_dir: Path, project: dict,
                              sub_settings: dict, orientation: str) -> Optional[Path]:
        """Generate new ASS subtitle file from word timestamps + settings."""
        from subtitle_generator import SubtitleGenerator

        word_timestamps = project.get("word_timestamps")
        if not word_timestamps and project.get("script_text"):
            word_timestamps = self._generate_heuristic_timestamps(project_dir, project)
            if word_timestamps:
                project["word_timestamps"] = word_timestamps
        if not word_timestamps:
            return None

        custom_overrides = {}
        for key in ("font", "font_size", "color", "outline_color", "position"):
            if sub_settings.get(key):
                custom_overrides[key] = sub_settings[key]

        style_preset = sub_settings.get("style", "youtube")

        sub_gen = SubtitleGenerator(
            style=style_preset,
            orientation=orientation,
            custom_overrides=custom_overrides if custom_overrides else None,
        )

        subtitle_file = project_dir / "subtitles.ass"

        # Check if custom subtitle entries were provided (user edited text/timing)
        custom_entries = sub_settings.get("entries")
        if custom_entries:
            # Build word timestamps from edited entries
            edited_timestamps = self._entries_to_word_timestamps(custom_entries, word_timestamps)
            if orientation in ("portrait", "square"):
                sub_gen.generate_karaoke_ass(edited_timestamps, subtitle_file)
            else:
                sub_gen.generate_phrase_ass(edited_timestamps, subtitle_file)
            # Update project word timestamps with edits
            project["word_timestamps"] = edited_timestamps
        else:
            if orientation in ("portrait", "square"):
                sub_gen.generate_karaoke_ass(word_timestamps, subtitle_file)
            else:
                sub_gen.generate_phrase_ass(word_timestamps, subtitle_file)

        return subtitle_file

    def _generate_heuristic_timestamps(self, project_dir: Path, project: dict) -> list:
        """Generate heuristic word timestamps from script text + narration audio duration.

        Used as a fallback when ASR and provider timestamps are both empty.
        """
        script_text = project.get("script_text", "").strip()
        if not script_text:
            return []

        words = script_text.split()
        if not words:
            return []

        # Get narration duration from audio file
        narration_file = (project.get("audio") or {}).get("narration_file")
        if not narration_file:
            return []
        narration_path = project_dir / narration_file
        if not narration_path.exists():
            return []

        import subprocess
        ffprobe = os.getenv("FFPROBE_PATH", "ffprobe")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(narration_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        try:
            total_duration = float(result.stdout.strip())
        except (ValueError, TypeError):
            return []

        # Use syllable-based weighting from TTSGenerator
        from tts_generator import TTSGenerator
        total_weight = sum(TTSGenerator._get_word_weight(w) for w in words)
        if total_weight <= 0:
            return []

        subtitle_offset = 0.15
        t = subtitle_offset
        timestamps = []
        for word in words:
            weight = TTSGenerator._get_word_weight(word)
            word_dur = total_duration * (weight / total_weight)
            timestamps.append({
                "word": word,
                "start": round(t, 3),
                "end": round(t + word_dur, 3),
            })
            t += word_dur

        return timestamps

    def _entries_to_word_timestamps(self, entries: list, original_timestamps: list) -> list:
        """Convert edited subtitle entries back to word-level timestamps.

        Distributes each entry's time span evenly across its words.
        """
        new_timestamps = []
        for entry in entries:
            text = entry.get("text", "")
            start = entry.get("start_time", 0)
            end = entry.get("end_time", 0)
            words = text.split()
            if not words:
                continue
            word_dur = (end - start) / len(words) if len(words) > 0 else 0
            for i, word in enumerate(words):
                new_timestamps.append({
                    "word": word,
                    "start": round(start + i * word_dur, 3),
                    "end": round(start + (i + 1) * word_dur, 3),
                })
        return new_timestamps

    def _remix_audio(self, project_dir: Path, project: dict,
                     narration_volume: float, music_volume: float) -> Path:
        """Re-mix narration + background music with new volumes."""
        narration_file = project.get("audio", {}).get("narration_file")
        if not narration_file:
            raise ValueError("Narration audio file not found in project")

        narration_path = project_dir / narration_file
        if not narration_path.exists():
            raise ValueError("Narration audio file not found on disk")

        import subprocess

        ffmpeg = os.getenv("FFMPEG_PATH", "ffmpeg")
        output = project_dir / "audio_final.wav"

        if project.get("audio", {}).get("has_background_music"):
            # Use stored music file from project directory
            music_file_ref = project.get("audio", {}).get("music_file")
            music_path = project_dir / music_file_ref if music_file_ref else None

            # Fallback for legacy projects: try to re-select music
            if not music_path or not music_path.exists():
                import logging
                logging.warning("Music file not found in project, attempting re-selection")
                try:
                    from music_selector import MusicSelector
                    selector = MusicSelector(music_dir=Path(os.getenv("MUSIC_DIR", "music")))
                    prompt = project.get("prompt", project.get("title", ""))
                    music_path = selector.select_music(prompt)
                except Exception as e:
                    logging.warning(f"Could not find background music: {e}")
                    music_path = None

            if music_path and music_path.exists():
                from audio_mixer import AudioMixer
                mixer = AudioMixer(ffmpeg_path=ffmpeg, ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"))

                mixer.mix_with_background_music(
                    narration_path, music_path, output,
                    music_volume=music_volume,
                    narration_volume=narration_volume,
                )

                return output

        # No background music or music not available — just adjust narration volume
        if narration_volume != 1.0:
            subprocess.run([
                ffmpeg, "-i", str(narration_path),
                "-af", f"volume={narration_volume}",
                "-y", str(output)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        else:
            shutil.copy2(narration_path, output)

        return output

    def _reprocess_clips(self, project_dir: Path, project: dict,
                         replaced_clips: list, orientation: str) -> Path:
        """Replace specified clips and re-concatenate all clips."""
        from video_assembler import VideoAssembler
        from app.media import VideoSearcher

        assembler = VideoAssembler(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
        )

        clips_dir = project_dir / "clips"
        all_clips = project.get("clips", [])
        transition_duration = project.get("transition_duration", float(os.getenv("TRANSITION_DURATION", "0.5")))

        # Build list of clip paths
        clip_paths = []
        for c in all_clips:
            clip_file = c.get("file")
            if clip_file:
                clip_paths.append(project_dir / clip_file)
            else:
                clip_paths.append(None)

        # Replace specified clips
        for replacement in replaced_clips:
            idx = replacement.get("index", 0)
            keywords = replacement.get("keywords", [])
            specific_video_id = replacement.get("video_id")
            trim_start = replacement.get("trim_start", 0)
            trim_end = replacement.get("trim_end", 0)

            if idx >= len(all_clips):
                continue

            clip_data = all_clips[idx]
            clip_dur = clip_data.get("duration_seconds", 5)

            # Check if this is a trim-only change (no replacement)
            is_replace = specific_video_id and specific_video_id != clip_data.get("video_id")

            if is_replace:
                searcher = VideoSearcher(api_key=settings.pexels_api_key)
                best = None

                if specific_video_id:
                    best = searcher.get_video_by_id(str(specific_video_id))

                if not best:
                    videos = searcher.search(keywords, max_results=5, orientation=orientation)
                    if not videos:
                        continue
                    best = videos[0]

                # Download, normalize, and trim
                download_dir = project_dir / "temp_download"
                download_dir.mkdir(exist_ok=True)
                downloaded = searcher.download(
                    best,
                    download_dir / f"clip_{idx}_{best.id or 'selected'}.mp4",
                )

                if downloaded:
                    normalized = clips_dir / f"clip_{idx}_norm.mp4"
                    assembler.normalize_video(downloaded, normalized, orientation=orientation)
                    dest = clips_dir / f"clip_{idx}.mp4"
                    effective_duration = max(0.5, clip_dur - trim_start - trim_end)
                    assembler.trim_video(normalized, dest,
                                         start_sec=trim_start,
                                         duration_sec=effective_duration)
                    # Clean up normalized temp
                    if normalized.exists() and normalized != dest:
                        normalized.unlink(missing_ok=True)
                    clip_paths[idx] = dest

                    # Update clip metadata
                    all_clips[idx]["keywords"] = keywords
                    all_clips[idx]["video_id"] = str(best.id)
                    all_clips[idx]["author"] = best.author
                    all_clips[idx]["file"] = f"clips/clip_{idx}.mp4"
                    all_clips[idx]["trim_start"] = trim_start
                    all_clips[idx]["trim_end"] = trim_end

                # Cleanup download dir
                shutil.rmtree(download_dir, ignore_errors=True)
            else:
                # Trim-only: re-trim existing source clip
                clip_file = clip_data.get("file")
                if clip_file:
                    source_clip = project_dir / clip_file
                    if source_clip.exists():
                        dest = clips_dir / f"clip_{idx}_trimmed.mp4"
                        effective_duration = max(0.5, clip_dur - trim_start - trim_end)
                        assembler.trim_video(source_clip, dest,
                                             start_sec=trim_start,
                                             duration_sec=effective_duration)
                        clip_paths[idx] = dest
                        all_clips[idx]["trim_start"] = trim_start
                        all_clips[idx]["trim_end"] = trim_end

        # Re-concatenate all clips
        valid_clips = [p for p in clip_paths if p and p.exists()]
        if not valid_clips:
            raise ValueError("No valid clips available for concatenation")

        video_no_audio = project_dir / "video_no_audio.mp4"
        assembler.concat_videos_with_transitions(
            valid_clips, video_no_audio,
            transition="crossfade",
            transition_duration=transition_duration,
        )

        project["clips"] = all_clips
        return video_no_audio

    def search_clips(self, keywords: list, orientation: str, count: int = 6) -> list:
        """Search Pexels for replacement clip candidates."""
        from app.media import VideoSearcher

        searcher = VideoSearcher(api_key=settings.pexels_api_key)
        videos = searcher.search(keywords, max_results=count, orientation=orientation)

        results = []
        for v in videos:
            results.append({
                "video_id": str(v.id),
                "preview_url": v.download_url or v.url,
                "author": v.author or "",
                "duration_seconds": v.duration or 0,
                "width": v.width or 0,
                "height": v.height or 0,
            })

        return results

    def _update_video_file_size(self, project: dict, output_path: Path):
        """Update the video's file size in the database after re-render."""
        video_id = project.get("video_id")
        if not video_id or not output_path.exists():
            return

        from app.db.base import SessionLocal
        from app.db.models import Video

        new_size = output_path.stat().st_size
        db = SessionLocal()
        try:
            video = db.query(Video).filter(Video.id == video_id).first()
            if video:
                video.file_size_bytes = new_size
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# Global service instance
editor_service = EditorService()
