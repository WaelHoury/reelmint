"""Subtitle generation with modern styling for video captions."""

from pathlib import Path
from typing import List, Dict, Tuple
import re

# Resolution mapping for orientations
ORIENTATION_RESOLUTION = {
    'landscape': (1920, 1080),
    'portrait': (1080, 1920),
    'square': (1080, 1080),
}


def _get_resolution(orientation: str) -> Tuple[int, int]:
    """Get (width, height) for a video orientation."""
    return ORIENTATION_RESOLUTION.get(orientation, (1920, 1080))


class SubtitleStyle:
    """Modern subtitle styling presets with orientation-based sizing."""

    YOUTUBE = {
        'landscape': {
            'font': 'Arial',
            'font_size': 52,  # Larger for landscape (1920x1080)
            'primary_color': '&H00FFFFFF',  # White
            'outline_color': '&H00000000',  # Black outline
            'back_color': '&H80000000',     # Semi-transparent black background
            'bold': True,
            'outline': 4,
            'shadow': 2,
            'alignment': 2,  # Bottom center
            'margin_v': 80,  # Distance from bottom
        },
        'portrait': {
            'font': 'Arial',
            'font_size': 36,  # Smaller for portrait (1080x1920)
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H80000000',
            'bold': True,
            'outline': 3,
            'shadow': 2,
            'alignment': 2,
            'margin_v': 60,
        },
        'square': {
            'font': 'Arial',
            'font_size': 44,
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H80000000',
            'bold': True,
            'outline': 3,
            'shadow': 2,
            'alignment': 2,
            'margin_v': 70,
        }
    }

    TIKTOK = {
        'landscape': {
            'font': 'Montserrat-Bold',
            'font_size': 64,  # Bold and large for landscape
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': True,
            'outline': 5,
            'shadow': 0,
            'alignment': 5,  # Center center
            'margin_v': 0,
        },
        'portrait': {
            'font': 'Montserrat-Bold',
            'font_size': 48,  # Standard for portrait
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': True,
            'outline': 4,
            'shadow': 0,
            'alignment': 5,
            'margin_v': 0,
        },
        'square': {
            'font': 'Montserrat-Bold',
            'font_size': 56,
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': True,
            'outline': 4,
            'shadow': 0,
            'alignment': 5,
            'margin_v': 0,
        }
    }

    MINIMAL = {
        'landscape': {
            'font': 'Helvetica',
            'font_size': 44,  # Larger minimal for landscape
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': False,
            'outline': 3,
            'shadow': 1,
            'alignment': 2,
            'margin_v': 60,
        },
        'portrait': {
            'font': 'Helvetica',
            'font_size': 32,
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': False,
            'outline': 2,
            'shadow': 1,
            'alignment': 2,
            'margin_v': 50,
        },
        'square': {
            'font': 'Helvetica',
            'font_size': 38,
            'primary_color': '&H00FFFFFF',
            'outline_color': '&H00000000',
            'back_color': '&H00000000',
            'bold': False,
            'outline': 2,
            'shadow': 1,
            'alignment': 2,
            'margin_v': 55,
        }
    }


class KaraokeStyle:
    """Word-by-word karaoke subtitle styles for portrait/square."""

    PORTRAIT = {
        'font': 'Comic Sans MS',
        'font_size': 72,
        'primary_color': '&H00FFFFFF',       # White (non-highlighted words)
        'highlight_color': '&H0000FFFF',     # Yellow (current word) — BGR format
        'outline_color': '&H00000000',       # Black outline
        'back_color': '&H00000000',
        'bold': True,
        'outline': 5,
        'shadow': 0,
        'alignment': 5,   # Center center
        'margin_v': 0,
    }

    SQUARE = {
        'font': 'Comic Sans MS',
        'font_size': 64,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H0000FFFF',
        'outline_color': '&H00000000',
        'back_color': '&H00000000',
        'bold': True,
        'outline': 5,
        'shadow': 0,
        'alignment': 5,
        'margin_v': 0,
    }


class SubtitleGenerator:
    """Generates subtitle files for video captions."""

    def __init__(self, style: str = 'youtube', orientation: str = 'landscape'):
        """Initialize subtitle generator.

        Args:
            style: Style preset ('youtube', 'tiktok', 'minimal')
            orientation: Video orientation ('landscape', 'portrait', 'square')
        """
        self.style_name = style
        self.orientation = orientation

        # Get style dict for the preset
        style_dict = getattr(SubtitleStyle, style.upper(), SubtitleStyle.YOUTUBE)

        # Select orientation-specific settings
        if orientation in style_dict:
            self.style = style_dict[orientation]
        else:
            # Fallback to landscape if orientation not found
            self.style = style_dict.get('landscape', style_dict)

    def generate_srt(self, scenes: List[Dict], output_path: Path) -> Path:
        """Generate SRT subtitle file from scenes.

        Args:
            scenes: List of dicts with 'narration', 'start_time', 'duration'
            output_path: Where to save SRT file

        Returns:
            Path to SRT file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            for i, scene in enumerate(scenes, 1):
                start_time = scene['start_time']
                end_time = start_time + scene['duration']

                # Write subtitle entry
                f.write(f"{i}\n")
                f.write(f"{self._format_srt_time(start_time)} --> {self._format_srt_time(end_time)}\n")
                f.write(f"{scene['narration']}\n")
                f.write("\n")

        return output_path

    def generate_ass(self, scenes: List[Dict], output_path: Path) -> Path:
        """Generate ASS subtitle file with advanced styling.

        Args:
            scenes: List of dicts with 'narration', 'start_time', 'duration'
            output_path: Where to save ASS file

        Returns:
            Path to ASS file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        res_w, res_h = _get_resolution(self.orientation)

        with open(output_path, 'w', encoding='utf-8') as f:
            # Write ASS header
            f.write("[Script Info]\n")
            f.write("ScriptType: v4.00+\n")
            f.write(f"PlayResX: {res_w}\n")
            f.write(f"PlayResY: {res_h}\n")
            f.write("WrapStyle: 0\n")
            f.write("\n")

            # Write styles
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                   "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                   "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                   "Alignment, MarginL, MarginR, MarginV, Encoding\n")

            # Default style
            style = self.style
            f.write(f"Style: Default,{style['font']},{style['font_size']},"
                   f"{style['primary_color']},&H000000FF,{style['outline_color']},{style['back_color']},"
                   f"{-1 if style['bold'] else 0},0,0,0,100,100,0,0,1,"
                   f"{style['outline']},{style['shadow']},{style['alignment']},10,10,{style['margin_v']},1\n")
            f.write("\n")

            # Write events
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            for scene in scenes:
                start_time = scene['start_time']
                end_time = start_time + scene['duration']
                text = scene['narration']

                # Split long text into multiple lines for better readability
                text = self._split_subtitle_text(text, max_chars_per_line=40)

                f.write(f"Dialogue: 0,{self._format_ass_time(start_time)},{self._format_ass_time(end_time)},"
                       f"Default,,0,0,0,,{text}\n")

        return output_path

    def _format_srt_time(self, seconds: float) -> str:
        """Format time for SRT format (HH:MM:SS,mmm).

        Args:
            seconds: Time in seconds

        Returns:
            Formatted time string
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _format_ass_time(self, seconds: float) -> str:
        """Format time for ASS format (H:MM:SS.cc).

        Args:
            seconds: Time in seconds

        Returns:
            Formatted time string
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int((seconds % 1) * 100)

        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

    def _split_subtitle_text(self, text: str, max_chars_per_line: int = 40) -> str:
        """Split long subtitle text into multiple lines.

        Args:
            text: Subtitle text
            max_chars_per_line: Maximum characters per line

        Returns:
            Text with line breaks (\\N for ASS format)
        """
        words = text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            word_length = len(word) + 1  # +1 for space

            if current_length + word_length > max_chars_per_line and current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
                current_length = word_length
            else:
                current_line.append(word)
                current_length += word_length

        if current_line:
            lines.append(' '.join(current_line))

        # Use \\N for ASS line breaks
        return '\\N'.join(lines)

    @staticmethod
    def map_sentences_to_timestamps(sentences: List[str],
                                    word_timestamps: List[Dict]) -> List[Dict]:
        """Map sentences to accurate start/end times using word timestamps.

        Uses position-based mapping: calculates each sentence's character position
        in the full text, then finds the corresponding word timestamp by proportion.
        This is robust against ASR word differences (contractions, etc.).

        Args:
            sentences: List of sentence strings
            word_timestamps: List of {'word': str, 'start': float, 'end': float}

        Returns:
            List of {'narration': str, 'start_time': float, 'duration': float}
        """
        if not sentences or not word_timestamps:
            return []

        # Get total audio duration from timestamps
        audio_start = word_timestamps[0]['start']
        audio_end = word_timestamps[-1]['end']
        audio_duration = audio_end - audio_start

        # Build full text and track character positions for each sentence
        full_text = ' '.join(sentences)
        total_chars = len(full_text)

        if total_chars == 0:
            return []

        result = []
        char_pos = 0

        for i, sentence in enumerate(sentences):
            sent_len = len(sentence)

            # Calculate this sentence's position as a proportion of total text
            start_ratio = char_pos / total_chars
            end_ratio = (char_pos + sent_len) / total_chars

            # Map to audio timestamps
            sent_start = audio_start + (start_ratio * audio_duration)
            sent_end = audio_start + (end_ratio * audio_duration)

            # Snap to nearest word boundaries for cleaner timing
            # Find closest word start
            best_start_idx = 0
            best_start_diff = abs(word_timestamps[0]['start'] - sent_start)
            for idx, wt in enumerate(word_timestamps):
                diff = abs(wt['start'] - sent_start)
                if diff < best_start_diff:
                    best_start_diff = diff
                    best_start_idx = idx

            # Find closest word end (searching from start position forward)
            best_end_idx = best_start_idx
            best_end_diff = abs(word_timestamps[best_start_idx]['end'] - sent_end)
            for idx in range(best_start_idx, len(word_timestamps)):
                diff = abs(word_timestamps[idx]['end'] - sent_end)
                if diff < best_end_diff:
                    best_end_diff = diff
                    best_end_idx = idx

            # Use snapped times
            final_start = word_timestamps[best_start_idx]['start']
            final_end = word_timestamps[best_end_idx]['end']

            # Ensure no overlap with previous sentence
            if result and final_start < result[-1]['start_time'] + result[-1]['duration']:
                final_start = result[-1]['start_time'] + result[-1]['duration']

            # Ensure positive duration
            duration = max(0.1, final_end - final_start)

            result.append({
                'narration': sentence,
                'start_time': final_start,
                'duration': duration
            })

            char_pos += sent_len + 1  # +1 for space between sentences

        return result

    @staticmethod
    def _group_words_by_phrase(word_timestamps: List[Dict],
                               pause_threshold: float = 0.22,
                               max_duration: float = 1.2,
                               min_duration: float = 0.4,
                               max_words: int = 3,
                               max_chars: int = 20) -> List[List[Dict]]:
        """Group word timestamps into natural phrase-sized display groups.

        Breaks on: audio pauses, punctuation boundaries, max duration/length.
        Keeps punctuated words at the END of their group (not start of next).

        Args:
            word_timestamps: List of {'word': str, 'start': float, 'end': float}
            pause_threshold: Gap between words (seconds) that triggers a break
            max_duration: Maximum group duration in seconds
            min_duration: Minimum group duration before soft breaks apply
            max_words: Hard max words per group
            max_chars: Hard max characters per group

        Returns:
            List of word groups (each group is a list of word timestamp dicts)
        """
        if not word_timestamps:
            return []

        HARD_BREAK_PUNCT = set('.!?')
        SOFT_BREAK_PUNCT = set(',;:\u2014')  # includes em dash

        groups = []
        current_group = []
        current_chars = 0

        for i, wt in enumerate(word_timestamps):
            current_group.append(wt)
            current_chars += len(wt['word']) + 1  # +1 for space

            # Decide whether to finalize this group after adding this word
            should_break = False
            word_text = wt['word'].rstrip()
            last_char = word_text[-1] if word_text else ''
            group_duration = wt['end'] - current_group[0]['start']

            # Hard break: sentence-ending punctuation
            if last_char in HARD_BREAK_PUNCT:
                should_break = True

            # Soft break: clause punctuation, but only if group is long enough
            elif last_char in SOFT_BREAK_PUNCT and len(current_group) >= 2:
                should_break = True

            # Max word/char/duration limits
            elif len(current_group) >= max_words:
                should_break = True
            elif current_chars >= max_chars:
                should_break = True
            elif group_duration >= max_duration:
                should_break = True

            # Pause break: large gap to next word
            elif i + 1 < len(word_timestamps):
                gap = word_timestamps[i + 1]['start'] - wt['end']
                if gap >= pause_threshold and len(current_group) >= 2:
                    should_break = True

            if should_break:
                groups.append(current_group)
                current_group = []
                current_chars = 0

        # Don't leave orphan words — append remainder
        if current_group:
            groups.append(current_group)

        return groups

    def generate_karaoke_ass(self, word_timestamps: List[Dict], output_path: Path,
                            highlight_color: str = None) -> Path:
        """Generate word-by-word karaoke subtitles for portrait/square.

        Uses phrase-aware grouping that breaks at natural speech boundaries
        (pauses, punctuation, clause boundaries). Each group is wrapped into
        1-2 lines for readability. Layout stays identical across all highlight
        frames within a group (no jumping).

        Args:
            word_timestamps: List of {'word': str, 'start': float, 'end': float}
            output_path: Where to save ASS file
            highlight_color: ASS color for highlighted word (BGR format)

        Returns:
            Path to ASS file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get karaoke style for current orientation
        if self.orientation == 'portrait':
            k_style = KaraokeStyle.PORTRAIT
        else:
            k_style = KaraokeStyle.SQUARE

        if highlight_color is None:
            highlight_color = k_style['highlight_color']

        primary_color = k_style['primary_color']

        res_w, res_h = _get_resolution(self.orientation)

        # Max chars per line — with 2-3 word groups, wrapping is rare
        max_line_chars = 20 if self.orientation == 'portrait' else 24

        with open(output_path, 'w', encoding='utf-8') as f:
            # ASS header
            f.write("[Script Info]\n")
            f.write("ScriptType: v4.00+\n")
            f.write(f"PlayResX: {res_w}\n")
            f.write(f"PlayResY: {res_h}\n")
            f.write("WrapStyle: 0\n")
            f.write("\n")

            # Styles
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                   "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                   "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                   "Alignment, MarginL, MarginR, MarginV, Encoding\n")

            f.write(f"Style: Karaoke,{k_style['font']},{k_style['font_size']},"
                   f"{k_style['primary_color']},&H000000FF,{k_style['outline_color']},{k_style['back_color']},"
                   f"{-1 if k_style['bold'] else 0},0,0,0,100,100,0,0,1,"
                   f"{k_style['outline']},{k_style['shadow']},{k_style['alignment']},10,10,{k_style['margin_v']},1\n")
            f.write("\n")

            # Events
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            if not word_timestamps:
                return output_path

            # Build phrase-aware groups
            groups = self._group_words_by_phrase(word_timestamps)

            # Pre-compute wrapped layout for each group.
            # The line break positions are fixed per group so that base layer
            # and all highlight layers have identical text layout (no jumping).
            def _wrap_group(group):
                """Return list of line-lists. Each line-list contains word indices."""
                lines = [[]]
                line_len = 0
                for idx, wt in enumerate(group):
                    wlen = len(wt['word']) + (1 if line_len > 0 else 0)
                    if line_len + wlen > max_line_chars and lines[-1]:
                        lines.append([])
                        line_len = 0
                        wlen = len(wt['word'])
                    lines[-1].append(idx)
                    line_len += wlen
                return lines

            for group_idx, group in enumerate(groups):
                group_start = group[0]['start']
                group_end = group[-1]['end']

                # Extend group end to meet next group (no gap)
                if group_idx + 1 < len(groups):
                    group_end = groups[group_idx + 1][0]['start']

                line_layout = _wrap_group(group)

                def _build_text(highlight_idx=-1):
                    """Build wrapped ASS text, optionally highlighting one word."""
                    line_strs = []
                    for line_indices in line_layout:
                        parts = []
                        for idx in line_indices:
                            w = group[idx]['word']
                            if idx == highlight_idx:
                                parts.append(f"{{\\c{highlight_color}}}{w}{{\\c{primary_color}}}")
                            else:
                                parts.append(w)
                        line_strs.append(' '.join(parts))
                    return '\\N'.join(line_strs)

                # Layer 0: base line (all words white) for full group duration
                base_text = _build_text()
                f.write(f"Dialogue: 0,"
                       f"{self._format_ass_time(group_start)},"
                       f"{self._format_ass_time(group_end)},"
                       f"Karaoke,,0,0,0,,{base_text}\n")

                # Layer 1: per-word highlight lines (seamless timing)
                for word_idx in range(len(group)):
                    w_start = group[word_idx]['start']
                    if word_idx + 1 < len(group):
                        w_end = group[word_idx + 1]['start']
                    else:
                        w_end = group_end

                    hl_text = _build_text(highlight_idx=word_idx)
                    f.write(f"Dialogue: 1,"
                           f"{self._format_ass_time(w_start)},"
                           f"{self._format_ass_time(w_end)},"
                           f"Karaoke,,0,0,0,,{hl_text}\n")

        return output_path

    def generate_phrase_ass(self, word_timestamps: List[Dict], output_path: Path) -> Path:
        """Generate phrase-based subtitles for landscape (no karaoke highlight).

        Uses the same phrase grouping as karaoke but displays as regular subtitles.
        Shows 2-3 words at a time with accurate timing from word timestamps.

        Args:
            word_timestamps: List of {'word': str, 'start': float, 'end': float}
            output_path: Where to save ASS file

        Returns:
            Path to ASS file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        res_w, res_h = _get_resolution(self.orientation)
        style = self.style

        with open(output_path, 'w', encoding='utf-8') as f:
            # ASS header
            f.write("[Script Info]\n")
            f.write("ScriptType: v4.00+\n")
            f.write(f"PlayResX: {res_w}\n")
            f.write(f"PlayResY: {res_h}\n")
            f.write("WrapStyle: 0\n")
            f.write("\n")

            # Styles - use the orientation-specific style
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                   "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                   "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                   "Alignment, MarginL, MarginR, MarginV, Encoding\n")

            f.write(f"Style: Default,{style['font']},{style['font_size']},"
                   f"{style['primary_color']},&H000000FF,{style['outline_color']},{style['back_color']},"
                   f"{-1 if style['bold'] else 0},0,0,0,100,100,0,0,1,"
                   f"{style['outline']},{style['shadow']},{style['alignment']},10,10,{style['margin_v']},1\n")
            f.write("\n")

            # Events
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            if not word_timestamps:
                return output_path

            # Build phrase-aware groups (same as karaoke)
            groups = self._group_words_by_phrase(word_timestamps)

            for group_idx, group in enumerate(groups):
                group_start = group[0]['start']
                group_end = group[-1]['end']

                # Extend group end to meet next group (no gap)
                if group_idx + 1 < len(groups):
                    group_end = groups[group_idx + 1][0]['start']

                # Build text from all words in the group
                text = ' '.join(wt['word'] for wt in group)

                f.write(f"Dialogue: 0,"
                       f"{self._format_ass_time(group_start)},"
                       f"{self._format_ass_time(group_end)},"
                       f"Default,,0,0,0,,{text}\n")

        return output_path

    def burn_subtitles(self, video_path: Path, subtitle_path: Path,
                      output_path: Path, ffmpeg_path: str = "ffmpeg") -> Path:
        """Burn subtitles into video using FFmpeg.

        Args:
            video_path: Input video file
            subtitle_path: ASS subtitle file
            output_path: Output video with burned subtitles
            ffmpeg_path: Path to FFmpeg executable

        Returns:
            Path to output video

        Raises:
            subprocess.CalledProcessError: If FFmpeg fails
        """
        import subprocess

        video_path = Path(video_path)
        subtitle_path = Path(subtitle_path)
        output_path = Path(output_path)

        # Convert paths to absolute for FFmpeg
        sub_path_str = str(subtitle_path.resolve()).replace('\\', '/')
        sub_path_str = sub_path_str.replace(':', '\\:')  # Escape colons for FFmpeg

        cmd = [
            ffmpeg_path,
            '-i', str(video_path),
            '-vf', f"ass='{sub_path_str}'",
            '-c:a', 'copy',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, capture_output=True, check=True)
        return output_path
