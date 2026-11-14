"""Shared test setup.

Runs before any test module is imported, so app settings never pick up a real
database, API keys or storage from a developer's local .env file.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# load_dotenv() never overrides variables that are already set, so these win.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["USE_R2_STORAGE"] = "false"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ.setdefault("PEXELS_API_KEY", "test-key")
