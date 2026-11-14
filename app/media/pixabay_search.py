"""Video and image search from Pixabay API."""

import hashlib
import json
import logging
import requests
from typing import List, Dict, Optional, Literal
from pathlib import Path

logger = logging.getLogger(__name__)


def cached_results(raw, max_results: int) -> Optional[list]:
    """Cached items that can answer a request for ``max_results`` (see video_searcher.cached_results)."""
    data = json.loads(raw)
    if isinstance(data, dict):
        items = data.get('items') or []
        limit = int(data.get('limit') or 0)
        if limit >= max_results or len(items) < limit:
            return items
        return None
    if isinstance(data, list) and len(data) >= max_results:
        return data
    return None


class PixabayMediaInfo:
    """Information about a Pixabay video or image. Duck-type compatible with VideoInfo."""

    def __init__(self, data: dict, media_type: str = "video"):
        self.provider = "Pixabay"
        self.media_type = media_type
        self.id = data.get('id')
        self.url = data.get('pageURL', '')
        self.author = data.get('user', 'Unknown')
        self.license = "Pixabay License"
        self.license_url = "https://pixabay.com/service/license-summary/"

        if media_type == "video":
            self.duration = data.get('duration', 0)
            videos = data.get('videos', {})
            # Prefer large > medium > small > tiny. Pixabay returns a "large"
            # entry with an empty url when no large rendition exists, so pick
            # the first rendition that actually has a URL.
            best = self._first_with_url(videos, ('large', 'medium', 'small', 'tiny'))
            self.download_url = best.get('url', '')
            self.width = best.get('width', 0)
            self.height = best.get('height', 0)
            preview = self._first_with_url(videos, ('small', 'tiny', 'medium', 'large'))
            self.preview_url = preview.get('url') or self.download_url
        else:
            self.duration = 0
            self.download_url = data.get('largeImageURL', data.get('webformatURL', ''))
            self.width = data.get('imageWidth', 0)
            self.height = data.get('imageHeight', 0)
            self.preview_url = data.get('webformatURL', self.download_url)

    @staticmethod
    def _first_with_url(renditions: dict, order) -> dict:
        for name in order:
            entry = renditions.get(name) or {}
            if entry.get('url'):
                return entry
        return {}

    @property
    def orientation(self) -> str:
        if self.width == 0 or self.height == 0:
            return 'unknown'
        ratio = self.width / self.height
        if ratio > 1.1:
            return 'landscape'
        elif ratio < 0.9:
            return 'portrait'
        return 'square'

    def to_dict(self) -> Dict:
        return {
            'url': self.url,
            'author': self.author,
            'provider': self.provider,
            'license': self.license,
        }


# Map our orientation names to Pixabay's API parameter values
_ORIENTATION_MAP = {
    'landscape': 'horizontal',
    'portrait': 'vertical',
}


# Pixabay rejects queries longer than 100 characters
_MAX_QUERY_LEN = 100

# Pixabay's API terms ask clients to cache results for 24 hours
PIXABAY_CACHE_TTL = 86400


def _build_query(keywords: List[str]) -> str:
    """Join keywords into a Pixabay query, truncated at a word boundary."""
    query = " ".join(k.strip() for k in keywords if k and k.strip())
    if len(query) <= _MAX_QUERY_LEN:
        return query
    cut = query[:_MAX_QUERY_LEN]
    if query[_MAX_QUERY_LEN] != " " and " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.strip()


class PixabaySearcher:
    """Search and download videos/images from Pixabay."""

    VIDEOS_URL = "https://pixabay.com/api/videos/"
    IMAGES_URL = "https://pixabay.com/api/"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Pixabay API key is required")
        self.api_key = api_key

    def search_videos(self, keywords: List[str], min_duration: float = 5.0,
                      max_results: int = 20,
                      orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any',
                      page: int = 1) -> List[PixabayMediaInfo]:
        query = _build_query(keywords)
        params = {
            'key': self.api_key,
            'q': query,
            'per_page': min(max_results * 2, 200),
            'video_type': 'film',
            'safesearch': 'true',
        }
        if page and int(page) > 1:
            params['page'] = int(page)
        if orientation in _ORIENTATION_MAP:
            params['orientation'] = _ORIENTATION_MAP[orientation]

        try:
            response = requests.get(self.VIDEOS_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("Pixabay video search failed: %s", e)
            return []

        videos = []
        for hit in data.get('hits', []):
            info = PixabayMediaInfo(hit, media_type="video")
            if info.duration >= min_duration and info.download_url:
                if orientation == 'any' or info.orientation == orientation:
                    videos.append(info)
            if len(videos) >= max_results:
                break

        return videos

    def search_images(self, keywords: List[str], max_results: int = 10,
                      orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> List[PixabayMediaInfo]:
        query = _build_query(keywords)
        params = {
            'key': self.api_key,
            'q': query,
            'per_page': min(max_results * 2, 200),
            'image_type': 'photo',
            'safesearch': 'true',
        }
        if orientation in _ORIENTATION_MAP:
            params['orientation'] = _ORIENTATION_MAP[orientation]

        try:
            response = requests.get(self.IMAGES_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("Pixabay image search failed: %s", e)
            return []

        images = []
        for hit in data.get('hits', []):
            info = PixabayMediaInfo(hit, media_type="image")
            if info.download_url:
                if orientation == 'any' or info.orientation == orientation:
                    images.append(info)
            if len(images) >= max_results:
                break

        return images

    def get_by_id(self, media_id, media_type: str = "video") -> Optional[PixabayMediaInfo]:
        """Fetch a single Pixabay video or image by its ID.

        Returns None when the item is not found or the request fails.
        """
        if media_id is None or media_id == "":
            return None
        url = self.IMAGES_URL if media_type == "image" else self.VIDEOS_URL
        params = {'key': self.api_key, 'id': str(media_id)}
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            hits = response.json().get('hits', [])
        except Exception as e:
            logger.warning("Pixabay lookup for %s %s failed: %s", media_type, media_id, e)
            return None
        if not hits:
            return None
        info = PixabayMediaInfo(hits[0], media_type="image" if media_type == "image" else "video")
        return info if info.download_url else None

    def download(self, media_info: PixabayMediaInfo, output_path: Path,
                 chunk_size: int = 8192) -> Path:
        from video_searcher import download_file
        download_file(media_info.download_url, output_path, chunk_size=max(chunk_size, 65536))
        return output_path


class CachedPixabaySearcher(PixabaySearcher):
    """PixabaySearcher with Redis-backed result caching."""

    def __init__(self, api_key: str, redis_client=None, cache_ttl: int = PIXABAY_CACHE_TTL):
        super().__init__(api_key)
        self._redis = redis_client
        self._cache_ttl = cache_ttl
        self._cache_hits = 0
        self._cache_misses = 0

    @staticmethod
    def _cache_key(keywords: List[str], orientation: str, media_type: str, page: int = 1) -> str:
        normalized = "|".join(sorted(k.lower().strip() for k in keywords))
        raw = f"pixabay_{media_type}:{orientation}:{normalized}"
        if page and int(page) > 1:
            raw += f":page={int(page)}"
        return f"pixabay_{media_type}_cache:{hashlib.md5(raw.encode()).hexdigest()}"

    def search_videos(self, keywords: List[str], min_duration: float = 5.0,
                      max_results: int = 20,
                      orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any',
                      page: int = 1) -> List[PixabayMediaInfo]:
        key = self._cache_key(keywords, orientation or 'any', 'video', page)
        if self._redis:
            try:
                cached = self._redis.get(key)
                hit = cached_results(cached, max_results) if cached else None
                if hit is not None:
                    self._cache_hits += 1
                    videos = [PixabayMediaInfo(d, media_type="video") for d in hit]
                    return [v for v in videos if v.duration >= min_duration][:max_results]
            except Exception:
                pass

        self._cache_misses += 1
        if page and int(page) > 1:
            videos = super().search_videos(keywords, min_duration, max_results, orientation, page=page)
        else:
            videos = super().search_videos(keywords, min_duration, max_results, orientation)

        if self._redis and videos:
            try:
                serializable = []
                for v in videos:
                    serializable.append({
                        'id': v.id,
                        'pageURL': v.url,
                        'user': v.author,
                        'duration': v.duration,
                        'videos': {
                            'large': {'url': v.download_url, 'width': v.width, 'height': v.height},
                            'small': {'url': v.preview_url, 'width': 960, 'height': 540},
                        },
                    })
                self._redis.setex(key, self._cache_ttl,
                                  json.dumps({'limit': max_results, 'items': serializable}))
            except Exception:
                pass

        return videos

    def search_images(self, keywords: List[str], max_results: int = 10,
                      orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> List[PixabayMediaInfo]:
        if self._redis:
            key = self._cache_key(keywords, orientation or 'any', 'image')
            try:
                cached = self._redis.get(key)
                hit = cached_results(cached, max_results) if cached else None
                if hit is not None:
                    self._cache_hits += 1
                    return [PixabayMediaInfo(d, media_type="image") for d in hit][:max_results]
            except Exception:
                pass

        self._cache_misses += 1
        images = super().search_images(keywords, max_results, orientation)

        if self._redis and images:
            key = self._cache_key(keywords, orientation or 'any', 'image')
            try:
                serializable = []
                for img in images:
                    serializable.append({
                        'id': img.id,
                        'pageURL': img.url,
                        'user': img.author,
                        'largeImageURL': img.download_url,
                        'webformatURL': img.preview_url,
                        'imageWidth': img.width,
                        'imageHeight': img.height,
                    })
                self._redis.setex(key, self._cache_ttl,
                                  json.dumps({'limit': max_results, 'items': serializable}))
            except Exception:
                pass

        return images

    @property
    def cache_stats(self) -> Dict:
        return {
            'hits': self._cache_hits,
            'misses': self._cache_misses,
            'hit_rate': self._cache_hits / max(1, self._cache_hits + self._cache_misses),
        }
