from app.media.multi_source_searcher import MultiSourceSearcher


class FakePexelsSearcher:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, keywords, min_duration=5.0, max_results=20, orientation='any'):
        self.calls.append((keywords, min_duration, max_results, orientation))
        return list(self.results)


class FakePixabaySearcher:
    def __init__(self, video_results=None, image_results=None):
        self.video_results = video_results or []
        self.image_results = image_results or []
        self.video_calls = []
        self.image_calls = []

    def search_videos(self, keywords, min_duration=5.0, max_results=20, orientation='any'):
        self.video_calls.append((keywords, min_duration, max_results, orientation))
        return list(self.video_results)

    def search_images(self, keywords, max_results=5, orientation='any'):
        self.image_calls.append((keywords, max_results, orientation))
        return list(self.image_results)


def test_search_returns_pexels_only_when_pixabay_disabled():
    pexels = FakePexelsSearcher(["p1", "p2"])
    pixabay = FakePixabaySearcher(video_results=["x1", "x2"])
    searcher = MultiSourceSearcher(
        pexels_searcher=pexels,
        pixabay_searcher=pixabay,
        enable_pixabay=False,
    )

    assert searcher.search(["nature"], max_results=2) == ["p1", "p2"]
    assert pixabay.video_calls == []


def test_search_interleaves_results_across_providers():
    pexels = FakePexelsSearcher(["p1", "p2", "p3"])
    pixabay = FakePixabaySearcher(video_results=["x1", "x2", "x3"])
    searcher = MultiSourceSearcher(
        pexels_searcher=pexels,
        pixabay_searcher=pixabay,
        enable_pixabay=True,
    )

    assert searcher.search(["nature"], max_results=5) == ["p1", "x1", "p2", "x2", "p3"]


def test_search_images_uses_pixabay_only_when_enabled():
    pexels = FakePexelsSearcher([])
    pixabay = FakePixabaySearcher(image_results=["img1", "img2"])
    disabled = MultiSourceSearcher(
        pexels_searcher=pexels,
        pixabay_searcher=pixabay,
        enable_pixabay=True,
        enable_image_fallback=False,
    )
    enabled = MultiSourceSearcher(
        pexels_searcher=pexels,
        pixabay_searcher=pixabay,
        enable_pixabay=True,
        enable_image_fallback=True,
    )

    assert disabled.search_images(["nature"], max_results=2) == []
    assert enabled.search_images(["nature"], max_results=2) == ["img1", "img2"]


class FakePexelsById(FakePexelsSearcher):
    def __init__(self, results=None, stats=None, error=None):
        super().__init__(results or [])
        self.id_calls = []
        self._stats = stats
        self.error = error
        if stats is not None:
            self.cache_stats = stats

    def search(self, keywords, min_duration=5.0, max_results=20, orientation='any'):
        if self.error:
            raise self.error
        return super().search(keywords, min_duration, max_results, orientation)

    def get_video_by_id(self, video_id):
        self.id_calls.append(video_id)
        return f"pexels:{video_id}"


class FakePixabayById(FakePixabaySearcher):
    def __init__(self, stats=None, **kw):
        super().__init__(**kw)
        self.id_calls = []
        if stats is not None:
            self.cache_stats = stats

    def get_by_id(self, media_id, media_type="video"):
        self.id_calls.append((media_id, media_type))
        return f"pixabay:{media_type}:{media_id}"


def test_get_video_by_id_routes_by_provider_and_media_type():
    pexels = FakePexelsById()
    pixabay = FakePixabayById()
    searcher = MultiSourceSearcher(pexels, pixabay, enable_pixabay=True)

    assert searcher.get_video_by_id("123") == "pexels:123"
    assert searcher.get_video_by_id("123", provider="Pexels", media_type="video") == "pexels:123"
    assert searcher.get_video_by_id("9", provider="Pixabay") == "pixabay:video:9"
    assert searcher.get_video_by_id("7", provider="Pixabay", media_type="image") == "pixabay:image:7"
    assert searcher.get_video_by_id("5", provider=None, media_type=None) == "pexels:5"
    assert pexels.id_calls == ["123", "123", "5"]
    assert pixabay.id_calls == [("9", "video"), ("7", "image")]


def test_get_video_by_id_pixabay_without_searcher_returns_none():
    searcher = MultiSourceSearcher(FakePexelsById(), None)
    assert searcher.get_video_by_id("9", provider="Pixabay") is None
    assert searcher.get_video_by_id(None) is None


def test_cache_stats_combines_sources_and_tolerates_missing():
    pexels = FakePexelsById(stats={'hits': 3, 'misses': 1, 'hit_rate': 0.75})
    pixabay = FakePixabayById(stats={'hits': 1, 'misses': 3, 'hit_rate': 0.25})
    combined = MultiSourceSearcher(pexels, pixabay, enable_pixabay=True).cache_stats
    assert combined == {'hits': 4, 'misses': 4, 'hit_rate': 0.5}

    # Pixabay disabled -> only Pexels counted
    only_pexels = MultiSourceSearcher(pexels, pixabay, enable_pixabay=False).cache_stats
    assert only_pexels == {'hits': 3, 'misses': 1, 'hit_rate': 0.75}

    # Searchers without cache_stats (plain VideoSearcher) -> zeros, no error
    plain = MultiSourceSearcher(FakePexelsSearcher([]), FakePixabaySearcher(), enable_pixabay=True)
    assert plain.cache_stats == {'hits': 0, 'misses': 0, 'hit_rate': 0.0}


def test_search_survives_pexels_failure_when_pixabay_enabled():
    import pytest

    pexels = FakePexelsById(error=RuntimeError("429 Too Many Requests"))
    pixabay = FakePixabaySearcher(video_results=["x1", "x2"])
    searcher = MultiSourceSearcher(pexels, pixabay, enable_pixabay=True)
    assert searcher.search(["nature"], max_results=5) == ["x1", "x2"]

    # Pixabay disabled: Pexels errors still propagate
    disabled = MultiSourceSearcher(pexels, pixabay, enable_pixabay=False)
    with pytest.raises(RuntimeError):
        disabled.search(["nature"])


def test_pixabay_media_info_skips_empty_large_rendition():
    from app.media.pixabay_search import PixabayMediaInfo, _build_query

    info = PixabayMediaInfo({
        'id': 1, 'duration': 10,
        'videos': {
            'large': {'url': '', 'width': 0, 'height': 0},
            'medium': {'url': 'https://cdn/m.mp4', 'width': 1920, 'height': 1080},
            'small': {'url': 'https://cdn/s.mp4', 'width': 960, 'height': 540},
        },
    })
    assert info.download_url == 'https://cdn/m.mp4'
    assert info.width == 1920
    assert info.preview_url == 'https://cdn/s.mp4'

    long_query = _build_query(["word"] * 40)
    assert len(long_query) <= 100
    assert not long_query.endswith(" ")
    assert all(w == "word" for w in long_query.split())
