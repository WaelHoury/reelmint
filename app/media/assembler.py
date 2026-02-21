"""FFmpeg-based video assembly operations.

Critical: All normalization must happen BEFORE concat operations.
Concat with -c copy requires identical formats across all inputs.
"""

import subprocess
from pathlib import Path
from typing import List, Tuple
import os

from app.core.config import settings

# Resolution mapping for video orientations
ORIENTATION_RESOLUTION = {
    'landscape': (1920, 1080),
    'portrait': (1080, 1920),
    'square': (1080, 1080),
}


def get_resolution(orientation: str) -> Tuple[int, int]:
    """Get (width, height) for a video orientation."""
    return ORIENTATION_RESOLUTION.get(orientation, (1920, 1080))


class VideoAssembler:
    """Handles all FFmpeg video/audio operations."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """Initialize assembler with FFmpeg paths.

        Args:
            ffmpeg_path: Path to ffmpeg executable
            ffprobe_path: Path to ffprobe executable
        """
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self._duration_cache = {}

    def get_duration(self, media_path: Path) -> float:
        """Get duration of video or audio file using ffprobe.

        Args:
            media_path: Path to media file

        Returns:
            Duration in seconds

        Raises:
            subprocess.CalledProcessError: If ffprobe fails
        """
        cache_key = str(media_path)
        if cache_key in self._duration_cache:
            return self._duration_cache[cache_key]

        cmd = [
            self.ffprobe, '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(media_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
        self._duration_cache[cache_key] = duration
        return duration

    def normalize_video(self, input_path: Path, output_path: Path,
                        orientation: str = 'landscape') -> Path:
        """Normalize video to standard format for reliable concatenation.

        Target format:
        - Resolution: orientation-dependent (e.g. 1920x1080, 1080x1920, 1080x1080)
        - FPS: 30
        - Codec: libx264
        - Pixel format: yuv420p
        - Preset: fast (good speed/quality balance)

        Args:
            input_path: Source video file
            output_path: Destination for normalized video
            orientation: Video orientation ('landscape', 'portrait', 'square')

        Returns:
            Path to normalized video

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        w, h = get_resolution(orientation)

        cmd = [
            self.ffmpeg, '-i', str(input_path),
            '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
            '-r', '30',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_intermediate_preset,
            '-crf', str(settings.ffmpeg_intermediate_crf),
            '-threads', str(settings.ffmpeg_threads),
            '-y',  # Overwrite output
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def normalize_audio(self, input_path: Path, output_path: Path) -> Path:
        """Normalize audio to standard format for reliable concatenation.

        Target format:
        - Sample rate: 48000 Hz
        - Channels: 2 (stereo)
        - Codec: pcm_s16le

        Args:
            input_path: Source audio file
            output_path: Destination for normalized audio

        Returns:
            Path to normalized audio

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        cmd = [
            self.ffmpeg, '-i', str(input_path),
            '-ar', '48000',
            '-ac', '2',
            '-c:a', 'pcm_s16le',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def trim_video(self, input_path: Path, output_path: Path,
                   start_sec: float, duration_sec: float) -> Path:
        """Trim video to specific duration with re-encoding for frame accuracy.

        Note: Uses re-encoding (not -c copy) for frame-accurate cuts.
        Input should already be normalized for consistent quality.
        STRIPS AUDIO - narration will be added separately.

        Args:
            input_path: Source video (should be normalized)
            output_path: Destination for trimmed video (no audio)
            start_sec: Start time in seconds
            duration_sec: Duration to extract in seconds

        Returns:
            Path to trimmed video (without audio)

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        cmd = [
            self.ffmpeg,
            '-ss', str(start_sec),
            '-i', str(input_path),
            '-t', str(duration_sec),
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_intermediate_preset,
            '-crf', str(settings.ffmpeg_intermediate_crf),
            '-pix_fmt', 'yuv420p',
            '-threads', str(settings.ffmpeg_threads),
            '-an',  # Strip audio - narration will be added later
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def process_clip(self, input_path: Path, output_path: Path,
                     duration_sec: float, color_grade_filter: str = None,
                     start_sec: float = 0,
                     orientation: str = 'landscape') -> Path:
        """Normalize, optionally color grade, and trim in a single FFmpeg encode.

        Combines what was previously 3 separate encodes (normalize_video +
        apply_color_grade + trim_video) into one pass for ~3x speedup.

        Args:
            input_path: Raw downloaded video file
            output_path: Destination for processed video (no audio)
            duration_sec: Duration to extract in seconds
            color_grade_filter: Optional FFmpeg filter string for color grading
            start_sec: Start time in seconds
            orientation: Video orientation ('landscape', 'portrait', 'square')

        Returns:
            Path to processed video (normalized, graded, trimmed, no audio)
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        w, h = get_resolution(orientation)

        vf_parts = [
            f'scale={w}:{h}:force_original_aspect_ratio=decrease',
            f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
        ]
        if color_grade_filter:
            vf_parts.append(color_grade_filter)

        vf_string = ','.join(vf_parts)

        cmd = [
            self.ffmpeg,
            '-ss', str(start_sec),
            '-i', str(input_path),
            '-t', str(duration_sec),
            '-vf', vf_string,
            '-r', '30',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-preset', settings.ffmpeg_intermediate_preset,
            '-crf', str(settings.ffmpeg_intermediate_crf),
            '-threads', str(settings.ffmpeg_threads),
            '-an',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def concat_videos(self, video_list: List[Path], output_path: Path) -> Path:
        """Concatenate multiple videos using concat demuxer.

        CRITICAL: All input videos MUST be normalized to identical format first.
        Uses -c copy for fast, lossless concatenation.

        Args:
            video_list: List of video files (must all be normalized)
            output_path: Destination for concatenated video

        Returns:
            Path to concatenated video

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
            ValueError: If video_list is empty
        """
        if not video_list:
            raise ValueError("video_list cannot be empty")

        output_path = Path(output_path)

        # Create concat file with absolute paths
        concat_file = output_path.parent / f"{output_path.stem}_concat.txt"

        with open(concat_file, 'w', encoding='utf-8') as f:
            for video_path in video_list:
                abs_path = Path(video_path).resolve()
                # Use forward slashes even on Windows for FFmpeg compatibility
                path_str = str(abs_path).replace('\\', '/')
                f.write(f"file '{path_str}'\n")

        cmd = [
            self.ffmpeg,
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            '-y',
            str(output_path)
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        finally:
            # Clean up concat file
            if concat_file.exists():
                concat_file.unlink()

        return output_path

    def concat_videos_with_transitions(self, video_paths: List[Path],
                                       output_path: Path,
                                       transition: str = 'crossfade',
                                       transition_duration: float = 0.5) -> Path:
        """Concatenate videos with smooth transitions between clips.

        Uses the EffectsEngine to apply crossfade or other transitions.
        This is slower than concat_videos but produces professional-looking results.

        Args:
            video_paths: List of video files to concatenate
            output_path: Output video path
            transition: Transition type ('crossfade', 'dissolve', 'wipe', 'slide')
            transition_duration: Transition duration in seconds (0.3-1.0 recommended)

        Returns:
            Path to output video with transitions

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
            ValueError: If video_paths is empty
        """
        if not video_paths:
            raise ValueError("video_paths cannot be empty")

        from effects_engine import EffectsEngine

        effects = EffectsEngine(ffmpeg_path=self.ffmpeg, ffprobe_path=self.ffprobe)
        return effects.concat_with_transitions(
            video_paths, output_path, transition, transition_duration
        )

    def concat_audio(self, audio_list: List[Path], output_path: Path) -> Path:
        """Concatenate multiple audio files using concat demuxer.

        CRITICAL: All input audio MUST be normalized to identical format first.
        Uses -c copy for fast, lossless concatenation.

        Args:
            audio_list: List of audio files (must all be normalized)
            output_path: Destination for concatenated audio

        Returns:
            Path to concatenated audio

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
            ValueError: If audio_list is empty
        """
        if not audio_list:
            raise ValueError("audio_list cannot be empty")

        output_path = Path(output_path)

        # Create concat file with absolute paths
        concat_file = output_path.parent / f"{output_path.stem}_concat.txt"

        with open(concat_file, 'w', encoding='utf-8') as f:
            for audio_path in audio_list:
                abs_path = Path(audio_path).resolve()
                # Use forward slashes even on Windows for FFmpeg compatibility
                path_str = str(abs_path).replace('\\', '/')
                f.write(f"file '{path_str}'\n")

        cmd = [
            self.ffmpeg,
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
            '-y',
            str(output_path)
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        finally:
            # Clean up concat file
            if concat_file.exists():
                concat_file.unlink()

        return output_path

    def mux_audio(self, video_path: Path, audio_path: Path, output_path: Path) -> Path:
        """Add audio track to video file.

        Uses -shortest to match video duration to audio duration.
        Explicitly maps video from input 0 and audio from input 1.

        Args:
            video_path: Video file (should have no audio)
            audio_path: Audio file to add (TTS narration)
            output_path: Destination for final video with audio

        Returns:
            Path to final video

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
        """
        video_path = Path(video_path)
        audio_path = Path(audio_path)
        output_path = Path(output_path)

        cmd = [
            self.ffmpeg,
            '-i', str(video_path),
            '-i', str(audio_path),
            '-map', '0:v',  # Video stream from input 0
            '-map', '1:a',  # Audio stream from input 1 (TTS narration)
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def extract_frame(self, video_path: Path, timestamp: float, output_path: Path) -> Path:
        """Extract a single frame at specific timestamp.

        Args:
            video_path: Source video file
            timestamp: Time in seconds to extract frame
            output_path: Destination for frame image (JPG)

        Returns:
            Path to extracted frame

        Raises:
            subprocess.CalledProcessError: If ffmpeg fails
        """
        video_path = Path(video_path)
        output_path = Path(output_path)

        cmd = [
            self.ffmpeg,
            '-ss', str(timestamp),
            '-i', str(video_path),
            '-frames:v', '1',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path
