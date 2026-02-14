"""Core business logic for video generation."""

from .config import settings, get_resolution, get_words_per_second

# Re-export from root modules for backward compatibility
import sys
from pathlib import Path

# Add parent directory to path for imports from root
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from scene_planner import ScenePlanner, VideoPlan, VisualClip, Scene, ScenePlan
from script_loader import load_script, estimate_duration_from_script, ScriptInput

__all__ = [
    'settings',
    'get_resolution',
    'get_words_per_second',
    'ScenePlanner',
    'VideoPlan',
    'VisualClip',
    'Scene',
    'ScenePlan',
    'load_script',
    'estimate_duration_from_script',
    'ScriptInput',
]
