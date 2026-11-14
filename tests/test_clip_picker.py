"""Clip picker: no clip repeats within a video, progressive broadening ladder, page plumbing."""

import logging
import threading
import time
from types import SimpleNamespace

import pytest

from app.media import clip_picker
from app.media.clip_picker import ClipReservations, clip_key, find_candidates, media_key, pick_clip


class Info:
    def __init__(self, id, provider="Pexels", duration=12, orientation="portrait", media_type="video"):
        self.id = id
        self.provider = provider
        self.duration = duration
        self.orientation = orientation
        self.media_type = media_type
        self.preview_url = f"https://example.com/{id}.mp4"
        self.author = "Ana"
        self.url = f"https://example.com/{id}"


class FakeSearcher:
    """Answers by (sorted query, page); records every call."""

    def __init__(self, results=None, pixabay=None, images=None, enable_pixabay=False,
                 enable_image_fallback=False, delay=0.0):
        self.results = results or {}
        self.images = images or {}
        self.calls = []
        self.delay = delay
        self.enable_pixabay = enable_pixabay
        self.enable_image_fallback = enable_image_fallback
        self.pixabay = pixabay

    @staticmethod
    def _key(keywords):
        return tuple(sorted(k.lower() for k in keywords))

    def search(self, keywords, min_duration=5.0, max_results=20, orientation="any", page=1):
        self.calls.append(("search", list(keywords), page, max_results))
        if self.delay:
            time.sleep(self.delay)
        return list(self.results.get((self._key(keywords), page), []))

    def search_images(self, keywords, max_results=5, orientation="any"):
        self.calls.append(("images", list(keywords)))
        return list(self.images.get(self._key(keywords), []))


class FakePixabay:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_videos(self, keywords, min_duration=5.0, max_results=20, orientation="any", page=1):
        self.calls.append((list(keywords), page))
        return list(self.results.get(tuple(sorted(k.lower() for k in keywords)), []))


CLIP = {"keywords": ["red fox", "snow", "forest"],
        "visual_description": "A red fox trotting through deep snow between pine trees"}


def k(*words):
    return tuple(sorted(words))


def test_takes_first_result_when_nothing_is_used():
    searcher = FakeSearcher({(k("red fox", "snow", "forest"), 1): [Info(1), Info(2)]})
    info, rung = pick_clip(searcher, CLIP, "portrait", min_duration=5)
    assert info.id == 1 and rung == "full_keywords"
    assert searcher.calls[0] == ("search", ["red fox", "snow", "forest"], 1, 40)  # asks for up to 40


def test_skips_clips_used_in_this_video_and_tries_page_two():
    searcher = FakeSearcher({
        (k("red fox", "snow", "forest"), 1): [Info(1), Info(2)],
        (k("red fox", "snow", "forest"), 2): [Info(2), Info(3)],
    })
    used = ClipReservations([("Pexels", "1"), ("Pexels", "2")])
    info, rung = pick_clip(searcher, CLIP, "portrait", reservations=used)
    assert info.id == 3 and rung == "full_keywords"
    assert [c[2] for c in searcher.calls] == [1, 2]
    assert ("Pexels", "3") in used  # the pick is reserved


def test_same_id_from_other_provider_is_a_different_clip():
    searcher = FakeSearcher({(k("red fox", "snow", "forest"), 1): [Info(1, provider="Pixabay")]})
    info, _ = pick_clip(searcher, CLIP, "portrait", reservations=ClipReservations([("Pexels", "1")]))
    assert info.provider == "Pixabay"


def test_ladder_broadens_rung_by_rung():
    clip = {"keywords": ["glacier", "ice", "melting", "arctic"],
            "visual_description": "Close up of meltwater dripping off a blue glacier wall"}
    pixabay = FakePixabay({})
    searcher = FakeSearcher(pixabay=pixabay, enable_pixabay=True, enable_image_fallback=True,
                            images={k("glacier", "ice", "melting", "arctic"): [Info(90, "Pixabay", 0, media_type="image")]})
    info, rung = pick_clip(searcher, clip, "landscape", topic="Why glaciers are turning blue")
    assert rung == "pixabay_image" and info.id == 90
    queries = [c[1] for c in searcher.calls if c[0] == "search"]
    assert queries == [
        ["glacier", "ice", "melting", "arctic"],  # 1: full (empty, so no page 2)
        ["glacier", "ice"],                       # 2: first two
        ["glacier"],                              # 3: strongest keyword
        ["meltwater", "dripping", "blue"],        # 4: nouns from the description
        ["glaciers", "turning", "blue"],          # 5: topic nouns
    ]
    assert pixabay.calls == [(["glacier", "ice", "melting", "arctic"], 2), (["glacier"], 2)]  # 6: pixabay videos


@pytest.mark.parametrize("rung,results", [
    ("first_two_keywords", {(k("red fox", "snow"), 1): [Info(5)]}),
    ("strongest_keyword", {(k("red fox"), 1): [Info(5)]}),
    ("description_nouns", {(k("red", "fox", "trotting"), 1): [Info(5)]}),
    ("topic_nouns", {(k("foxes", "survive", "winter"), 1): [Info(5)]}),
])
def test_stops_at_first_rung_with_an_unused_clip(rung, results):
    searcher = FakeSearcher(results)
    info, got = pick_clip(searcher, CLIP, "portrait", topic="How foxes survive winter")
    assert got == rung and info.id == 5


def test_pixabay_video_rung():
    pixabay = FakePixabay({k("red fox", "snow", "forest"): [Info(70, "Pixabay")]})
    searcher = FakeSearcher(pixabay=pixabay, enable_pixabay=True)
    info, rung = pick_clip(searcher, CLIP, "portrait")
    assert rung == "pixabay_video" and info.id == 70


def test_prefers_matching_orientation_and_long_enough_clips():
    searcher = FakeSearcher({(k("red fox", "snow", "forest"), 1): [
        Info(1, duration=4), Info(2, orientation="landscape", duration=30), Info(3, duration=11),
    ]})
    info, _ = pick_clip(searcher, CLIP, "portrait", min_duration=9)
    assert info.id == 3


def test_last_resort_reuses_a_clip_from_this_video(caplog):
    searcher = FakeSearcher({(k("red fox", "snow", "forest"), 1): [Info(1), Info(2)]})
    used = ClipReservations([("Pexels", "1"), ("Pexels", "2")])
    with caplog.at_level(logging.WARNING, logger="app.media.clip_picker"):
        info, rung = pick_clip(searcher, CLIP, "portrait", reservations=used)
    assert rung == "reuse_in_video" and info.id == 1
    assert "already used in this video" in caplog.text


def test_nothing_found_and_all_searches_failing():
    assert pick_clip(FakeSearcher(), CLIP, "portrait") == (None, None)

    class Broken(FakeSearcher):
        def search(self, *a, **kw):
            raise RuntimeError("429 Too Many Requests")

    with pytest.raises(RuntimeError, match="429"):
        pick_clip(Broken(), CLIP, "portrait")


def test_parallel_picks_never_share_a_clip():
    """Many shots with identical keywords searched at once: every pick is distinct."""
    pool = [Info(i) for i in range(10)]
    searcher = FakeSearcher({(k("city", "night"), 1): pool}, delay=0.01)
    used = ClipReservations()
    barrier = threading.Barrier(8)
    picks = [None] * 8

    def worker(n):
        barrier.wait()
        picks[n] = pick_clip(searcher, {"keywords": ["city", "night"], "visual_description": ""},
                             "portrait", reservations=used)[0]

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [p.id for p in picks]
    assert len(set(ids)) == 8
    assert len(used) == 8


def test_find_candidates_skips_used_without_reserving():
    searcher = FakeSearcher({(k("red fox", "snow", "forest"), 1): [Info(1), Info(2), Info(3)]})
    used = ClipReservations([("Pexels", "1")])
    cands, rung = find_candidates(searcher, SimpleNamespace(**CLIP), "portrait", reservations=used, limit=5)
    assert [c.id for c in cands] == [2, 3] and rung == "full_keywords"
    assert len(used) == 1


def test_keys_and_query_helpers():
    assert media_key(Info(7, provider="pixabay")) == ("Pixabay", "7")
    assert clip_key({"video_id": 12, "provider": None}) == ("Pexels", "12")
    assert clip_key({"video_id": None}) is None
    assert clip_picker.concrete_nouns("A slow aerial shot of the old lighthouse at dusk") == ["old", "lighthouse", "dusk"]
    assert clip_picker.strongest_keyword(["process", "volcano"]) == "volcano"
    rungs = [r for r, _ in clip_picker.build_queries(["fox", "snow"], "fox snow", None)]
    assert rungs == ["full_keywords", "strongest_keyword"]  # duplicates skipped


# ── searcher page plumbing ────────────────────────────────────


def test_pexels_page_param_and_cache_key(monkeypatch):
    import video_searcher

    captured = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"videos": [{"id": 5, "duration": 20, "url": "u", "user": {"name": "A"},
                                "video_files": [{"link": "l", "width": 1080, "height": 1920,
                                                 "file_type": "video/mp4"}]}]}

    monkeypatch.setattr(video_searcher.requests, "get",
                        lambda url, headers=None, params=None, timeout=None: captured.append(params) or Resp())
    searcher = video_searcher.VideoSearcher("key")
    searcher.search(["fox"], orientation="portrait", page=2)
    assert captured[-1]["page"] == 2
    searcher.search(["fox"], orientation="portrait")
    assert captured[-1]["page"] == 1

    key = video_searcher.CachedVideoSearcher._cache_key
    assert key(["fox"], "portrait") == key(["fox"], "portrait", 1)
    assert key(["fox"], "portrait", 2) != key(["fox"], "portrait", 1)


def test_cached_searcher_respects_limit_and_page(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    import video_searcher

    calls = []

    def fake_search(self, keywords, min_duration=5.0, max_results=20, orientation="any", page=1):
        calls.append((max_results, page))
        return [video_searcher.VideoInfo({"id": i, "duration": 20, "video_files": [
            {"link": f"l{i}", "width": 1080, "height": 1920, "file_type": "video/mp4"}]})
            for i in range(max_results)]

    monkeypatch.setattr(video_searcher.VideoSearcher, "search", fake_search)
    cached = video_searcher.CachedVideoSearcher("key", redis_client=fakeredis.FakeRedis())
    assert len(cached.search(["fox"], max_results=5)) == 5
    assert len(cached.search(["fox"], max_results=5)) == 5          # hit
    assert len(cached.search(["fox"], max_results=40)) == 40        # 5 cached can't answer 40: miss
    assert len(cached.search(["fox"], max_results=40, page=2)) == 40
    assert calls == [(5, 1), (40, 1), (40, 2)]


def test_multi_source_passes_page_only_when_paging():
    from app.media.multi_source_searcher import MultiSourceSearcher

    seen = []

    class Pexels:
        def search(self, keywords, min_duration=5.0, max_results=20, orientation="any", **kw):
            seen.append(("pexels", kw))
            return []

    class Pixabay:
        def search_videos(self, keywords, min_duration=5.0, max_results=20, orientation="any", **kw):
            seen.append(("pixabay", kw))
            return []

    m = MultiSourceSearcher(Pexels(), Pixabay(), enable_pixabay=True)
    m.search(["fox"])
    m.search(["fox"], page=2)
    assert sorted(seen[:2]) == [("pexels", {}), ("pixabay", {})]
    assert sorted(seen[2:]) == [("pexels", {"page": 2}), ("pixabay", {"page": 2})]


# ── wiring into the wizard / batch clip search ────────────────


@pytest.fixture
def clip_env(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    import importlib
    vs = importlib.import_module("app.api.services.video_service")
    from app.api.services.session_store import session_store
    import app.media.multi_source_searcher as mss
    import scene_planner

    monkeypatch.setattr(session_store, "_redis", fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(vs.Redis, "from_url", staticmethod(lambda url: None))
    shared = {}

    class Searcher:
        def __init__(self, **kw):
            self.enable_pixabay = False
            self.enable_image_fallback = False
            self.pixabay = None

        def search(self, keywords, min_duration=5.0, max_results=20, orientation="any", page=1):
            time.sleep(0.005)
            return list(shared.get(page, []))

        def search_images(self, *a, **kw):
            return []

    monkeypatch.setattr(mss, "MultiSourceSearcher", Searcher)
    return SimpleNamespace(vs=vs, store=session_store, shared=shared, planner_mod=scene_planner)


def test_generate_clips_task_never_repeats_a_clip(clip_env, monkeypatch):
    vs = clip_env.vs
    clip_env.shared[1] = [Info(i) for i in range(6)]
    clip_env.shared[2] = [Info(i) for i in range(6, 12)]
    clips = [SimpleNamespace(keywords=["ocean", "waves"], visual_description="waves") for _ in range(9)]

    class Planner:
        def generate_clips_from_script(self, script, minutes):
            return SimpleNamespace(clips=clips)

    monkeypatch.setattr(clip_env.planner_mod, "create_planner", lambda **kw: Planner())
    session = clip_env.store.create(title="Ocean", input_type="script", script_text="Waves. " * 30,
                                    duration_seconds=60, orientation="portrait")
    session.status = "clips_generating"
    clip_env.store.update(session)

    vs.generate_clips_task(session.session_id)

    done = clip_env.store.get(session.session_id)
    assert done.status == "clips_ready"
    ids = [c["video_id"] for c in done.clips]
    assert all(c["search_status"] == "ready" for c in done.clips)
    assert len(set(ids)) == 9  # page 1 had 6; the rest came from page 2
    assert set(done.clips[0]) >= {"index", "keywords", "visual_description", "video_preview_url",
                                  "video_id", "author", "author_url", "duration_seconds",
                                  "search_status", "search_error", "media_type", "provider"}


def test_update_clip_excludes_the_videos_other_clips(clip_env):
    vs = clip_env.vs
    clip_env.shared[1] = [Info(1), Info(2), Info(3)]
    session = clip_env.store.create(title="Ocean", input_type="prompt", prompt="ocean waves",
                                    duration_seconds=60, orientation="portrait")
    session.clips = [
        {"index": 0, "video_id": "1", "provider": "Pexels"},
        {"index": 1, "video_id": "3", "provider": "Pexels"},
        {"index": 2, "video_id": "2", "provider": "Pexels"},
    ]
    clip_env.store.update(session)
    updated = vs.video_service.update_clip(session, 0, "crashing ocean waves at sunset", ["ocean", "waves"])
    # 3 and 2 belong to other shots; 1 (this shot's own clip) is fine to pick again
    assert updated.clips[0]["video_id"] == "1"
    updated.clips[0]["video_id"] = "9"
    updated = vs.video_service.update_clip(updated, 1, "waves", ["ocean", "waves"])
    assert updated.clips[1]["video_id"] == "1"
