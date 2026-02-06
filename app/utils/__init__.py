"""Utility modules (tracking, helpers)."""

# Re-export from root modules for backward compatibility
import sys
from pathlib import Path

# Add parent directory to path for imports from root
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from license_tracker import LicenseTracker
from token_counter import TokenCounter
# Note: utils.py helpers can be imported directly if needed

__all__ = [
    'LicenseTracker',
    'TokenCounter',
]
