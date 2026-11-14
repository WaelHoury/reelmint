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
