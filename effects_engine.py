"""Video effects engine for transitions, filters, and enhancements."""

from pathlib import Path
from typing import List, Literal, Optional
import subprocess


class EffectsEngine:
    """Handles video transitions and visual effects."""

    TRANSITION_TYPES = {
        'crossfade': 'fade',      # Smooth blend between clips
        'dissolve': 'dissolve',   # Similar to crossfade
        'wipe': 'wipe',          # Directional wipe
        'slide': 'slide'          # Slide transition
    }

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
            '-preset', 'medium',
            '-crf', '23',
            '-y',
            str(output)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
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
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',  # Ensure Windows compatibility
            '-profile:v', 'high',    # H.264 High Profile for better compatibility
            '-level', '4.0',         # H.264 Level 4.0 for wide compatibility
            '-movflags', '+faststart',  # Enable streaming/fast start
            '-y',
            str(output)
        ])

        subprocess.run(cmd, check=True, capture_output=True)
        return output

    def _build_transition_filtergraph(self, num_clips: int,
                                     clip_durations: List[float],
                                     transition: str, duration: float) -> str:
        """Build FFmpeg filtergraph for multiple transitions.

        Args:
            num_clips: Number of video clips
            clip_durations: Duration of each clip in seconds
            transition: Transition type
            duration: Transition duration in seconds

        Returns:
            FFmpeg filtergraph string

        Example for 3 clips:
        [0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v01];
        [v01][2:v]xfade=transition=fade:duration=0.5:offset=8.5[outv]
        """
        if num_clips < 2:
            return "[0:v]copy[outv]"

        transition_type = self.TRANSITION_TYPES.get(transition, 'fade')
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
                output_tag = "[outv]"
            else:
                output_tag = f"[v{i}{i+1}]"

            filters.append(
                f"{input1}{input2}xfade=transition={transition_type}:"
                f"duration={duration}:offset={cumulative_offset}{output_tag}"
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
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',  # Ensure Windows compatibility
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
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
                                chromakey_blend: float = 0.12) -> Path:
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
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-profile:v', 'high',
            '-level', '4.0',
            '-movflags', '+faststart',
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
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
            '-preset', 'medium',
            '-crf', '23',
            '-y',
            str(output_video)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
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

        video_duration = self._get_video_duration(input_video)
        fade_start = max(0, video_duration - fade_duration)

        cmd = [
            self.ffmpeg_path,
            '-i', str(input_video),
            '-vf', f'fade=t=out:st={fade_start:.3f}:d={fade_duration}',
            '-af', f'afade=t=out:st={fade_start:.3f}:d={fade_duration}',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k',
            '-y', str(output_video)
        ]

        subprocess.run(cmd, capture_output=True, check=True)
        return output_video
