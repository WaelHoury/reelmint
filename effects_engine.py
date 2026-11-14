"""Video effects engine for transitions, filters, and enhancements."""

from pathlib import Path
from typing import List, Literal, Optional
import subprocess

from app.core.config import settings


class EffectsEngine:
    """Handles video transitions and visual effects."""

    TRANSITION_TYPES = {
        'crossfade': 'fade',      # Smooth blend between clips
        'dissolve': 'dissolve',   # Similar to crossfade
        'wipe': 'wipeleft',       # Directional wipe (xfade has no bare 'wipe')
        'slide': 'slideleft',     # Slide transition (xfade has no bare 'slide')
    }

    # Native ffmpeg xfade transition names, accepted as-is
    XFADE_TRANSITIONS = frozenset({
        'fade', 'fadeblack', 'fadewhite', 'fadegrays', 'dissolve', 'distance',
        'wipeleft', 'wiperight', 'wipeup', 'wipedown',
        'wipetl', 'wipetr', 'wipebl', 'wipebr',
        'slideleft', 'slideright', 'slideup', 'slidedown',
        'smoothleft', 'smoothright', 'smoothup', 'smoothdown',
        'circlecrop', 'rectcrop', 'circleopen', 'circleclose',
        'vertopen', 'vertclose', 'horzopen', 'horzclose',
        'radial', 'pixelize', 'diagtl', 'diagtr', 'diagbl', 'diagbr',
        'hlslice', 'hrslice', 'vuslice', 'vdslice',
        'hblur', 'squeezeh', 'squeezev', 'zoomin',
        'coverleft', 'coverright', 'coverup', 'coverdown',
        'revealleft', 'revealright', 'revealup', 'revealdown',
    })

    @classmethod
    def _resolve_transition(cls, transition: Optional[str]) -> str:
        """Map a friendly transition name to a valid ffmpeg xfade transition."""
        name = (transition or '').strip().lower()
        if name in cls.TRANSITION_TYPES:
            return cls.TRANSITION_TYPES[name]
        if name in cls.XFADE_TRANSITIONS:
            return name
        return 'fade'

    @staticmethod
    def _escape_filter_path(path) -> str:
        """Escape a file path for use as a filter option inside -filter_complex.

        FFmpeg parses filtergraphs at two levels, each with its own escaping:
        1. filter option value: \\ ' : are special
        2. filtergraph description: \\ ' [ ] , ; are special
        Escaping both levels lets paths containing apostrophes, colons,
        commas or brackets (e.g. "Why Earth's Core Is Hot") work.
        """
        raw = str(Path(path).resolve()).replace('\\', '/')
        level1 = ''.join('\\' + c if c in "\\':" else c for c in raw)
        return ''.join('\\' + c if c in "\\'[],;" else c for c in level1)

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """Initialize effects engine.

        Args:
            ffmpeg_path: Path to FFmpeg executable
            ffprobe_path: Path to FFprobe executable
        """
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def apply_crossfade(self, clip1: Path, clip2: Path, output: Path,
                       duration: float = 0.5) -> Path:
        """Apply crossfade transition between two clips.

        Args:
            clip1: First video clip
            clip2: Second video clip
            output: Output path for combined video
            duration: Transition duration in seconds (0.3-1.0 recommended)

        Returns:
            Path to output video with transition

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        clip1 = Path(clip1)
        clip2 = Path(clip2)
        output = Path(output)

        # Get durations of both clips
        duration1 = self._get_duration(clip1)

        # Calculate offset (start transition at end of first clip)
        offset = duration1 - duration

        # FFmpeg xfade filter for smooth crossfades
        cmd = [
            self.ffmpeg_path,
            '-i', str(clip1),
            '-i', str(clip2),
            '-filter_complex',
            f'[0:v][1:v]xfade=transition=fade:duration={duration}:offset={offset}[outv]',
            '-map', '[outv]',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-threads', str(settings.ffmpeg_threads),
            '-y',
            str(output)
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output

    def concat_with_transitions(self, clips: List[Path], output: Path,
                               transition: str = 'crossfade',
                               duration: float = 0.5) -> Path:
        """Concatenate multiple clips with transitions between them.

        Args:
            clips: List of video clip paths
            output: Output path
            transition: Transition type ('crossfade', 'dissolve', etc.)
            duration: Transition duration in seconds

        Returns:
            Path to final video

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        output = Path(output)

        if len(clips) == 1:
            # Single clip, just copy
            import shutil
            shutil.copy(clips[0], output)
            return output

        if len(clips) == 0:
            raise ValueError("No clips provided for concatenation")

        # Get durations for all clips
        clip_durations = [self._get_duration(clip) for clip in clips]

        # Build complex FFmpeg filtergraph with xfade between each pair
        filter_complex = self._build_transition_filtergraph(
            len(clips), clip_durations, transition, duration
        )

        # Build command with all inputs
        cmd = [self.ffmpeg_path]
        for clip in clips:
            cmd.extend(['-i', str(clip)])

        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y',
            str(output)
        ])

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output

    def _build_transition_filtergraph(self, num_clips: int,
                                     clip_durations: List[float],
                                     transition: str, duration: float,
                                     output_tag: str = "[outv]") -> str:
        """Build FFmpeg filtergraph for multiple transitions.

        Args:
            num_clips: Number of video clips
            clip_durations: Duration of each clip in seconds
            transition: Transition type
            duration: Transition duration in seconds
            output_tag: Tag for the final output stream (default [outv])

        Returns:
            FFmpeg filtergraph string

        Example for 3 clips:
        [0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v01];
        [v01][2:v]xfade=transition=fade:duration=0.5:offset=8.5[outv]
        """
        if num_clips < 2:
            return f"[0:v]copy{output_tag}"

        transition_type = self._resolve_transition(transition)
        filters = []
        cumulative_offset = 0.0

        for i in range(num_clips - 1):
            # Calculate offset for this transition
            # Offset is cumulative duration of all previous clips minus overlaps
            cumulative_offset += clip_durations[i] - duration

            if i == 0:
                input1 = f"[0:v]"
                input2 = f"[{i+1}:v]"
            else:
                input1 = f"[v{i-1}{i}]"
                input2 = f"[{i+1}:v]"

            if i == num_clips - 2:
                out = output_tag
            else:
                out = f"[v{i}{i+1}]"

            filters.append(
                f"{input1}{input2}xfade=transition={transition_type}:"
                f"duration={duration}:offset={cumulative_offset}{out}"
            )

        return ";".join(filters)

    def apply_color_grade(self, input_video: Path, output_video: Path,
                         preset: str = 'balanced',
                         custom_lut: Optional[Path] = None) -> Path:
        """Apply color grading to video for consistent look.

        Args:
            input_video: Input video file
            output_video: Output graded video
            preset: Grading preset ('balanced', 'warm', 'cool', 'vibrant', 'cinematic')
            custom_lut: Optional path to custom LUT file (.cube format)

        Returns:
            Path to graded video

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        input_video = Path(input_video)
        output_video = Path(output_video)

        if custom_lut and Path(custom_lut).exists():
            # Use custom LUT file
            lut_path = str(Path(custom_lut).resolve()).replace('\\', '/')
            filter_str = f"lut3d='{lut_path}'"
        else:
            # Use preset filters
            filter_str = self._get_color_preset_filter(preset)

        cmd = [
            self.ffmpeg_path,
            '-i', str(input_video),
            '-vf', filter_str,
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_video

    def get_color_filter(self, preset: str = 'balanced',
                        custom_lut: Optional[Path] = None) -> str:
        """Get FFmpeg filter string for color grading (for use in combined pipelines).

        Args:
            preset: Color preset name
            custom_lut: Optional path to custom LUT file

        Returns:
            FFmpeg filter string suitable for appending to -vf chains
        """
        if custom_lut and Path(custom_lut).exists():
            lut_path = str(Path(custom_lut).resolve()).replace('\\', '/')
            return f"lut3d='{lut_path}'"
        return self._get_color_preset_filter(preset)

    def _get_color_preset_filter(self, preset: str) -> str:
        """Get FFmpeg filter string for color preset.

        Presets adjust:
        - Contrast (eq=contrast)
        - Brightness (eq=brightness)
        - Saturation (eq=saturation)

        Args:
            preset: Color preset name

        Returns:
            FFmpeg filter string
        """
        presets = {
            'balanced': (
                # Normalize to neutral, consistent look
                'eq=contrast=1.1:brightness=0.02:saturation=1.05'
            ),
            'warm': (
                # Warm, inviting (YouTube lifestyle videos)
                'eq=contrast=1.15:brightness=0.05:saturation=1.15'
            ),
            'cool': (
                # Cool, modern tech look
                'eq=contrast=1.2:brightness=-0.02:saturation=1.1'
            ),
            'vibrant': (
                # Punchy, saturated (TikTok/Reels style)
                'eq=contrast=1.25:brightness=0.05:saturation=1.3'
            ),
            'cinematic': (
                # Film-like, desaturated
                'eq=contrast=1.3:brightness=-0.05:saturation=0.9'
            )
        }

        return presets.get(preset, presets['balanced'])

    def apply_subscribe_overlay(self, input_video: Path, subscribe_video: Path,
                                output_video: Path, orientation: str = 'landscape',
                                scale: float = None, margin_bottom: int = 50,
                                chromakey_color: str = '0x60FA01',
                                chromakey_similarity: float = 0.25,
                                chromakey_blend: float = 0.18) -> Path:
        """Overlay a green-screen video (e.g. subscribe button) onto the main video.

        The overlay appears centered at the midpoint of the video duration,
        positioned at bottom-center. Uses chromakey (YUV-space) for robust
        green screen removal from compressed video.

        Args:
            input_video: Main video file (with audio)
            subscribe_video: Green-screen overlay video
            output_video: Output path
            orientation: Video orientation for default scale calculation
            scale: Overlay width as fraction of video width (None = auto per orientation)
            margin_bottom: Pixels from bottom edge
            chromakey_color: Green screen color in hex (e.g. '0x60FA01')
            chromakey_similarity: Chromakey similarity threshold (0.01-1.0)
            chromakey_blend: Chromakey blend/smoothness (0.0-1.0)

        Returns:
            Path to output video
        """
        from video_assembler import get_resolution

        input_video = Path(input_video)
        subscribe_video = Path(subscribe_video)
        output_video = Path(output_video)

        video_duration = self._get_duration(input_video)
        subscribe_duration = self._get_duration(subscribe_video)

        # Center the overlay at the midpoint of the video
        start_time = (video_duration / 2) - (subscribe_duration / 2)
        end_time = start_time + subscribe_duration
        start_time = max(0, start_time)
        end_time = min(video_duration, end_time)

        # Determine scale based on orientation if not explicitly set
        if scale is None or scale <= 0:
            orientation_scales = {
                'landscape': 0.60,
                'portrait': 0.75,
                'square': 0.65,
            }
            scale = orientation_scales.get(orientation, 0.60)

        video_width, _ = get_resolution(orientation)
        overlay_width = int(video_width * scale)

        # colorkey (RGB-space) is more precise than chromakey (YUV-space):
        # chromakey was eating white pixels because YUV green is close to white.
        # Scale first with lanczos, then colorkey with blend for soft edges.
        # Use setpts to shift subscribe frames so they arrive at the overlay
        # start time. eof_action=pass makes overlay disappear when it ends.
        # No -stream_loop needed — we play subscribe.mp4 exactly once.
        filter_complex = (
            f"[1:v]scale={overlay_width}:-1:flags=lanczos,"
            f"colorkey={chromakey_color}:{chromakey_similarity}:{chromakey_blend},"
            f"format=rgba,"
            f"setpts=PTS+{start_time:.3f}/TB[ovr];"
            f"[0:v][ovr]overlay=x=(W-w)/2:y=H-h-{margin_bottom}"
            f":eof_action=pass[outv]"
        )

        cmd = [
            self.ffmpeg_path,
            '-i', str(input_video),
            '-i', str(subscribe_video),
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', '0:a',
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_video

    def normalize_white_balance(self, input_video: Path, output_video: Path) -> Path:
        """Normalize white balance to neutral daylight (6500K).

        Args:
            input_video: Input video
            output_video: Output normalized video

        Returns:
            Path to output video

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        input_video = Path(input_video)
        output_video = Path(output_video)

        cmd = [
            self.ffmpeg_path,
            '-i', str(input_video),
            '-vf', 'eq=contrast=1.1:brightness=0.02:saturation=1.05',
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-threads', str(settings.ffmpeg_threads),
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_video

    def _get_duration(self, video_file: Path) -> float:
        """Get video duration in seconds using ffprobe.

        Args:
            video_file: Path to video file

        Returns:
            Duration in seconds

        Raises:
            subprocess.CalledProcessError: If ffprobe command fails
        """
        cmd = [
            self.ffprobe_path,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(video_file)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())

    def apply_fade_out(self, input_video: Path, output_video: Path,
                       fade_duration: float = 1.0) -> Path:
        """Apply a fade-to-black at the end of the video (both video and audio).

        Args:
            input_video: Input video file (must have audio)
            output_video: Output video with fade-out
            fade_duration: Duration of the fade in seconds

        Returns:
            Path to output video
        """
        input_video = Path(input_video)
        output_video = Path(output_video)

        video_duration = self._get_duration(input_video)
        fade_start = max(0, video_duration - fade_duration)

        cmd = [
            self.ffmpeg_path,
            '-i', str(input_video),
            '-vf', f'fade=t=out:st={fade_start:.3f}:d={fade_duration}',
            '-af', f'afade=t=out:st={fade_start:.3f}:d={fade_duration}',
            '-c:v', 'libx264', '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-threads', str(settings.ffmpeg_threads),
            '-c:a', 'aac', '-b:a', '192k',
            '-y', str(output_video)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_video

    def finalize_video(self, video_no_audio: Path, audio_path: Path,
                       output_path: Path,
                       subtitle_path: Path = None,
                       subscribe_video: Path = None,
                       subscribe_params: dict = None,
                       fade_duration: float = 0,
                       audio_pad_duration: float = 0) -> Path:
        """Mux audio and apply overlay + subtitles + fade in a single FFmpeg pass.

        Combines what used to be 3-4 separate re-encode passes into one.
        If no effects are needed, falls back to a fast mux with -c:v copy.

        Args:
            video_no_audio: Video file without audio (from concat step)
            audio_path: Final audio file (narration, optionally mixed with music)
            output_path: Where to write the finished video
            subtitle_path: Optional ASS subtitle file to burn in
            subscribe_video: Optional green-screen subscribe button video
            subscribe_params: Dict with overlay settings (orientation, scale,
                              margin_bottom, chromakey_color, chromakey_similarity,
                              chromakey_blend)
            fade_duration: Fade-to-black duration in seconds (0 = no fade)
            audio_pad_duration: Seconds of silence appended to the audio so an
                outro (freeze-frame) longer than the narration is not cut by
                -shortest (0 = no padding). Same meaning as in
                concat_and_finalize, so re-renders match the first render.

        Returns:
            Path to output video
        """
        video_no_audio = Path(video_no_audio)
        audio_path = Path(audio_path)
        output_path = Path(output_path)

        needs_overlay = subscribe_video is not None and Path(subscribe_video).exists()
        needs_subtitles = subtitle_path is not None
        needs_fade = fade_duration > 0
        needs_pad = (audio_pad_duration or 0) > 0
        needs_effects = needs_overlay or needs_subtitles or needs_fade or needs_pad

        if not needs_effects:
            # Fast path: just mux audio, no re-encoding
            cmd = [
                self.ffmpeg_path,
                '-i', str(video_no_audio),
                '-i', str(audio_path),
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k',
                '-map', '0:v', '-map', '1:a',
                '-shortest',
                '-movflags', '+faststart',
                '-y', str(output_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            return output_path

        video_duration = self._get_duration(video_no_audio)

        # Output length with -shortest = min(video, padded audio). The fade
        # must land in the last fade_duration seconds of the actual output.
        final_duration = video_duration
        try:
            audio_duration = self._get_duration(audio_path)
            if audio_duration > 0:
                final_duration = min(video_duration,
                                     audio_duration + max(0.0, audio_pad_duration or 0))
        except Exception:
            pass

        # --- Build inputs ---
        cmd = [self.ffmpeg_path]
        cmd.extend(['-i', str(video_no_audio)])   # [0] video (no audio)
        cmd.extend(['-i', str(audio_path)])        # [1] audio
        if needs_overlay:
            cmd.extend(['-i', str(subscribe_video)])  # [2] subscribe video

        # --- Build video filter chain ---
        if needs_overlay:
            p = subscribe_params or {}
            from video_assembler import get_resolution

            sub_dur = self._get_duration(subscribe_video)
            start = max(0, (video_duration / 2) - (sub_dur / 2))

            ori = p.get('orientation', 'landscape')
            sc = p.get('scale')
            if not sc or sc <= 0:
                sc = {'landscape': 0.60, 'portrait': 0.75, 'square': 0.65}.get(ori, 0.60)
            w, _ = get_resolution(ori)
            ow = int(w * sc)
            mb = p.get('margin_bottom', 50)
            ck_color = p.get('chromakey_color', '0x60FA01')
            ck_sim = p.get('chromakey_similarity', 0.25)
            ck_blend = p.get('chromakey_blend', 0.18)

            # Overlay: scale + colorkey subscribe, overlay onto main video
            overlay_chain = (
                f"[2:v]scale={ow}:-1:flags=lanczos,"
                f"colorkey={ck_color}:{ck_sim}:{ck_blend},"
                f"format=rgba,"
                f"setpts=PTS+{start:.3f}/TB[ovr];"
                f"[0:v][ovr]overlay=x=(W-w)/2:y=H-h-{mb}:eof_action=pass"
            )
        else:
            overlay_chain = None

        # Subsequent filters (appended after overlay or directly on [0:v])
        post_filters = []
        if needs_subtitles:
            post_filters.append(f"ass=filename={self._escape_filter_path(subtitle_path)}")
        if needs_fade:
            fade_start = max(0, final_duration - fade_duration)
            post_filters.append(f"fade=t=out:st={fade_start:.3f}:d={fade_duration}")

        # Combine into complete video filter
        if overlay_chain:
            if post_filters:
                video_filter = overlay_chain + "," + ",".join(post_filters) + "[outv]"
            else:
                video_filter = overlay_chain + "[outv]"
        else:
            # No overlay — chain directly from input
            video_filter = "[0:v]" + (",".join(post_filters) or "null") + "[outv]"

        # --- Build audio filter: optional silence padding (outro) + fade ---
        audio_filters = []
        if needs_pad:
            audio_filters.append(f"apad=pad_dur={audio_pad_duration:.3f}")
        if needs_fade:
            fade_start = max(0, final_duration - fade_duration)
            audio_filters.append(f"afade=t=out:st={fade_start:.3f}:d={fade_duration}")
        if audio_filters:
            filter_complex = video_filter + ";[1:a]" + ",".join(audio_filters) + "[outa]"
            audio_map = ['-map', '[outa]']
        else:
            filter_complex = video_filter
            audio_map = ['-map', '1:a']
        audio_codec = ['-c:a', 'aac', '-b:a', '192k']

        # --- Assemble final command ---
        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            *audio_map,
            '-c:v', 'libx264', '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            *audio_codec,
            '-shortest',
            '-movflags', '+faststart',
            '-y', str(output_path)
        ])

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def concat_and_finalize(self, clips: List[Path], audio_path: Path,
                            output_path: Path,
                            transition: str = 'crossfade',
                            transition_duration: float = 0.5,
                            subtitle_path: Path = None,
                            subscribe_video: Path = None,
                            subscribe_params: dict = None,
                            fade_duration: float = 0,
                            audio_pad_duration: float = 0) -> Path:
        """Concatenate clips with transitions AND apply effects in a single FFmpeg pass.

        Combines concat_with_transitions + finalize_video into one encode,
        eliminating a full decode+encode cycle (~20-30s savings).

        Args:
            clips: List of video clip paths (no audio)
            audio_path: Final audio file (narration, optionally mixed with music)
            output_path: Where to write the finished video
            transition: Transition type ('crossfade', 'dissolve', etc.)
            transition_duration: Transition duration in seconds
            subtitle_path: Optional ASS subtitle file to burn in
            subscribe_video: Optional green-screen subscribe button video
            subscribe_params: Dict with overlay settings
            fade_duration: Fade-to-black duration in seconds (0 = no fade)
            audio_pad_duration: Seconds of silence appended to the audio so an
                outro (freeze-frame) longer than the narration is not cut by
                -shortest (0 = no padding)

        Returns:
            Path to final output video
        """
        output_path = Path(output_path)
        audio_path = Path(audio_path)

        if not clips:
            raise ValueError("No clips provided")

        if len(clips) == 1:
            # Single clip — fall back to finalize_video (no xfade needed)
            return self.finalize_video(
                clips[0], audio_path, output_path,
                subtitle_path=subtitle_path,
                subscribe_video=subscribe_video,
                subscribe_params=subscribe_params,
                fade_duration=fade_duration,
                audio_pad_duration=audio_pad_duration,
            )

        needs_overlay = subscribe_video is not None and Path(subscribe_video).exists()
        needs_subtitles = subtitle_path is not None
        needs_fade = fade_duration > 0
        needs_pad = audio_pad_duration > 0
        needs_effects = needs_overlay or needs_subtitles or needs_fade or needs_pad

        # Get durations for xfade offsets
        clip_durations = [self._get_duration(clip) for clip in clips]

        # Calculate total video duration after transitions
        total_duration = sum(clip_durations) - transition_duration * (len(clips) - 1)

        # Output length with -shortest = min(video, padded audio). The fade
        # must land in the last fade_duration seconds of the actual output.
        final_duration = total_duration
        if needs_fade or needs_pad:
            try:
                audio_duration = self._get_duration(audio_path)
                if audio_duration > 0:
                    final_duration = min(total_duration,
                                         audio_duration + max(0.0, audio_pad_duration))
            except Exception:
                pass

        # Build xfade filtergraph — output to intermediate tag if more filters follow
        if needs_effects:
            xfade_out_tag = "[xfade_out]"
        else:
            xfade_out_tag = "[outv]"

        xfade_filter = self._build_transition_filtergraph(
            len(clips), clip_durations, transition, transition_duration,
            output_tag=xfade_out_tag
        )

        # --- Build inputs ---
        cmd = [self.ffmpeg_path]
        for clip in clips:
            cmd.extend(['-i', str(clip)])

        # Audio input index = number of clips
        audio_idx = len(clips)
        cmd.extend(['-i', str(audio_path)])

        # Subscribe video input (optional)
        subscribe_idx = None
        if needs_overlay:
            subscribe_idx = audio_idx + 1
            cmd.extend(['-i', str(subscribe_video)])

        if not needs_effects:
            # No effects — xfade filter already outputs [outv], just mux audio
            cmd.extend([
                '-filter_complex', xfade_filter,
                '-map', '[outv]',
                '-map', f'{audio_idx}:a',
                '-c:v', 'libx264', '-preset', settings.ffmpeg_preset,
                '-crf', str(settings.ffmpeg_crf),
                '-pix_fmt', 'yuv420p',
                '-threads', str(settings.ffmpeg_threads),
                '-c:a', 'aac', '-b:a', '192k',
                '-shortest',
                '-profile:v', 'high',
                '-level', '4.0',
                '-movflags', '+faststart',
                '-y', str(output_path)
            ])
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            return output_path

        # --- Build post-xfade filter chain ---
        filter_parts = [xfade_filter]

        # Subscribe overlay
        if needs_overlay:
            p = subscribe_params or {}
            from video_assembler import get_resolution

            sub_dur = self._get_duration(subscribe_video)
            start = max(0, (total_duration / 2) - (sub_dur / 2))

            ori = p.get('orientation', 'landscape')
            sc = p.get('scale')
            if not sc or sc <= 0:
                sc = {'landscape': 0.60, 'portrait': 0.75, 'square': 0.65}.get(ori, 0.60)
            w, _ = get_resolution(ori)
            ow = int(w * sc)
            mb = p.get('margin_bottom', 50)
            ck_color = p.get('chromakey_color', '0x60FA01')
            ck_sim = p.get('chromakey_similarity', 0.25)
            ck_blend = p.get('chromakey_blend', 0.18)

            overlay_filter = (
                f"[{subscribe_idx}:v]scale={ow}:-1:flags=lanczos,"
                f"colorkey={ck_color}:{ck_sim}:{ck_blend},"
                f"format=rgba,"
                f"setpts=PTS+{start:.3f}/TB[ovr];"
                f"[xfade_out][ovr]overlay=x=(W-w)/2:y=H-h-{mb}:eof_action=pass"
            )
            filter_parts.append(overlay_filter)
            # After overlay, the current video stream is unnamed (inline)
            current_input = None  # inline continuation
        else:
            # No overlay — continue from [xfade_out]
            current_input = "[xfade_out]"

        # Subtitles + fade (appended as comma-separated filters)
        post_filters = []
        if needs_subtitles:
            post_filters.append(f"ass=filename={self._escape_filter_path(subtitle_path)}")
        if needs_fade:
            fade_start = max(0, final_duration - fade_duration)
            post_filters.append(f"fade=t=out:st={fade_start:.3f}:d={fade_duration}")

        # Assemble the complete filter_complex
        if needs_overlay:
            # Overlay is inline (no semicolon before post_filters)
            if post_filters:
                video_chain = ";".join(filter_parts) + "," + ",".join(post_filters) + "[outv]"
            else:
                video_chain = ";".join(filter_parts) + "[outv]"
        else:
            # No overlay — chain from [xfade_out]
            if post_filters:
                video_chain = xfade_filter + ";" + current_input + ",".join(post_filters) + "[outv]"
            else:
                video_chain = xfade_filter + ";" + current_input + "null[outv]"

        # Audio: optional silence padding (for the outro) + fade
        audio_filters = []
        if needs_pad:
            audio_filters.append(f"apad=pad_dur={audio_pad_duration:.3f}")
        if needs_fade:
            fade_start = max(0, final_duration - fade_duration)
            audio_filters.append(f"afade=t=out:st={fade_start:.3f}:d={fade_duration}")
        if audio_filters:
            filter_complex = video_chain + f";[{audio_idx}:a]" + ",".join(audio_filters) + "[outa]"
            audio_map = ['-map', '[outa]']
        else:
            filter_complex = video_chain
            audio_map = ['-map', f'{audio_idx}:a']

        # --- Final command ---
        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            *audio_map,
            '-c:v', 'libx264', '-preset', settings.ffmpeg_preset,
            '-crf', str(settings.ffmpeg_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y', str(output_path)
        ])

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path
