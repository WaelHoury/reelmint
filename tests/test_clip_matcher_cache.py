"""Thread-safety / corruption tests for the CLIP matcher cache index.

Heavy deps (torch, open_clip, PIL) are stubbed when missing; only the
index helpers are exercised, never the model.
"""

import importlib
import json
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _import_clip_matcher():
    for name in ("torch", "open_clip"):
        try:
            importlib.import_module(name)
        except ImportError:
            sys.modules.setdefault(name, types.ModuleType(name))
    try:
        importlib.import_module("PIL.Image")
    except ImportError:
        pil = types.ModuleType("PIL")
        pil.Image = types.ModuleType("PIL.Image")
        sys.modules.setdefault("PIL", pil)
        sys.modules.setdefault("PIL.Image", pil.Image)
    return importlib.import_module("clip_matcher")


def _matcher(cache_dir: Path):
    cm = _import_clip_matcher()
    m = cm.CLIPMatcher.__new__(cm.CLIPMatcher)  # skip model loading
    m.cache_dir = cache_dir
    m._load_cache_index()
    return m


def test_corrupted_index_starts_empty(tmp_path):
    (tmp_path / "index.json").write_text('{"abc": {"video_path": "x"', encoding="utf-8")
    m = _matcher(tmp_path)
    assert m.cache_index == {}


def test_non_dict_index_starts_empty(tmp_path):
    (tmp_path / "index.json").write_text("[1, 2]", encoding="utf-8")
    assert _matcher(tmp_path).cache_index == {}


def test_concurrent_updates_keep_valid_index(tmp_path):
    m = _matcher(tmp_path)
    errors = []

    def worker(n):
        try:
            for j in range(25):
                m._update_cache_index(f"k{n}_{j}", {"video_path": f"v{n}_{j}"})
                m._save_cache_index()
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(data) == 20 * 25
    assert not list(tmp_path.glob("*.tmp"))
    # Reload sees the same content
    assert _matcher(tmp_path).cache_index == data
