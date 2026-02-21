"""Text-to-speech generation using Piper TTS, Kokoro-82M, or Orpheus TTS with audio normalization."""

import subprocess
from pathlib import Path
import tempfile
from typing import Optional, Literal, List, Dict
import numpy as np


def transcribe_with_word_timestamps(audio_path: Path,
                                     whisper_model: str = "base") -> List[Dict]:
    """Extract word-level timestamps from audio using faster-whisper ASR.

    This provides accurate, audio-derived word timing instead of heuristic
    estimation from text. Falls back gracefully if faster-whisper is not installed.

    Args:
        audio_path: Path to WAV audio file
        whisper_model: Whisper model size ('tiny', 'base', 'small', 'medium')

    Returns:
        List of {'word': str, 'start': float, 'end': float} dicts,
        or empty list if ASR fails.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("    faster-whisper not installed, using heuristic timestamps")
        return []

    try:
        model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="en",
        )

        word_timestamps = []
        for segment in segments:
            if segment.words:
                for w in segment.words:
                    word_timestamps.append({
                        'word': w.word.strip(),
                        'start': w.start,
                        'end': w.end,
                    })

        return word_timestamps

    except Exception as e:
        print(f"    ASR failed ({e}), using heuristic timestamps")
        return []


class TTSGenerator:
    """Generates speech audio from text using Piper TTS, Kokoro-82M, or Orpheus TTS."""

    # Punctuation pause weights (in equivalent syllable units).
    # Kokoro adds natural pauses after these marks; this compensates
    # so word timestamps don't drift after commas/periods.
    PUNCTUATION_PAUSE_WEIGHT = {
        ',': 1.5,    # Comma: ~150-300ms pause
        '.': 2.5,    # Period: ~300-500ms pause
        '!': 2.5,    # Exclamation: ~300-500ms pause
        '?': 2.5,    # Question mark: ~300-500ms pause
        ';': 2.0,    # Semicolon: ~200-400ms pause
        ':': 2.0,    # Colon: ~200-400ms pause
    }

    def __init__(self,
                 tts_engine: Literal["piper", "kokoro", "orpheus", "chatterbox"] = "kokoro",
                 # Piper settings
                 piper_executable: str = "piper.exe",
                 model_path: str = None,
                 # Kokoro settings
                 kokoro_voice: str = "af_heart",
                 kokoro_speed: float = 1.0,
                 kokoro_lang: str = "a",  # 'a' = American English
                 # Orpheus settings
                 orpheus_voice: str = "tara",
                 # Chatterbox settings
                 chatterbox_exaggeration: float = 0.5,
                 chatterbox_cfg: float = 0.5,
                 chatterbox_voice_ref: str = "",
                 # Common settings
                 ffmpeg_path: str = "ffmpeg"):
        """Initialize TTS generator.

        Args:
            tts_engine: TTS engine to use ("piper", "kokoro", "orpheus", or "chatterbox")
            piper_executable: Path to Piper TTS executable (for Piper)
            model_path: Path to Piper voice model (.onnx file) (for Piper)
            kokoro_voice: Voice code for Kokoro (e.g., "af_heart", "am_michael")
            kokoro_speed: Speech speed for Kokoro (default 1.0, range 0.5-2.0)
            kokoro_lang: Language code for Kokoro ('a'=American, 'b'=British, etc.)
            orpheus_voice: Voice for Orpheus TTS (tara, leah, jess, leo, dan, mia, zac, zoe)
            chatterbox_exaggeration: Emotion intensity for Chatterbox (0.0-1.0, default 0.5)
            chatterbox_cfg: Voice adherence for Chatterbox (0.0-1.0, default 0.5)
            chatterbox_voice_ref: Path to reference audio file for voice cloning (empty = default voice)
            ffmpeg_path: Path to ffmpeg executable for normalization
        """
        self.engine = tts_engine
        self.ffmpeg = ffmpeg_path

        # Piper settings
        self.piper = piper_executable
        self.model = model_path

        # Kokoro settings
        self.kokoro_voice = kokoro_voice
        self.kokoro_speed = kokoro_speed
        self.kokoro_lang = kokoro_lang
        self._kokoro_pipeline = None  # Lazy initialization

        # Orpheus settings
        self.orpheus_voice = orpheus_voice
        self._orpheus_model = None  # Lazy initialization

        # Chatterbox settings
        self.chatterbox_exaggeration = chatterbox_exaggeration
        self.chatterbox_cfg = chatterbox_cfg
        self.chatterbox_voice_ref = chatterbox_voice_ref if chatterbox_voice_ref else None
        self._chatterbox_model = None  # Lazy initialization

        # Validate configuration
        if self.engine == "piper" and not model_path:
            raise ValueError("model_path must be specified for Piper TTS")

    def _init_kokoro(self):
        """Lazy initialization of Kokoro pipeline."""
        if self._kokoro_pipeline is None:
            try:
                from kokoro import KPipeline
                self._kokoro_pipeline = KPipeline(lang_code=self.kokoro_lang)
            except ImportError:
                raise ImportError(
                    "Kokoro TTS not installed. Install with: pip install kokoro>=0.9.4 soundfile"
                )

    def generate(self, text: str, output_path: Path) -> Path:
        """Generate speech audio from text.

        Args:
            text: Text to convert to speech
            output_path: Where to save final normalized audio

        Returns:
            Path to normalized WAV file

        Raises:
            subprocess.CalledProcessError: If TTS or FFmpeg fails
            ValueError: If text is empty
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        output_path = Path(output_path)

        # Generate raw audio
        raw_audio = self._generate_raw(text, output_path)

        # Normalize audio format for concatenation compatibility
        normalized_audio = self._normalize_audio(raw_audio, output_path)

        # Clean up raw audio if different from output
        if raw_audio != normalized_audio and raw_audio.exists():
            raw_audio.unlink()

        return normalized_audio

    def _generate_raw(self, text: str, output_path: Path) -> Path:
        """Generate raw audio using configured TTS engine.

        Args:
            text: Text to synthesize
            output_path: Base path for output

        Returns:
            Path to raw audio file

        Raises:
            subprocess.CalledProcessError: If TTS fails
        """
        if self.engine == "piper":
            return self._generate_piper(text, output_path)
        elif self.engine == "kokoro":
            return self._generate_kokoro(text, output_path)
        elif self.engine == "orpheus":
            return self._generate_orpheus(text, output_path)
        elif self.engine == "chatterbox":
            return self._generate_chatterbox(text, output_path)
        else:
            raise ValueError(f"Unknown TTS engine: {self.engine}")

    def _generate_piper(self, text: str, output_path: Path) -> Path:
        """Generate audio using Piper TTS.

        Args:
            text: Text to synthesize
            output_path: Base path for output

        Returns:
            Path to raw audio file

        Raises:
            subprocess.CalledProcessError: If Piper fails
        """
        raw_output = output_path.parent / f"{output_path.stem}_raw.wav"

        # Write text to temp file for cross-platform compatibility
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                          delete=False, suffix='.txt') as f:
            f.write(text)
            text_file = f.name

        try:
            cmd = [
                self.piper,
                '--model', str(self.model),
                '--output_file', str(raw_output)
            ]

            with open(text_file, 'r', encoding='utf-8') as f:
                subprocess.run(cmd, stdin=f, check=True, capture_output=True)

        finally:
            Path(text_file).unlink(missing_ok=True)

        return raw_output

    def _generate_kokoro(self, text: str, output_path: Path) -> Path:
        """Generate audio using Kokoro-82M.

        Args:
            text: Text to synthesize
            output_path: Base path for output

        Returns:
            Path to raw audio file

        Raises:
            ImportError: If Kokoro dependencies not installed
            Exception: If generation fails
        """
        import soundfile as sf

        self._init_kokoro()
        raw_output = output_path.parent / f"{output_path.stem}_raw.wav"

        # Generate audio using Kokoro
        generator = self._kokoro_pipeline(
            text,
            voice=self.kokoro_voice,
            speed=self.kokoro_speed
        )

        # Kokoro returns a generator of (graphemes, phonemes, audio) tuples
        # Concatenate all audio chunks
        audio_chunks = []
        for _, _, audio in generator:
            audio_chunks.append(audio)

        # Combine all chunks
        if not audio_chunks:
            raise ValueError(f"Kokoro failed to generate audio for text: {text[:50]}...")

        combined_audio = np.concatenate(audio_chunks)

        # Kokoro outputs at 24000 Hz sample rate
        sf.write(str(raw_output), combined_audio, 24000)

        return raw_output

    def _init_orpheus(self):
        """Lazy initialization of Orpheus TTS model (via orpheus-cpp / llama.cpp)."""
        if self._orpheus_model is None:
            try:
                from orpheus_cpp import OrpheusCpp
                self._orpheus_model = OrpheusCpp()
            except ImportError:
                raise ImportError(
                    "Orpheus TTS not installed. Install with: "
                    "pip install orpheus-cpp llama-cpp-python"
                )

    def _generate_orpheus(self, text: str, output_path: Path) -> Path:
        """Generate audio using Orpheus TTS (orpheus-cpp / llama.cpp backend).

        Outputs 24kHz mono 16-bit PCM audio.

        Args:
            text: Text to synthesize
            output_path: Base path for output

        Returns:
            Path to raw audio file
        """
        from scipy.io.wavfile import write as wav_write

        self._init_orpheus()
        raw_output = output_path.parent / f"{output_path.stem}_raw.wav"

        sample_rate, samples = self._orpheus_model.tts(
            text,
            options={"voice_id": self.orpheus_voice}
        )

        wav_write(str(raw_output), sample_rate, samples.squeeze())

        return raw_output

    def _init_chatterbox(self):
        """Lazy initialization of Chatterbox TTS model."""
        if self._chatterbox_model is None:
            try:
                from chatterbox.tts import ChatterboxTTS
                self._chatterbox_model = ChatterboxTTS.from_pretrained(device="cuda")
            except ImportError:
                raise ImportError(
                    "Chatterbox TTS not installed. Install with: "
                    "pip install chatterbox-tts"
                )

    def _generate_chatterbox(self, text: str, output_path: Path) -> Path:
        """Generate audio using Chatterbox TTS with sentence batching.

        Chatterbox produces corrupted output on long texts (500+ words).
        This method splits text into sentence batches and concatenates
        the audio to handle any length reliably.

        Outputs 24kHz audio via the standard ChatterboxTTS model (500M params).

        Args:
            text: Text to synthesize
            output_path: Base path for output

        Returns:
            Path to raw audio file
        """
        import soundfile as sf
        import numpy as np
        import re
        import os

        self._init_chatterbox()
        raw_output = output_path.parent / f"{output_path.stem}_raw.wav"

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        sentences = [s for s in sentences if s.strip()]

        # Batch size (default 4 sentences per batch)
        batch_size = int(os.getenv("CHATTERBOX_BATCH_SIZE", "4"))
        num_batches = (len(sentences) + batch_size - 1) // batch_size

        audio_parts = []
        for i in range(0, len(sentences), batch_size):
            batch_num = i // batch_size + 1
            batch = ' '.join(sentences[i:i + batch_size])

            # Chatterbox ignores commas — replace with em-dashes to force pauses
            batch = batch.replace(',', ' —')

            print(f"    Generating audio batch {batch_num}/{num_batches}...")

            generate_kwargs = dict(
                text=batch,
                exaggeration=self.chatterbox_exaggeration,
                cfg_weight=self.chatterbox_cfg,
            )
            if self.chatterbox_voice_ref:
                generate_kwargs["audio_prompt_path"] = self.chatterbox_voice_ref

            wav = self._chatterbox_model.generate(**generate_kwargs)
            audio_np = wav.squeeze().cpu().numpy()
            audio_parts.append(audio_np)

        # Concatenate with 50ms silence gaps between batches for natural pauses
        sample_rate = self._chatterbox_model.sr
        silence_samples = int(0.05 * sample_rate)  # 50ms
        silence = np.zeros(silence_samples, dtype=audio_parts[0].dtype)

        combined_parts = []
        for i, part in enumerate(audio_parts):
            combined_parts.append(part)
            if i < len(audio_parts) - 1:  # Don't add silence after last batch
                combined_parts.append(silence)

        combined_audio = np.concatenate(combined_parts)
        sf.write(str(raw_output), combined_audio, sample_rate)

        return raw_output

    @staticmethod
    def _estimate_syllables(word: str) -> int:
        """Estimate syllable count for a word.

        Syllable count correlates much better with spoken duration
        than character count. Uses vowel-group counting heuristic.
        """
        word = word.lower().strip()
        if not word:
            return 1
        count = 0
        prev_vowel = False
        for char in word:
            is_vowel = char in 'aeiouy'
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        # Handle silent e at end (e.g. "make" = 1 syllable, not 2)
        if word.endswith('e') and count > 1 and not word.endswith('le'):
            count -= 1
        return max(1, count)

    @classmethod
    def _get_word_weight(cls, word: str) -> float:
        """Get timing weight for a word, including punctuation pause compensation.

        Returns syllable count plus extra weight if the word ends with
        punctuation that causes TTS to insert a natural pause.
        """
        syllables = cls._estimate_syllables(word)
        stripped = word.rstrip()
        if stripped:
            last_char = stripped[-1]
            syllables += cls.PUNCTUATION_PAUSE_WEIGHT.get(last_char, 0.0)
        return float(syllables)

    def generate_with_timestamps(self, text: str, output_path: Path):
        """Generate audio and return word-level timestamps.

        Uses Kokoro's chunk-level output to estimate when each word is spoken.
        Timing is based on syllable count within each chunk for better accuracy
        than character-based distribution. Processes the full text in one Kokoro
        call for smooth continuous speech (no inter-sentence pauses).

        Args:
            text: Text to convert to speech
            output_path: Where to save raw audio

        Returns:
            Tuple of (Path to raw audio, List of word timestamp dicts)
            Each dict: {'word': str, 'start': float, 'end': float}
        """
        if self.engine not in ("kokoro",):
            # Fallback for non-Kokoro: generate audio, estimate timing uniformly
            raw_path = self._generate_raw(text, output_path)
            duration = self.get_audio_duration(raw_path)
            words = text.split()
            if not words:
                return raw_path, []
            total_weight = sum(self._get_word_weight(w) for w in words)
            word_timestamps = []
            t = 0.0
            for word in words:
                weight = self._get_word_weight(word)
                word_dur = duration * (weight / total_weight) if total_weight > 0 else 0
                word_timestamps.append({
                    'word': word,
                    'start': t,
                    'end': t + word_dur
                })
                t += word_dur
            return raw_path, word_timestamps

        import soundfile as sf

        self._init_kokoro()
        raw_output = output_path.parent / f"{output_path.stem}_raw.wav"

        SAMPLE_RATE = 24000

        # Single continuous Kokoro call for smooth speech without pauses.
        # Kokoro internally splits at natural phrase boundaries and yields
        # (graphemes, phonemes, audio) chunks.
        generator = self._kokoro_pipeline(
            text,
            voice=self.kokoro_voice,
            speed=self.kokoro_speed
        )

        chunks = []
        audio_parts = []
        current_time = 0.0

        for graphemes, phonemes, audio in generator:
            chunk_duration = len(audio) / SAMPLE_RATE
            chunks.append({
                'text': graphemes,
                'start': current_time,
                'duration': chunk_duration,
            })
            audio_parts.append(audio)
            current_time += chunk_duration

        if not audio_parts:
            raise ValueError(f"Kokoro failed to generate audio for text: {text[:50]}...")

        combined_audio = np.concatenate(audio_parts)
        sf.write(str(raw_output), combined_audio, SAMPLE_RATE)

        # Distribute timing within each chunk using syllable + punctuation weight.
        # Punctuation-bearing words get extra weight to absorb TTS pauses.
        word_timestamps = []
        for chunk in chunks:
            words = chunk['text'].split()
            if not words:
                continue
            total_weight = sum(self._get_word_weight(w) for w in words)
            if total_weight == 0:
                continue
            word_start = chunk['start']
            for word in words:
                weight = self._get_word_weight(word)
                word_dur = chunk['duration'] * (weight / total_weight)
                word_timestamps.append({
                    'word': word,
                    'start': word_start,
                    'end': word_start + word_dur
                })
                word_start += word_dur

        return raw_output, word_timestamps

    def _normalize_audio(self, input_path: Path, output_path: Path) -> Path:
        """Normalize audio to standard format.

        Target format:
        - Sample rate: 48000 Hz
        - Channels: 2 (stereo)
        - Codec: pcm_s16le

        This ensures all audio files can be concatenated with -c copy.

        Args:
            input_path: Raw audio file from TTS
            output_path: Destination for normalized audio

        Returns:
            Path to normalized audio

        Raises:
            subprocess.CalledProcessError: If FFmpeg fails
        """
        cmd = [
            self.ffmpeg,
            '-i', str(input_path),
            '-ar', '48000',
            '-ac', '2',
            '-c:a', 'pcm_s16le',
            '-y',
            str(output_path)
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        return output_path

    def get_audio_duration(self, audio_path: Path) -> float:
        """Get duration of audio file.

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds

        Raises:
            subprocess.CalledProcessError: If ffprobe fails
        """
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(audio_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())

    def test_installation(self) -> bool:
        """Test if TTS engine is properly configured.

        Returns:
            True if test passes, False otherwise
        """
        try:
            # Try to generate a short test phrase
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                test_output = Path(f.name)

            self.generate("Test.", test_output)

            # Check if file was created
            success = test_output.exists() and test_output.stat().st_size > 0

            # Clean up
            test_output.unlink(missing_ok=True)

            return success

        except Exception as e:
            print(f"TTS test failed: {e}")
            return False

    @staticmethod
    def list_kokoro_voices(lang: Optional[str] = None) -> dict:
        """List available Kokoro voices.

        Args:
            lang: Filter by language code ('a', 'b', 'j', etc.) or None for all

        Returns:
            Dictionary of voices organized by language and gender
        """
        voices = {
            "American English": {
                "female": [
                    ("af_heart", "❤️ Grade A - Best quality"),
                    ("af_bella", "🔥 Grade A- - High quality"),
                    ("af_nicole", "🎧 Grade B- - Good quality"),
                    ("af_aoede", "Grade C+"),
                    ("af_sarah", "Grade C+"),
                    ("af_kore", "Grade C+"),
                    ("af_sky", "Grade C-"),
                    ("af_alloy", "Grade C"),
                    ("af_nova", "Grade C"),
                    ("af_jessica", "Grade D"),
                    ("af_river", "Grade D"),
                ],
                "male": [
                    ("am_fenrir", "Grade C+ - Best male"),
                    ("am_michael", "Grade C+"),
                    ("am_puck", "Grade C+"),
                    ("am_adam", "Grade F+"),
                    ("am_echo", "Grade D"),
                    ("am_eric", "Grade D"),
                    ("am_liam", "Grade D"),
                    ("am_onyx", "Grade D"),
                    ("am_santa", "Grade D-"),
                ]
            },
            "British English": {
                "female": [
                    ("bf_emma", "Grade B-"),
                    ("bf_isabella", "Grade C"),
                    ("bf_alice", "Grade D"),
                    ("bf_lily", "Grade D"),
                ],
                "male": [
                    ("bm_fable", "Grade C"),
                    ("bm_george", "Grade C"),
                    ("bm_lewis", "Grade D+"),
                    ("bm_daniel", "Grade D"),
                ]
            },
            "French": {
                "female": [("ff_siwis", "Grade B-")]
            },
            "Japanese": {
                "female": [
                    ("jf_alpha", "Grade C+"),
                    ("jf_gongitsune", "Grade C"),
                    ("jf_tebukuro", "Grade C"),
                    ("jf_nezumi", "Grade C-"),
                ],
                "male": [("jm_kumo", "Grade C-")]
            },
            "Hindi": {
                "female": [("hf_alpha", "Grade C"), ("hf_beta", "Grade C")],
                "male": [("hm_omega", "Grade C"), ("hm_psi", "Grade C")]
            },
            "Italian": {
                "female": [("if_sara", "Grade C")],
                "male": [("im_nicola", "Grade C")]
            },
        }

        if lang:
            lang_map = {
                'a': "American English",
                'b': "British English",
                'f': "French",
                'j': "Japanese",
                'h': "Hindi",
                'i': "Italian"
            }
            lang_name = lang_map.get(lang)
            if lang_name and lang_name in voices:
                return {lang_name: voices[lang_name]}
            return {}

        return voices
