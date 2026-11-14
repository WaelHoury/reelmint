"""Media processing modules (TTS, video search, assembly, effects)."""

import sys
from importlib import import_module
from pathlib import Path

# Add parent directory to path for imports from root
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

_EXPORTS = {
    'TTSGenerator': ('tts_generator', 'TTSGenerator'),
    'transcribe_with_word_timestamps': ('tts_generator', 'transcribe_with_word_timestamps'),
    'VideoSearcher': ('video_searcher', 'VideoSearcher'),
    'CachedVideoSearcher': ('video_searcher', 'CachedVideoSearcher'),
    'VideoInfo': ('video_searcher', 'VideoInfo'),
    'CLIPMatcher': ('clip_matcher', 'CLIPMatcher'),
    'VideoAssembler': ('video_assembler', 'VideoAssembler'),
    'get_resolution': ('video_assembler', 'get_resolution'),
    'EffectsEngine': ('effects_engine', 'EffectsEngine'),
    'SubtitleGenerator': ('subtitle_generator', 'SubtitleGenerator'),
    'AudioMixer': ('audio_mixer', 'AudioMixer'),
    'MusicSelector': ('music_selector', 'MusicSelector'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
