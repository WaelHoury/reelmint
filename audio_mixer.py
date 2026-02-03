"""Audio mixing engine for background music and narration."""

from pathlib import Path
from typing import Optional
import subprocess


class AudioMixer:
    """Mix narration with background music."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """Initialize audio mixer.

        Args:
            ffmpeg_path: Path to FFmpeg executable
            ffprobe_path: Path to FFprobe executable
        """
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def mix_with_background_music(self, narration_audio: Path,
                                  music_file: Path,
                                  output_audio: Path,
                                  music_volume: float = 0.15,
                                  narration_volume: float = 1.0,
                                  fade_in: float = 2.0,
                                  fade_out: float = 2.0) -> Path:
        """Mix narration with background music.

        Background music will loop if shorter than narration, with fade in/out.

        Args:
            narration_audio: Main narration audio file
            music_file: Background music file (will loop if needed)
            output_audio: Output mixed audio
            music_volume: Background music volume (0.1-0.3 recommended, 1.0 = same as narration)
            narration_volume: Narration volume (usually 1.0)
            fade_in: Music fade in duration in seconds
            fade_out: Music fade out duration in seconds

        Returns:
            Path to mixed audio file

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        narration_audio = Path(narration_audio)
        music_file = Path(music_file)
        output_audio = Path(output_audio)

        # Get narration duration to loop/trim music
        narration_duration = self._get_duration(narration_audio)

        # FFmpeg command with amix filter
        # - Loop music if needed
        # - Apply volume adjustments
        # - Mix both tracks
        cmd = [
            self.ffmpeg_path,
            '-i', str(narration_audio),
            '-stream_loop', '-1',  # Loop music indefinitely
            '-i', str(music_file),
            '-filter_complex',
            f'[1:a]afade=t=in:st=0:d={fade_in},'
            f'afade=t=out:st={narration_duration-fade_out}:d={fade_out},'
            f'volume={music_volume}[music];'
            f'[0:a]volume={narration_volume}[narration];'
            f'[narration][music]amix=inputs=2:duration=first:dropout_transition=2[outa]',
            '-map', '[outa]',
            '-t', str(narration_duration),
            '-c:a', 'aac',
            '-b:a', '192k',
            '-y',
            str(output_audio)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
        return output_audio

    def apply_volume_ducking(self, narration_audio: Path,
                            music_file: Path,
                            output_audio: Path,
                            duck_amount: float = -12.0) -> Path:
        """Apply volume ducking - reduce music when narration speaks.

        Uses FFmpeg sidechaincompress to automatically lower music volume
        when narration is present.

        Args:
            narration_audio: Narration track (drives the ducking)
            music_file: Music track (will be ducked)
            output_audio: Output file with narration + ducked music
            duck_amount: How much to reduce music in dB (-12 to -18 typical)

        Returns:
            Path to ducked audio

        Raises:
            subprocess.CalledProcessError: If FFmpeg command fails
        """
        narration_audio = Path(narration_audio)
        music_file = Path(music_file)
        output_audio = Path(output_audio)

        # Get narration duration
        narration_duration = self._get_duration(narration_audio)

        # Use FFmpeg sidechaincompress for dynamic ducking
        cmd = [
            self.ffmpeg_path,
            '-i', str(music_file),
            '-i', str(narration_audio),
            '-filter_complex',
            f'[0:a]aloop=loop=-1:size={int(narration_duration * 48000)}[music];'
            f'[music][1:a]sidechaincompress=threshold=0.02:ratio=4:attack=10:release=100[outa]',
            '-map', '[outa]',
            '-t', str(narration_duration),
            '-c:a', 'aac',
            '-b:a', '192k',
            '-y',
            str(output_audio)
        ]

        subprocess.run(cmd, check=True, capture_output=True)
        return output_audio

    def _get_duration(self, audio_file: Path) -> float:
        """Get audio duration in seconds using ffprobe.

        Args:
            audio_file: Path to audio file

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
            str(audio_file)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
