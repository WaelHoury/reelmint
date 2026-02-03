"""Music selector - automatically choose background music based on video topic."""

from pathlib import Path
from typing import Optional
import random


class MusicSelector:
    """Select background music based on video topic/mood."""

    MOOD_KEYWORDS = {
        'energetic': ['sport', 'fitness', 'action', 'fast', 'exciting', 'workout', 'exercise', 'running', 'game'],
        'calm': ['meditation', 'nature', 'relaxing', 'peaceful', 'ocean', 'forest', 'sleep', 'quiet', 'serene'],
        'inspiring': ['success', 'motivation', 'achievement', 'journey', 'dream', 'goal', 'inspire', 'uplift'],
        'neutral': []  # Default fallback
    }

    def __init__(self, music_dir: Path = Path("music")):
        """Initialize music selector.

        Args:
            music_dir: Directory containing music files organized by mood subdirectories
        """
        self.music_dir = Path(music_dir)

    def select_music(self, topic: str, mood: Optional[str] = None) -> Path:
        """Select appropriate music file for video topic.

        Args:
            topic: Video topic/prompt (used for auto-detection if mood not specified)
            mood: Optional mood override ('energetic', 'calm', 'inspiring', 'neutral')
                 If None, mood is auto-detected from topic keywords

        Returns:
            Path to selected music file

        Raises:
            ValueError: If no music files found in mood directory
        """
        # Auto-detect mood from topic if not specified
        if mood is None or mood == 'auto':
            mood = self._detect_mood(topic)

        # Get music files for mood
        mood_dir = self.music_dir / mood
        if not mood_dir.exists():
            # Fallback to neutral
            mood_dir = self.music_dir / 'neutral'

        # Find all music files in the mood directory
        music_files = list(mood_dir.glob('*.mp3')) + list(mood_dir.glob('*.wav'))

        if not music_files:
            raise ValueError(
                f"No music files found in {mood_dir}. "
                f"Please add .mp3 or .wav files to {self.music_dir}/{mood}/"
            )

        # Random selection from category for variety
        selected = random.choice(music_files)
        return selected

    def _detect_mood(self, topic: str) -> str:
        """Detect mood from topic keywords.

        Args:
            topic: Video topic/prompt

        Returns:
            Detected mood category ('energetic', 'calm', 'inspiring', or 'neutral')
        """
        topic_lower = topic.lower()

        # Check each mood's keywords
        for mood, keywords in self.MOOD_KEYWORDS.items():
            if any(keyword in topic_lower for keyword in keywords):
                return mood

        # Default to neutral if no keywords match
        return 'neutral'

    def list_available_music(self) -> dict:
        """List all available music files organized by mood.

        Returns:
            Dictionary mapping mood names to lists of music file paths
        """
        available = {}

        for mood in ['energetic', 'calm', 'inspiring', 'neutral']:
            mood_dir = self.music_dir / mood
            if mood_dir.exists():
                files = list(mood_dir.glob('*.mp3')) + list(mood_dir.glob('*.wav'))
                available[mood] = files
            else:
                available[mood] = []

        return available
