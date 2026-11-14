"""Editor service — loads project data, computes timeline entries, renders changes.

project.json clip model (one entry per shot):

    source_file   untouched footage for the shot (never overwritten)
    trimmed_file  derived from source_file + trim_start/trim_end (+ pad), or None
    trim_start / trim_end   seconds cut from the start / end of source_file
    pad_seconds   freeze-frame seconds added so the video still covers the narration
    loop_copy     True for the last shot in loop mode (it repeats shot 0)

Project-level render settings (what the first render used, reused by every
re-render): transition_type, transition_duration, outro_duration (freeze-frame
outro on the last shot, mirrored by audio padding), fade_duration, loop_mode,
and render_clips (the exact clip list concatenated into video_no_audio.mp4).
"""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bump when the way video_no_audio.mp4 is built changes, so stale builds are redone.
VIDEO_NO_AUDIO_REV = 2

# Shortest stretch of footage a trim may leave (matches the timeline UI)
MIN_CLIP_SECONDS = 0.5

# Deficits smaller than this (frame rounding) are not padded
PAD_TOLERANCE = 0.1

# Values the old project writer stored when the session had no explicit choice
_LEGACY_SUBTITLE_DEFAULTS = {
    "font": "Arial", "font_size": 52, "color": "#FFFFFF",
    "outline_color": "#000000", "position": "bottom",
}

SUBTITLE_OVERRIDE_KEYS = ("font", "font_size", "color", "outline_color", "position")
SUBTITLE_PRESETS = ("youtube", "tiktok", "minimal")


def video_edit_lock_key(output_path) -> str:
    """Lock key shared by every operation that rewrites a video's output mp4.

    The editor render (session and library routes), subtitle regeneration and
    the editor's preview / project.json rebuilds must use the same key, or
    they can overwrite the same files concurrently. The output filename is the
    one identifier all of them can derive.
    """
    return f"video_output:{Path(output_path).name}"


class EditorValidationError(ValueError):
    """An edit request that can't be applied (shown to the user as a 400)."""


class ClipReplacementError(RuntimeError):
    """One or more clip replacements failed; nothing was rendered."""

    def __init__(self, failures: List[Tuple[int, str]]):
        self.failures = failures
        self.failed_indices = [i for i, _ in failures]
        parts = [f"clip {i + 1} ({reason})" for i, reason in failures]
        super().__init__(
            "Could not replace " + ", ".join(parts)
            + ". Nothing was rendered; pick a different clip and try again."
        )


def plan_clip_pads(effective: Sequence[float], shortfall: Sequence[float],
                   transition: float, required: float,
                   min_clip: float = 0.0, tolerance: float = PAD_TOLERANCE) -> List[float]:
    """Freeze-frame seconds to add to each clip so the video covers ``required`` seconds.

    Args:
        effective: Seconds of footage each clip contributes after trimming.
        shortfall: How much shorter each clip is than its slot (trims, short
            replacements); the deficit is shared out in proportion to it, so
            the clip that was shortened is the one that gets extended.
        transition: Crossfade overlap between consecutive clips.
        required: Seconds the concatenated video must last (the narration).
        min_clip: Every clip is padded to at least this long (xfade needs
            clips longer than the transition).

    Returns:
        Pad seconds per clip (0 when the clips already cover ``required``).
    """
    n = len(effective)
    if n == 0:
        return []
    pads = [max(0.0, min_clip - e) for e in effective]
    total = sum(e + p for e, p in zip(effective, pads)) - transition * max(0, n - 1)
    deficit = required - total
    if deficit > tolerance:
        weights = [max(0.0, s) for s in shortfall]
        weight_sum = sum(weights)
        if weight_sum > 0:
            for i, w in enumerate(weights):
                pads[i] += deficit * w / weight_sum
        else:
            pads[-1] += deficit
    return [round(p, 3) for p in pads]


class EditorService:
    """Business logic for the video editor."""

    # ── project.json ──────────────────────────────────────────

    def load_project(self, project_dir: Path) -> dict:
        """Load and parse project.json from a project directory."""
        project_json = project_dir / "project.json"
        if not project_json.exists():
            raise FileNotFoundError(f"project.json not found in {project_dir}")
        with open(project_json, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_project(self, project_dir: Path, project: dict) -> None:
        """Write project.json back to disk (atomically, so readers never see half a file)."""
        project_json = project_dir / "project.json"
        tmp = project_json.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(project, f, indent=2, ensure_ascii=False)
        os.replace(tmp, project_json)

    # ── Render settings ───────────────────────────────────────

    @staticmethod
    def _ffmpeg() -> str:
        return os.getenv("FFMPEG_PATH", "ffmpeg")

    @staticmethod
    def _ffprobe() -> str:
        return os.getenv("FFPROBE_PATH", "ffprobe")

    def _probe_duration(self, path: Path) -> Optional[float]:
        try:
            result = subprocess.run(
                [self._ffprobe(), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return None
            return float(result.stdout.strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _loop_mode(project: dict) -> bool:
        return bool(project.get("loop_mode")) and len(project.get("clips") or []) >= 2

    @staticmethod
    def _transition_duration(project: dict) -> float:
        value = project.get("transition_duration")
        if value is None:
            value = float(os.getenv("TRANSITION_DURATION", "0.5"))
        return float(value)

    @staticmethod
    def _transition_type(project: dict) -> str:
        return project.get("transition_type") or os.getenv("TRANSITION_TYPE", "crossfade")

    def _outro_duration(self, project: dict) -> float:
        """Freeze-frame outro on the last clip (never in loop mode)."""
        if project.get("loop_mode"):
            return 0.0
        value = project.get("outro_duration")
        if value is None:
            value = float(os.getenv("VIDEO_OUTRO_DURATION", "1.0"))
        return max(0.0, float(value))

    @staticmethod
    def _fade_duration(project: dict) -> float:
        if project.get("loop_mode"):
            return 0.0
        value = project.get("fade_duration")
        if value is None:
            value = float(os.getenv("FADE_OUT_DURATION", "1.0"))
        return max(0.0, float(value))

    def _audio_path(self, project_dir: Path, project: dict) -> Optional[Path]:
        """The audio the video is finalized with (mixed if present, else narration)."""
        audio = project.get("audio") or {}
        for key in ("mixed_file", "narration_file"):
            name = audio.get(key)
            if name and (project_dir / name).exists():
                return project_dir / name
        return None

    # ── Clips ─────────────────────────────────────────────────

    def _normalize_clips(self, project_dir: Path, project: dict) -> None:
        """Give legacy clip entries a source_file (in place; idempotent).

        Old projects stored a single ``file``. A trim-only edit never touched
        it, but a replacement wrote the already-trimmed result over it; such a
        file is shorter than its slot, so its trims are treated as baked in.
        """
        for i, c in enumerate(project.get("clips") or []):
            c.setdefault("index", i)
            if "source_file" in c:
                continue
            c["source_file"] = c.get("file")
            c["trimmed_file"] = None
            c.setdefault("pad_seconds", 0)
            trims = float(c.get("trim_start") or 0) + float(c.get("trim_end") or 0)
            if trims > 0 and c.get("file"):
                dur = self._probe_duration(project_dir / c["file"])
                slot = float(c.get("duration_seconds") or 0)
                if dur is not None and slot and dur < slot - trims / 2:
                    c["trim_start"] = 0
                    c["trim_end"] = 0

    def _slot_specs(self, project_dir: Path, project: dict) -> List[dict]:
        """What each slot of the video is cut from: source, trims and slot length."""
        clips = project.get("clips") or []
        loop = self._loop_mode(project)
        specs = []
        for i, c in enumerate(clips):
            src_clip = clips[0] if (loop and i == len(clips) - 1) else c
            source = src_clip.get("source_file") or src_clip.get("file")
            if not source or not (project_dir / source).exists():
                raise EditorValidationError(f"Footage for clip {i + 1} is missing from the project")
            slot = float(src_clip.get("duration_seconds") or 0)
            src_dur = self._probe_duration(project_dir / source) or slot
            if not slot:
                slot = src_dur
            ts = max(0.0, float(src_clip.get("trim_start") or 0))
            te = max(0.0, float(src_clip.get("trim_end") or 0))
            effective = max(0.1, min(src_dur - ts, slot - ts - te))
            specs.append({
                "source": source, "trim_start": ts, "trim_end": te,
                "slot": slot, "source_duration": src_dur, "effective": effective,
            })
        return specs

    def _render_clip(self, project_dir: Path, spec: dict, pad: float, out_rel: str) -> str:
        """Cut ``spec`` from its source and freeze the last frame for ``pad`` seconds."""
        from app.core.config import settings as cfg
        out = project_dir / out_rel
        tmp = out.with_name(out.stem + ".tmp.mp4")
        cmd = [self._ffmpeg()]
        if spec["trim_start"] > 0:
            cmd += ["-ss", f"{spec['trim_start']:.3f}"]
        cmd += ["-t", f"{spec['effective']:.3f}", "-i", str(project_dir / spec["source"])]
        if pad > 0:
            cmd += ["-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}"]
        cmd += [
            "-c:v", "libx264",
            "-preset", cfg.ffmpeg_intermediate_preset,
            "-crf", str(cfg.ffmpeg_intermediate_crf),
            "-pix_fmt", "yuv420p",
            "-threads", str(cfg.ffmpeg_threads),
            "-an", "-y", str(tmp),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        os.replace(tmp, out)
        return out_rel

    def _build_render_clips(self, project_dir: Path, project: dict) -> List[Path]:
        """Derive every slot's clip from source + trims, pad to cover the narration.

        Applies the loop copy (last slot = first clip) and the freeze-frame
        outro exactly like the first render, records ``trimmed_file`` /
        ``pad_seconds`` per clip and the final list in ``render_clips``.
        """
        clips = project.get("clips") or []
        if not clips:
            raise ValueError("No clip files available to build the video")
        loop = self._loop_mode(project)
        outro = self._outro_duration(project)
        transition = self._transition_duration(project)

        specs = self._slot_specs(project_dir, project)
        audio = self._audio_path(project_dir, project)
        required = (self._probe_duration(audio) or 0.0) if audio else 0.0
        pads = plan_clip_pads(
            [s["effective"] for s in specs],
            [max(0.0, s["slot"] - s["effective"]) for s in specs],
            transition, required,
            min_clip=(transition + 0.1) if len(specs) > 1 else 0.0,
        )

        render_rel = []
        last = len(specs) - 1
        for i, (spec, pad) in enumerate(zip(specs, pads)):
            total_pad = pad + (outro if i == last else 0.0)
            untouched = (spec["trim_start"] == 0 and spec["trim_end"] == 0 and total_pad < 0.001
                         and spec["effective"] >= spec["source_duration"] - 0.05)
            clip = clips[i]
            wanted = {"source": spec["source"], "trim_start": spec["trim_start"],
                      "trim_end": spec["trim_end"], "effective": round(spec["effective"], 3),
                      "pad": round(total_pad, 3)}
            if untouched:
                rel = spec["source"]
                clip["trimmed_file"] = None
            else:
                rel = f"clips/clip_{i}_{'loop' if (loop and i == last) else 'edit'}.mp4"
                cached = clip.get("trimmed_file") == rel and clip.get("render_spec") == wanted
                if not (cached and (project_dir / rel).exists()):
                    self._render_clip(project_dir, spec, total_pad, rel)
                clip["trimmed_file"] = rel
            clip["render_spec"] = wanted
            clip["pad_seconds"] = pad
            clip["loop_copy"] = bool(loop and i == last)
            render_rel.append(rel)

        project["render_clips"] = render_rel
        return [project_dir / r for r in render_rel]

    def _concat(self, project_dir: Path, project: dict, clip_paths: List[Path]) -> Path:
        from video_assembler import VideoAssembler

        video_no_audio = project_dir / "video_no_audio.mp4"
        tmp = project_dir / "video_no_audio.tmp.mp4"
        assembler = VideoAssembler(ffmpeg_path=self._ffmpeg(), ffprobe_path=self._ffprobe())
        assembler.concat_videos_with_transitions(
            clip_paths, tmp,
            transition=self._transition_type(project),
            transition_duration=self._transition_duration(project),
        )
        os.replace(tmp, video_no_audio)
        output = project.setdefault("output", {})
        output["video_no_audio"] = "video_no_audio.mp4"
        output["video_no_audio_rev"] = VIDEO_NO_AUDIO_REV
        return video_no_audio

    def _ensure_video_no_audio(self, project_dir: Path, project: dict) -> Path:
        """Return video_no_audio.mp4, (re)building it from the project's clips if needed.

        Single-pass renders never write this file, so the editor builds it on
        demand: from ``render_clips`` (the exact list the last render used,
        incl. loop copy and outro) when present, otherwise from the clip
        sources with the same loop/outro/padding rules as the first render.
        """
        output = project.get("output") or {}
        name = output.get("video_no_audio")
        if (name and (project_dir / name).exists()
                and output.get("video_no_audio_rev") == VIDEO_NO_AUDIO_REV):
            return project_dir / name

        self._normalize_clips(project_dir, project)
        render_clips = project.get("render_clips") or []
        paths = [project_dir / r for r in render_clips]
        if not (paths and len(paths) == len(project.get("clips") or [])
                and all(p.exists() for p in paths)):
            paths = self._build_render_clips(project_dir, project)

        video_no_audio = self._concat(project_dir, project, paths)
        self.save_project(project_dir, project)
        return video_no_audio

    # ── Preview ───────────────────────────────────────────────

    def preview_is_current(self, project_dir: Path, project: dict) -> bool:
        """True if video_preview.mp4 exists and is newer than its video and audio sources."""
        preview_path = project_dir / "video_preview.mp4"
        output = project.get("output") or {}
        vna = output.get("video_no_audio")
        audio_src = self._audio_path(project_dir, project)
        if not (preview_path.exists() and vna and audio_src
                and (project_dir / vna).exists()
                and output.get("video_no_audio_rev") == VIDEO_NO_AUDIO_REV):
            return False
        mtime = preview_path.stat().st_mtime
        return mtime > (project_dir / vna).stat().st_mtime and mtime > audio_src.stat().st_mtime

    def generate_preview(self, project_dir: Path, project: dict) -> Path | None:
        """Generate a subtitle-free preview video by muxing video + audio.

        Returns path to the preview file, or None if source files are missing.
        Skips regeneration if preview is already up-to-date. Callers must hold
        the project's edit lock (it may rebuild video_no_audio / project.json).
        """
        preview_path = project_dir / "video_preview.mp4"
        audio_src = self._audio_path(project_dir, project)
        if not audio_src:
            return None

        if self.preview_is_current(project_dir, project):
            return preview_path

        try:
            video_src = self._ensure_video_no_audio(project_dir, project)
        except Exception:
            logger.exception("Could not build video_no_audio for %s", project_dir)
            return None

        if self.preview_is_current(project_dir, project):
            return preview_path

        # Same audio padding / length as the real render, minus subtitles and fade
        tmp = project_dir / "video_preview.tmp.mp4"
        pad = self._outro_duration(project)
        audio_args = ["-af", f"apad=pad_dur={pad:.3f}"] if pad > 0 else []
        cmd = [
            self._ffmpeg(),
            "-i", str(video_src),
            "-i", str(audio_src),
            "-c:v", "copy",
            *audio_args,
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v", "-map", "1:a",
            "-shortest",
            "-movflags", "+faststart",
            "-y", str(tmp),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            # Atomic swap: a concurrent stream never reads a half-written file
            os.replace(tmp, preview_path)
            return preview_path
        except subprocess.CalledProcessError:
            tmp.unlink(missing_ok=True)
            return None

    # ── Editor data ───────────────────────────────────────────

    def subtitle_state(self, project: dict) -> dict:
        """Style, explicit overrides and the resolved values captions are drawn with."""
        from subtitle_generator import SubtitleGenerator

        orientation = project.get("orientation", "landscape")
        sub = project.get("subtitles") or {}
        style = sub.get("style") or "youtube"
        if "overrides" in sub:
            overrides = dict(sub.get("overrides") or {})
        else:
            # Legacy project: stored values equal to the old defaults were not choices
            overrides = {k: sub[k] for k in SUBTITLE_OVERRIDE_KEYS
                         if sub.get(k) and sub[k] != _LEGACY_SUBTITLE_DEFAULTS[k]}
        clean = {}
        for k, v in overrides.items():
            if k not in SUBTITLE_OVERRIDE_KEYS or not v:
                continue
            if k in ("color", "outline_color"):
                try:
                    v = SubtitleGenerator.normalize_color(v)
                except ValueError:
                    continue
            clean[k] = v
        resolved = SubtitleGenerator(style=style, orientation=orientation,
                                     custom_overrides=clean or None).resolved_settings()
        presets = {
            name: SubtitleGenerator(style=name, orientation=orientation).resolved_settings()
            for name in SUBTITLE_PRESETS
        }
        return {"style": style, "overrides": clean, "resolved": resolved, "presets": presets}

    def rendered_duration(self, project_dir: Path, project: dict,
                          output_path: Path = None, timeline_end: float = None) -> float:
        """Length of the rendered video, which the timeline must match.

        ``project["duration_seconds"]`` is only the length the user asked for.
        Prefer the rendered file, then narration + outro, then the clip timeline.
        """
        if output_path and Path(output_path).exists():
            probed = self._probe_duration(Path(output_path))
            if probed:
                return round(probed, 3)
        audio = self._audio_path(project_dir, project)
        if audio:
            probed = self._probe_duration(audio)
            if probed:
                return round(probed + self._outro_duration(project), 3)
        if timeline_end:
            return round(timeline_end, 3)
        return float(project.get("duration_seconds") or 0)

    def get_editor_data(self, project_dir: Path, project: dict, stream_url: str,
                        session_id: str = None, video_id: int = None,
                        preview_stream_url: str = None, output_path: Path = None) -> dict:
        """Build the full editor data response from a project manifest."""
        orientation = project.get("orientation", "landscape")
        transition_duration = self._transition_duration(project)
        outro = self._outro_duration(project)

        # Clip start/end times as rendered: crossfade overlaps, trims, pads, outro
        clips_data = project.get("clips", [])
        clips = []
        current_time = 0.0
        last = len(clips_data) - 1
        for i, c in enumerate(clips_data):
            clip_dur = float(c.get("duration_seconds") or 0)
            trim_start = float(c.get("trim_start") or 0)
            trim_end = float(c.get("trim_end") or 0)
            pad = float(c.get("pad_seconds") or 0)
            if i > 0:
                current_time -= transition_duration
            start = max(0, current_time)
            rendered = max(0.0, clip_dur - trim_start - trim_end) + pad + (outro if i == last else 0)
            clips.append({
                "index": c.get("index", i),
                "keywords": c.get("keywords", []),
                "visual_description": c.get("visual_description", ""),
                "video_id": c.get("video_id"),
                "provider": c.get("provider") or "Pexels",
                "media_type": c.get("media_type") or "video",
                "author": c.get("author"),
                "duration_seconds": clip_dur,
                "start_time": round(start, 3),
                "end_time": round(start + clip_dur, 3),
                "trim_start": trim_start,
                "trim_end": trim_end,
                "pad_seconds": pad,
                "loop_copy": bool(c.get("loop_copy")) or (self._loop_mode(project) and i == last),
            })
            current_time = start + rendered

        duration = self.rendered_duration(project_dir, project, output_path,
                                          timeline_end=current_time)

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

        # Subtitle settings: the values the captions are actually drawn with
        sub_data = project.get("subtitles", {})
        state = self.subtitle_state(project)
        subtitles = {
            "enabled": sub_data.get("enabled", True) is not False,
            "style": state["style"],
            **state["resolved"],
            "overrides": sorted(state["overrides"]),
            "presets": state["presets"],
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

    # ── Subtitle entries ──────────────────────────────────────

    @staticmethod
    def _group_word_ranges(word_timestamps: List[Dict], orientation: str) -> List[Tuple[int, int]]:
        """[start, end) word index ranges of the editor's subtitle entries.

        Groups 2-4 words at natural speech boundaries, similar to SubtitleGenerator logic.
        """
        ranges = []
        max_words = 3 if orientation in ("portrait", "square") else 4
        max_duration = 1.5
        group_start = None
        for i, wt in enumerate(word_timestamps):
            if group_start is None:
                group_start = i
            word = wt.get("word", "")
            first_start = word_timestamps[group_start].get("start", 0)
            count = i - group_start + 1
            if (count >= max_words
                    or wt.get("end", 0) - first_start >= max_duration
                    or word.rstrip().endswith((".", "!", "?", ",", ";", ":"))):
                ranges.append((group_start, i + 1))
                group_start = None
        if group_start is not None:
            ranges.append((group_start, len(word_timestamps)))
        return ranges

    def compute_subtitle_entries(self, word_timestamps: List[Dict], orientation: str) -> List[Dict]:
        """Convert word timestamps into subtitle entries (phrase groups) for the timeline."""
        if not word_timestamps:
            return []
        entries = []
        for idx, (a, b) in enumerate(self._group_word_ranges(word_timestamps, orientation)):
            words = word_timestamps[a:b]
            entries.append({
                "index": idx,
                "text": " ".join(w.get("word", "") for w in words),
                "start_time": round(words[0].get("start", 0), 3),
                "end_time": round(words[-1].get("end", 0), 3),
            })
        return entries

    def _entries_to_word_timestamps(self, entries: list, original_timestamps: list,
                                    orientation: str = "landscape") -> list:
        """Convert edited subtitle entries back to word-level timestamps.

        Unchanged phrases keep their original (ASR) word timings. A phrase
        whose span moved but whose words are the same count keeps each word's
        relative timing, rescaled into the new span. Only phrases with a
        different number of words are spread out (by word length).
        """
        original_timestamps = original_timestamps or []
        ranges = self._group_word_ranges(original_timestamps, orientation)
        tol = 0.0015
        out = []
        for entry in entries:
            text = entry.get("text", "") or ""
            start = float(entry.get("start_time", 0))
            end = float(entry.get("end_time", 0))
            words = text.split()
            if not words:
                continue  # caption deleted
            idx = entry.get("index")
            orig = original_timestamps[ranges[idx][0]:ranges[idx][1]] \
                if isinstance(idx, int) and 0 <= idx < len(ranges) else None

            if orig:
                o_start = round(orig[0].get("start", 0), 3)
                o_end = round(orig[-1].get("end", 0), 3)
                same_words = [w.get("word", "") for w in orig] == words
                same_time = abs(start - o_start) <= tol and abs(end - o_end) <= tol
                if same_words and same_time:
                    out.extend(dict(w) for w in orig)
                    continue
                if len(orig) == len(words):
                    span = o_end - o_start
                    for j, (word, o) in enumerate(zip(words, orig)):
                        if span > 0:
                            f0 = (o.get("start", 0) - o_start) / span
                            f1 = (o.get("end", 0) - o_start) / span
                        else:
                            f0, f1 = j / len(words), (j + 1) / len(words)
                        out.append({
                            "word": word,
                            "start": round(start + f0 * (end - start), 3),
                            "end": round(start + f1 * (end - start), 3),
                        })
                    continue

            weights = [max(1, len(w)) for w in words]
            total = sum(weights)
            t = start
            for word, weight in zip(words, weights):
                d = (end - start) * weight / total
                out.append({"word": word, "start": round(t, 3), "end": round(t + d, 3)})
                t += d
        out.sort(key=lambda w: w["start"])
        return out

    # ── Render ────────────────────────────────────────────────

    def render_changes(self, project_dir: Path, project: dict,
                       subtitles: dict = None, audio: dict = None,
                       replaced_clips: list = None, output_path: Path = None) -> str:
        """Render video with editor changes. Returns render type description.

        Only re-processes what changed:
          - subtitles only: regenerate ASS + finalize (~5-10s)
          - audio only: re-mix audio + finalize (~10-20s)
          - clip edits: re-cut changed clips + re-concat + finalize (~30-60s)

        Raises:
            EditorValidationError: the edit can't be applied (bad index/trim).
            ClipReplacementError: a replacement clip could not be fetched.
        """
        from effects_engine import EffectsEngine

        orientation = project.get("orientation", "landscape")
        self._normalize_clips(project_dir, project)
        render_type_parts = []

        # Step 1: Clip edits (most expensive) — validated before anything is touched
        if replaced_clips:
            video_no_audio = self._reprocess_clips(project_dir, project, replaced_clips, orientation)
            render_type_parts.append("clips")
        else:
            video_no_audio = self._ensure_video_no_audio(project_dir, project)

        audio_path = self._audio_path(project_dir, project)
        if not audio_path:
            raise EditorValidationError("Audio source file not found in project")

        # Step 2: Handle audio changes
        if audio:
            new_narration_vol = audio.get("narration_volume", 1.0)
            new_music_vol = audio.get("music_volume", 0.15)
            old_narration_vol = project.get("audio", {}).get("narration_volume", 1.0)
            old_music_vol = project.get("audio", {}).get("music_volume", 0.15)

            if new_narration_vol != old_narration_vol or new_music_vol != old_music_vol:
                audio_path = self._remix_audio(project_dir, project, new_narration_vol, new_music_vol)
                project.setdefault("audio", {})["narration_volume"] = new_narration_vol
                project["audio"]["music_volume"] = new_music_vol
                project["audio"]["mixed_file"] = "audio_final.wav"
                render_type_parts.append("audio")

        # Step 3: Subtitles
        subtitle_path = None
        sub_project = project.setdefault("subtitles", {})
        if subtitles:
            if subtitles.get("enabled", True):
                subtitle_path = self._regenerate_subtitles(project_dir, project, subtitles, orientation)
            else:
                sub_project["enabled"] = False
            render_type_parts.append("subtitles")
        elif sub_project.get("enabled", True) is not False:
            sub_file = sub_project.get("file")
            if sub_file and (project_dir / sub_file).exists():
                subtitle_path = project_dir / sub_file
            else:
                # No ASS on disk: redraw with the saved style so captions aren't lost
                state = self.subtitle_state(project)
                subtitle_path = self._regenerate_subtitles(
                    project_dir, project,
                    {"enabled": True, "style": state["style"], **state["overrides"]},
                    orientation,
                )

        # Step 4: Finalize — same audio padding / fade as the first render
        finalizer = EffectsEngine(ffmpeg_path=self._ffmpeg(), ffprobe_path=self._ffprobe())
        subscribe_video, subscribe_params = self._subscribe_overlay(orientation)
        tmp_out = Path(output_path).with_name(Path(output_path).stem + ".editing.mp4")
        try:
            finalizer.finalize_video(
                video_no_audio=video_no_audio,
                audio_path=audio_path,
                output_path=tmp_out,
                subtitle_path=subtitle_path,
                subscribe_video=subscribe_video,
                subscribe_params=subscribe_params,
                fade_duration=self._fade_duration(project),
                audio_pad_duration=self._outro_duration(project),
            )
            os.replace(tmp_out, output_path)
        finally:
            tmp_out.unlink(missing_ok=True)

        # Swapped clips change who the footage belongs to: keep the credits current
        if replaced_clips:
            self._rewrite_credits(project, Path(output_path))

        # Update file size in DB
        self._update_video_file_size(project, Path(output_path))

        # Save updated project
        self.save_project(project_dir, project)

        # Regenerate subtitle-free preview for editor
        self.generate_preview(project_dir, project)

        return " + ".join(render_type_parts) if render_type_parts else "finalize"

    @staticmethod
    def _rewrite_credits(project: dict, output_path: Path) -> None:
        """Regenerate ``<output>_credits.txt/.json`` from the project's current clips."""
        from license_tracker import write_credits_for_clips
        try:
            write_credits_for_clips(project.get("clips") or [], output_path)
        except OSError as e:
            logger.warning("Could not rewrite credits for %s: %s", output_path, e)

    @staticmethod
    def _subscribe_overlay(orientation: str):
        if os.getenv("ENABLE_SUBSCRIBE_OVERLAY", "false").lower() != "true":
            return None, None
        sub_path = Path(os.getenv("SUBSCRIBE_VIDEO_PATH", "subscribe.mp4"))
        if not sub_path.is_absolute():
            sub_path = Path(settings.base_dir) / sub_path
        if not sub_path.exists():
            return None, None
        return sub_path, {
            "orientation": orientation,
            "scale": float(os.getenv("SUBSCRIBE_SCALE", "0")) or None,
            "margin_bottom": int(os.getenv("SUBSCRIBE_MARGIN_BOTTOM", "50")),
            "chromakey_color": os.getenv("SUBSCRIBE_CHROMAKEY_COLOR", "0x60FA01"),
            "chromakey_similarity": float(os.getenv("SUBSCRIBE_CHROMAKEY_SIMILARITY", "0.25")),
            "chromakey_blend": float(os.getenv("SUBSCRIBE_CHROMAKEY_BLEND", "0.18")),
        }

    def _regenerate_subtitles(self, project_dir: Path, project: dict,
                              sub_settings: dict, orientation: str) -> Optional[Path]:
        """Generate a new ASS file and record the settings it was drawn with.

        Only non-empty font/size/colour/position values are overrides; the rest
        come from the style preset for this orientation.
        """
        from subtitle_generator import SubtitleGenerator

        style_preset = sub_settings.get("style") or "youtube"
        overrides = {k: sub_settings[k] for k in SUBTITLE_OVERRIDE_KEYS if sub_settings.get(k)}
        try:
            sub_gen = SubtitleGenerator(style=style_preset, orientation=orientation,
                                        custom_overrides=overrides or None)
        except ValueError as e:
            raise EditorValidationError(str(e))
        for key in ("color", "outline_color"):
            if key in overrides:
                overrides[key] = SubtitleGenerator.normalize_color(overrides[key])

        word_timestamps = project.get("word_timestamps")
        if not word_timestamps and project.get("script_text"):
            word_timestamps = self._generate_heuristic_timestamps(project_dir, project)
            if word_timestamps:
                project["word_timestamps"] = word_timestamps

        custom_entries = sub_settings.get("entries")
        if custom_entries is not None and word_timestamps is not None:
            # Edited text/timing: keep original word timing where untouched
            word_timestamps = self._entries_to_word_timestamps(
                custom_entries, word_timestamps, orientation)
            project["word_timestamps"] = word_timestamps

        project["subtitles"] = {
            "enabled": True,
            "style": style_preset,
            **sub_gen.resolved_settings(),
            "overrides": overrides,
            "file": None,
        }
        if not word_timestamps:
            return None

        subtitle_file = project_dir / "subtitles.ass"
        sub_gen.generate_for_words(word_timestamps, subtitle_file)
        project["subtitles"]["file"] = "subtitles.ass"
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

        narration_file = (project.get("audio") or {}).get("narration_file")
        if not narration_file:
            return []
        narration_path = project_dir / narration_file
        if not narration_path.exists():
            return []

        total_duration = self._probe_duration(narration_path)
        if not total_duration:
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

    def _remix_audio(self, project_dir: Path, project: dict,
                     narration_volume: float, music_volume: float) -> Path:
        """Re-mix narration + background music with new volumes."""
        narration_file = project.get("audio", {}).get("narration_file")
        if not narration_file:
            raise ValueError("Narration audio file not found in project")

        narration_path = project_dir / narration_file
        if not narration_path.exists():
            raise ValueError("Narration audio file not found on disk")

        ffmpeg = self._ffmpeg()
        output = project_dir / "audio_final.wav"

        if project.get("audio", {}).get("has_background_music"):
            music_file_ref = project.get("audio", {}).get("music_file")
            music_path = project_dir / music_file_ref if music_file_ref else None

            # Fallback for legacy projects: try to re-select music
            if not music_path or not music_path.exists():
                logger.warning("Music file not found in project, attempting re-selection")
                try:
                    from music_selector import MusicSelector
                    selector = MusicSelector(music_dir=Path(os.getenv("MUSIC_DIR", "music")))
                    prompt = project.get("prompt", project.get("title", ""))
                    music_path = selector.select_music(prompt)
                except Exception as e:
                    logger.warning(f"Could not find background music: {e}")
                    music_path = None

            if music_path and music_path.exists():
                from audio_mixer import AudioMixer
                mixer = AudioMixer(ffmpeg_path=ffmpeg, ffprobe_path=self._ffprobe())
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

    # ── Clip edits ────────────────────────────────────────────

    def validate_clip_edits(self, project: dict, edits: list) -> None:
        """Reject bad indices, trims that leave too little footage, and loop-copy edits."""
        clips = project.get("clips") or []
        loop = self._loop_mode(project)
        seen = set()
        for edit in edits:
            idx = edit.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(clips):
                raise EditorValidationError(
                    f"Clip {idx + 1 if isinstance(idx, int) else idx} does not exist "
                    f"(this video has {len(clips)} clips)")
            if idx in seen:
                raise EditorValidationError(f"Clip {idx + 1} is listed twice")
            seen.add(idx)
            clip = clips[idx]
            if loop and idx == len(clips) - 1:
                changed = (
                    (edit.get("video_id") and str(edit["video_id"]) != str(clip.get("video_id")))
                    or float(edit.get("trim_start") or 0) != float(clip.get("trim_start") or 0)
                    or float(edit.get("trim_end") or 0) != float(clip.get("trim_end") or 0)
                )
                if changed:
                    raise EditorValidationError(
                        f"Clip {idx + 1} repeats clip 1 so the video loops seamlessly; edit clip 1 instead")
            ts = float(edit.get("trim_start") or 0)
            te = float(edit.get("trim_end") or 0)
            if ts < 0 or te < 0:
                raise EditorValidationError(f"Clip {idx + 1}: trims can't be negative")
            slot = float(clip.get("duration_seconds") or 0)
            if slot and ts + te > slot - MIN_CLIP_SECONDS + 1e-6:
                raise EditorValidationError(
                    f"Clip {idx + 1}: trim start + trim end ({ts + te:.1f}s) must leave at least "
                    f"{MIN_CLIP_SECONDS}s of the {slot:.1f}s clip")

    def _reprocess_clips(self, project_dir: Path, project: dict,
                         replaced_clips: list, orientation: str) -> Path:
        """Apply trims / replacements and rebuild video_no_audio.

        Every replacement is fetched before anything in the project changes;
        if any fails, ClipReplacementError lists them and nothing is rendered.
        """
        self.validate_clip_edits(project, replaced_clips)
        clips = project.get("clips") or []

        new_sources = {}
        failures = []
        for edit in replaced_clips:
            idx = edit["index"]
            clip = clips[idx]
            vid = edit.get("video_id")
            provider = edit.get("provider") or clip.get("provider") or "Pexels"
            is_replace = bool(vid) and (
                str(vid) != str(clip.get("video_id"))
                or (edit.get("provider") and edit["provider"] != (clip.get("provider") or "Pexels"))
            )
            if not is_replace:
                continue
            try:
                new_sources[idx] = self._fetch_replacement(
                    project_dir, project, idx, str(vid), provider,
                    edit.get("media_type") or "video", orientation)
            except Exception as e:
                logger.warning("Replacing clip %d with %s %s failed: %s", idx, provider, vid, e)
                failures.append((idx, _short_reason(e)))

        if failures:
            for meta in new_sources.values():
                (project_dir / meta["source_file"]).unlink(missing_ok=True)
            raise ClipReplacementError(failures)

        # All fetched: apply edits to the project
        for edit in replaced_clips:
            idx = edit["index"]
            clip = clips[idx]
            if idx in new_sources:
                meta = new_sources[idx]
                old_source = clip.get("source_file")
                clip.update(meta)
                clip["file"] = meta["source_file"]
                clip["keywords"] = edit.get("keywords") or clip.get("keywords") or []
                if old_source and "_src_" in old_source and old_source != meta["source_file"]:
                    (project_dir / old_source).unlink(missing_ok=True)
            clip["trim_start"] = float(edit.get("trim_start") or 0)
            clip["trim_end"] = float(edit.get("trim_end") or 0)

        paths = self._build_render_clips(project_dir, project)
        return self._concat(project_dir, project, paths)

    def _fetch_replacement(self, project_dir: Path, project: dict, idx: int, video_id: str,
                           provider: str, media_type: str, orientation: str) -> dict:
        """Download one replacement and process it to its slot length (the new source_file)."""
        from video_assembler import VideoAssembler, KEN_BURNS_EFFECTS
        from app.api.services.video_service import build_media_searcher

        clip = project["clips"][idx]
        slot = float(clip.get("duration_seconds") or 5)
        searcher = build_media_searcher()
        info = searcher.get_video_by_id(video_id, provider=provider, media_type=media_type)
        if not info:
            raise ValueError(f"{provider} {media_type} {video_id} not found")

        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", str(info.id or video_id))[:40] or "selected"
        rel = f"clips/clip_{idx}_src_{safe_id}.mp4"
        dest = project_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        download_dir = project_dir / "temp_download"
        download_dir.mkdir(exist_ok=True)
        assembler = VideoAssembler(ffmpeg_path=self._ffmpeg(), ffprobe_path=self._ffprobe())
        color_filter = self._color_grade_filter()
        is_image = getattr(info, "media_type", media_type) == "image"
        try:
            if is_image:
                downloaded = download_dir / f"clip_{idx}_{safe_id}.jpg"
                searcher.download_image(info, downloaded)
                if not downloaded.exists():
                    raise ValueError("download failed")
                assembler.image_to_video(
                    downloaded, dest, duration_sec=slot, orientation=orientation,
                    effect=KEN_BURNS_EFFECTS[idx % len(KEN_BURNS_EFFECTS)],
                    color_grade_filter=color_filter,
                )
            else:
                downloaded = download_dir / f"clip_{idx}_{safe_id}.mp4"
                searcher.download(info, downloaded)
                if not downloaded.exists():
                    raise ValueError("download failed")
                # Same processing as the first render: normalize + grade, padded to the slot
                assembler.process_clip(
                    downloaded, dest, duration_sec=slot,
                    color_grade_filter=color_filter, orientation=orientation,
                )
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)

        return {
            "source_file": rel,
            "trimmed_file": None,
            "video_id": str(info.id),
            "provider": getattr(info, "provider", provider) or provider,
            "media_type": "image" if is_image else "video",
            "author": getattr(info, "author", None),
            "author_url": getattr(info, "url", None),
            "render_spec": None,
        }

    @staticmethod
    def _color_grade_filter() -> Optional[str]:
        if os.getenv("ENABLE_COLOR_GRADING", "true").lower() != "true":
            return None
        try:
            from effects_engine import EffectsEngine
            custom_lut = os.getenv("CUSTOM_LUT_FILE", "")
            return EffectsEngine().get_color_filter(
                preset=os.getenv("COLOR_PRESET", "balanced"),
                custom_lut=Path(custom_lut) if custom_lut else None,
            )
        except Exception:
            return None

    # ── Clip search ───────────────────────────────────────────

    def search_clips(self, keywords: list, orientation: str, count: int = 6,
                     min_duration: float = 0.0, exclude: Optional[list] = None,
                     topic: Optional[str] = None) -> list:
        """Replacement candidates via the no-repeat broadening ladder.

        Clips already in the video (``exclude``: [{video_id, provider}]) are
        skipped, and when any result is at least ``min_duration`` (the slot
        length) long, only those are returned. Still images are not offered.
        """
        from app.api.services.video_service import build_media_searcher
        from app.media.clip_picker import (
            ClipReservations, clip_key, iter_rungs, media_key, RUNG_PIXABAY_IMAGE,
        )

        searcher = build_media_searcher()
        taken = ClipReservations(clip_key(c) for c in (exclude or []))
        min_duration = float(min_duration or 0)

        picked = []
        for rung, results in iter_rungs(searcher, keywords, orientation,
                                        min_duration=min_duration, topic=topic):
            if rung == RUNG_PIXABAY_IMAGE:
                continue
            fresh = []
            for v in results:
                key = media_key(v)
                if getattr(v, "media_type", "video") == "image" or not key or key in taken:
                    continue
                if any(media_key(p) == key for p in fresh):
                    continue
                fresh.append(v)
            if fresh:
                long_enough = [v for v in fresh if (v.duration or 0) >= min_duration]
                picked = long_enough or fresh
                break

        return [{
            "video_id": str(v.id),
            "provider": getattr(v, "provider", "Pexels") or "Pexels",
            "media_type": getattr(v, "media_type", "video") or "video",
            "preview_url": getattr(v, "preview_url", None) or v.download_url or v.url,
            "author": v.author or "",
            "duration_seconds": v.duration or 0,
            "width": v.width or 0,
            "height": v.height or 0,
        } for v in picked[:count]]

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


def _short_reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:120]


# Global service instance
editor_service = EditorService()
