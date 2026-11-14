"""Pick stock footage for a shot without repeating a clip inside one video.

Every path that auto-picks footage (the wizard/batch clip search, swapping one
clip, and the CLI's full pipeline) goes through the broadening ladder here.
When every result of a search is already used in the same video (or a search
comes back empty), the next rung tries a broader query, stopping at the first
rung that yields an unused clip:

1. ``full_keywords``       all keywords, up to 40 results, then page 2
2. ``first_two_keywords``  the first two keywords
3. ``strongest_keyword``   the single most concrete keyword
4. ``description_nouns``   2-3 concrete nouns from the shot's visual description
5. ``topic_nouns``         nouns from the video's topic / title
6. ``pixabay_video``       Pixabay videos (page 2), if Pixabay is enabled
   ``pixabay_image``       Pixabay still images, if the image fallback is enabled
7. ``reuse_in_video``      last resort: a clip already used in this video

Within each rung, orientation-matching clips at least as long as the shot are
preferred. ``ClipReservations`` is the thread-safe set of clips already taken
by this video; picks are reserved atomically so parallel shot searches never
grab the same clip.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MediaKey = Tuple[str, str]

LADDER_MAX_RESULTS = 40
DEFAULT_MIN_DURATION = 5.0  # what the searchers filter on by default

RUNG_FULL = "full_keywords"
RUNG_FIRST_TWO = "first_two_keywords"
RUNG_STRONGEST = "strongest_keyword"
RUNG_DESCRIPTION = "description_nouns"
RUNG_TOPIC = "topic_nouns"
RUNG_PIXABAY_VIDEO = "pixabay_video"
RUNG_PIXABAY_IMAGE = "pixabay_image"
RUNG_REUSE = "reuse_in_video"

RUNGS = (
    RUNG_FULL, RUNG_FIRST_TWO, RUNG_STRONGEST, RUNG_DESCRIPTION, RUNG_TOPIC,
    RUNG_PIXABAY_VIDEO, RUNG_PIXABAY_IMAGE, RUNG_REUSE,
)


# ── Keys and reservations ─────────────────────────────────────


def _norm_provider(provider: Optional[str]) -> str:
    p = (provider or "Pexels").strip()
    low = p.lower()
    if low == "pexels":
        return "Pexels"
    if low == "pixabay":
        return "Pixabay"
    return p


def media_key(info) -> Optional[MediaKey]:
    """(provider, media_id) for a search result, or None if it has no id."""
    media_id = getattr(info, "id", None)
    if media_id is None or media_id == "":
        return None
    return _norm_provider(getattr(info, "provider", None)), str(media_id)


def clip_key(clip: Optional[dict]) -> Optional[MediaKey]:
    """(provider, media_id) for a stored clip dict (``video_id`` / ``provider``)."""
    if not isinstance(clip, dict):
        return None
    media_id = clip.get("video_id")
    if media_id is None or media_id == "":
        return None
    return _norm_provider(clip.get("provider")), str(media_id)


class ClipReservations:
    """Thread-safe set of clips already used by one video."""

    def __init__(self, keys: Iterable[Optional[MediaKey]] = ()):
        self._lock = threading.Lock()
        self._keys = {k for k in keys if k}

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._keys

    def __len__(self) -> int:
        with self._lock:
            return len(self._keys)

    def try_reserve(self, key: Optional[MediaKey]) -> bool:
        """Atomically claim ``key``. False if this video already uses it."""
        if not key:
            return False
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True

    def reserve(self, key: Optional[MediaKey]) -> None:
        """Claim ``key`` even if it is already used (last-resort reuse)."""
        if key:
            with self._lock:
                self._keys.add(key)

    def release(self, key: Optional[MediaKey]) -> None:
        with self._lock:
            self._keys.discard(key)

    def snapshot(self) -> set:
        with self._lock:
            return set(self._keys)


# ── Query building ────────────────────────────────────────────


def _filters():
    """Stopwords and abstract words shared with the scene planner."""
    try:
        from scene_planner import KEYWORD_STOPWORDS, ScenePlanner
        return KEYWORD_STOPWORDS, ScenePlanner.ABSTRACT_BLOCKLIST
    except Exception:  # pragma: no cover - scene_planner always importable in the app
        return frozenset(), frozenset()


def concrete_nouns(text: Optional[str], limit: int = 3) -> List[str]:
    """Up to ``limit`` filmable words from free text (stopwords/abstract words removed)."""
    if not text or not isinstance(text, str):
        return []
    stopwords, abstract = _filters()
    out, seen = [], set()
    for word in re.findall(r"[a-z]{3,}", text.lower()):
        if word in stopwords or word in abstract or word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= limit:
            break
    return out


def strongest_keyword(keywords: Sequence[str]) -> Optional[str]:
    """The first keyword that names something concrete (falls back to the first one)."""
    keywords = [k.strip() for k in keywords or [] if isinstance(k, str) and k.strip()]
    if not keywords:
        return None
    stopwords, abstract = _filters()
    for k in keywords:
        low = k.lower()
        if low not in stopwords and low not in abstract:
            return k
    return keywords[0]


def _clip_fields(clip) -> Tuple[List[str], str]:
    if isinstance(clip, dict):
        keywords = clip.get("keywords") or []
        desc = clip.get("visual_description") or ""
    else:
        keywords = getattr(clip, "keywords", None) or []
        desc = getattr(clip, "visual_description", None) or ""
    if isinstance(keywords, str):
        keywords = keywords.split(",")
    keywords = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    return keywords, desc if isinstance(desc, str) else ""


def build_queries(keywords: Sequence[str], visual_description: str = "",
                  topic: Optional[str] = None) -> List[Tuple[str, List[str]]]:
    """The keyword rungs (1-5) as (rung, keywords), skipping empty or repeated queries."""
    keywords = [k for k in keywords or [] if k]
    strongest = strongest_keyword(keywords)
    candidates = [
        (RUNG_FULL, list(keywords)),
        (RUNG_FIRST_TWO, list(keywords[:2]) if len(keywords) > 2 else []),
        (RUNG_STRONGEST, [strongest] if strongest and len(keywords) > 1 else []),
        (RUNG_DESCRIPTION, concrete_nouns(visual_description, 3)),
        (RUNG_TOPIC, concrete_nouns(topic, 3)),
    ]
    out, seen = [], set()
    for rung, query in candidates:
        norm = tuple(sorted(q.lower().strip() for q in query))
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append((rung, query))
    return out


# ── Ranking ───────────────────────────────────────────────────


def _rank(results: list, orientation: Optional[str], min_duration: float) -> list:
    """Stable sort: matching orientation first, then clips long enough for the shot."""
    def key(info):
        o = getattr(info, "orientation", None)
        wrong_orientation = bool(
            orientation and orientation != "any" and o and o not in ("unknown", orientation)
        )
        is_image = getattr(info, "media_type", "video") == "image"
        too_short = (not is_image) and min_duration > 0 and (getattr(info, "duration", 0) or 0) < min_duration
        return (wrong_orientation, too_short)
    return sorted(results, key=key)


def _search_floor(min_duration: float) -> float:
    """Minimum duration passed to the searchers (ranking prefers >= min_duration)."""
    if min_duration and min_duration > 0:
        return min(DEFAULT_MIN_DURATION, float(min_duration))
    return DEFAULT_MIN_DURATION


# ── The ladder ────────────────────────────────────────────────


class _Errors:
    def __init__(self):
        self.first: Optional[Exception] = None
        self.any_ok = False


def _call(errors: _Errors, fn: Callable, *args, **kwargs) -> list:
    try:
        results = fn(*args, **kwargs) or []
        errors.any_ok = True
        return list(results)
    except Exception as e:  # one failed provider call must not end the ladder
        logger.warning("Clip search call failed: %s", e)
        if errors.first is None:
            errors.first = e
        return []


def iter_rungs(searcher, keywords: Sequence[str], orientation: Optional[str],
               min_duration: float = 0.0, visual_description: str = "",
               topic: Optional[str] = None, max_results: int = LADDER_MAX_RESULTS,
               errors: Optional[_Errors] = None) -> Iterator[Tuple[str, list]]:
    """Yield (rung, ranked results) for each search, broadest last. Lazy: stop iterating to stop searching."""
    errors = errors or _Errors()
    floor = _search_floor(min_duration)
    queries = build_queries(keywords, visual_description, topic)

    for rung, query in queries:
        page1 = _call(errors, searcher.search, query, min_duration=floor,
                      max_results=max_results, orientation=orientation)
        yield rung, _rank(page1, orientation, min_duration)
        if rung == RUNG_FULL and page1:
            page2 = _call(errors, searcher.search, query, min_duration=floor,
                          max_results=max_results, orientation=orientation, page=2)
            yield rung, _rank(page2, orientation, min_duration)

    broad = [q for r, q in queries if r in (RUNG_FULL, RUNG_STRONGEST)] or [q for _, q in queries[:1]]

    pixabay = getattr(searcher, "pixabay", None)
    if pixabay is not None and getattr(searcher, "enable_pixabay", False):
        for query in broad:
            results = _call(errors, pixabay.search_videos, query, floor, max_results,
                            orientation, page=2)
            yield RUNG_PIXABAY_VIDEO, _rank(results, orientation, min_duration)

    if getattr(searcher, "enable_image_fallback", False) and hasattr(searcher, "search_images"):
        image_queries = broad + [q for r, q in queries if r == RUNG_DESCRIPTION]
        for query in image_queries:
            results = _call(errors, searcher.search_images, query, max_results=10,
                            orientation=orientation)
            yield RUNG_PIXABAY_IMAGE, _rank(results, orientation, 0)


def pick_clip(searcher, clip, orientation: Optional[str], min_duration: float = 0.0,
              reservations: Optional[ClipReservations] = None, topic: Optional[str] = None,
              max_results: int = LADDER_MAX_RESULTS, label: str = "") -> Tuple[Optional[object], Optional[str]]:
    """Pick one clip for a shot and reserve it for this video.

    ``clip`` is anything with ``keywords`` and ``visual_description`` (object or
    dict). Returns ``(media_info, rung)``, or ``(None, None)`` if nothing was
    found at all. Raises the first search error only if every search failed.
    """
    reservations = reservations if reservations is not None else ClipReservations()
    keywords, desc = _clip_fields(clip)
    errors = _Errors()
    seen: list = []
    for rung, results in iter_rungs(searcher, keywords, orientation, min_duration, desc,
                                    topic, max_results, errors):
        for info in results:
            if reservations.try_reserve(media_key(info)):
                _log_rung(label, keywords, rung, info)
                return info, rung
        seen.extend(results)

    if seen:
        info = seen[0]
        reservations.reserve(media_key(info))
        logger.warning(
            "Clip %s %s: every candidate is already used in this video; reusing %s %s",
            label, keywords, getattr(info, "provider", "?"), getattr(info, "id", "?"),
        )
        return info, RUNG_REUSE
    if errors.first is not None and not errors.any_ok:
        raise errors.first
    return None, None


def find_candidates(searcher, clip, orientation: Optional[str], min_duration: float = 0.0,
                    reservations: Optional[ClipReservations] = None, topic: Optional[str] = None,
                    limit: int = 5, max_results: int = LADDER_MAX_RESULTS,
                    label: str = "") -> Tuple[list, Optional[str]]:
    """Unused candidates from the first rung that has any (for scoring, e.g. CLIP in the CLI).

    Nothing is reserved: the caller reserves its final pick with
    ``reservations.try_reserve(media_key(best))``. Candidates long enough for
    the shot come first. The last-resort rung returns already-used clips.
    """
    reservations = reservations if reservations is not None else ClipReservations()
    keywords, desc = _clip_fields(clip)
    errors = _Errors()
    seen: list = []
    for rung, results in iter_rungs(searcher, keywords, orientation, min_duration, desc,
                                    topic, max_results, errors):
        fresh = [r for r in results if media_key(r) not in reservations]
        if fresh:
            _log_rung(label, keywords, rung, fresh[0])
            return fresh[:limit], rung
        seen.extend(results)
    if seen:
        logger.warning("Clip %s %s: every candidate is already used in this video; reusing one",
                       label, keywords)
        return seen[:limit], RUNG_REUSE
    if errors.first is not None and not errors.any_ok:
        raise errors.first
    return [], None


def _log_rung(label: str, keywords, rung: str, info) -> None:
    if rung == RUNG_FULL:
        logger.debug("Clip %s %s: picked on rung %s", label, keywords, rung)
    else:
        logger.info("Clip %s %s: picked %s %s on broader rung '%s'", label, keywords,
                    getattr(info, "provider", "?"), getattr(info, "id", "?"), rung)
