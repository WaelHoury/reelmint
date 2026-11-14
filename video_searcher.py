"""Video search and download from stock video providers (Pexels)."""

import hashlib
import json
import logging
import requests
from typing import List, Dict, Optional, Literal
from pathlib import Path
import time

logger = logging.getLogger(__name__)



def download_file(url: str, output_path, chunk_size: int = 65536,
                  connect_read_timeout: float = 30.0, deadline: float = None):
    """Stream a URL to disk with a per-read timeout AND an overall deadline.

    A connection that trickles bytes never trips the per-read timeout, so the
    overall deadline (DOWNLOAD_DEADLINE_SECONDS, default 180) makes a slow
    download fail fast instead of blocking a render for an hour. The partial
    file is removed on failure.
    """
    import os
    import time
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if deadline is None:
        deadline = float(os.getenv("DOWNLOAD_DEADLINE_SECONDS", "180"))
    started = time.monotonic()
    try:
        with requests.get(url, stream=True, timeout=connect_read_timeout) as response:
            response.raise_for_status()
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                    if time.monotonic() - started > deadline:
                        raise requests.Timeout(
                            f"Download took longer than {deadline:.0f}s: {url[:80]}")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


class VideoInfo:
    """Information about a stock video."""

    def __init__(self, data: dict, provider: str = "Pexels"):
        """Initialize from API response data.

        Args:
            data: Video data from API
            provider: Source provider name
        """
        self.provider = provider
        self.media_type = "video"
        self.id = data.get('id')
        self.url = data.get('url', '')
        self.author = data.get('user', {}).get('name', 'Unknown')
        self.duration = data.get('duration', 0)

        # Get best quality video file (for final render)
        video_files = data.get('video_files', [])
        best_file = self._select_best_quality(video_files)
        self.download_url = best_file.get('link', '') if best_file else ''
        self.width = best_file.get('width', 0) if best_file else 0
        self.height = best_file.get('height', 0) if best_file else 0

        # Get lighter preview file (for UI preview cards)
        preview_file = self._select_preview_quality(video_files)
        self.preview_url = preview_file.get('link', '') if preview_file else self.download_url

        # License info
        self.license = "Pexels License"
        self.license_url = "https://www.pexels.com/license/"

    @property
    def orientation(self) -> str:
        """Determine video orientation based on dimensions.

        Returns:
            'landscape', 'portrait', or 'square'
        """
        if self.width == 0 or self.height == 0:
            return 'unknown'

        aspect_ratio = self.width / self.height

        if aspect_ratio > 1.1:
            return 'landscape'
        elif aspect_ratio < 0.9:
            return 'portrait'
        else:
            return 'square'

    # Renders are 1080p (1920x1080, 1080x1920 or 1080x1080), so the short side
    # of the source only needs to reach 1080. Bigger files (2K/4K) are 3-4x the
    # download for no visible gain.
    TARGET_SHORT_SIDE = 1080

    def _select_best_quality(self, video_files: List[Dict]) -> Dict:
        """Pick the smallest MP4 that is still full 1080p for the final render.

        Falls back to the largest available file when nothing reaches 1080p.
        """
        if not video_files:
            return {}

        mp4_files = [vf for vf in video_files if vf.get('file_type') == 'video/mp4'] or list(video_files)

        def short_side(vf):
            return min(vf.get('width') or 0, vf.get('height') or 0)

        sufficient = [vf for vf in mp4_files if short_side(vf) >= self.TARGET_SHORT_SIDE]
        if sufficient:
            return min(sufficient, key=lambda vf: (short_side(vf), vf.get('width') or 0))
        return max(mp4_files, key=lambda vf: (short_side(vf), vf.get('width') or 0))

    def _select_preview_quality(self, video_files: List[Dict]) -> Dict:
        """Select a lighter MP4 variant for UI preview cards.

        Targets ~540p (960x540 landscape or equivalent). Falls back to
        smallest available if nothing matches the target range.
        """
        if not video_files:
            return {}

        mp4_files = [vf for vf in video_files if vf.get('file_type') == 'video/mp4']
        if not mp4_files:
            mp4_files = video_files

        # Sort by width ascending
        mp4_files.sort(key=lambda x: x.get('width', 0))

        # Pick the first file whose width is >= 480 (decent preview quality)
        for vf in mp4_files:
            if vf.get('width', 0) >= 480:
                return vf

        # Fallback to smallest available
        return mp4_files[0] if mp4_files else {}

    def to_dict(self) -> Dict:
        """Convert to dictionary for license tracking."""
        return {
            'url': self.url,
            'author': self.author,
            'provider': self.provider,
            'license': self.license
        }


class VideoSearcher:
    """Search and download stock videos from Pexels."""

    PEXELS_API_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str):
        """Initialize searcher with API key.

        Args:
            api_key: Pexels API key
        """
        if not api_key:
            raise ValueError("Pexels API key is required")

        self.api_key = api_key
        self.headers = {
            'Authorization': api_key
        }

    def get_video_by_id(self, video_id: str) -> Optional[VideoInfo]:
        """Fetch a single video by its Pexels ID.

        Args:
            video_id: Pexels video ID

        Returns:
            VideoInfo object, or None if not found
        """
        url = f"https://api.pexels.com/videos/videos/{video_id}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return VideoInfo(response.json())
        except requests.RequestException:
            return None

    def search(self, keywords: List[str], min_duration: float = 5.0,
               max_results: int = 20,
               orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any',
               page: int = 1) -> List[VideoInfo]:
        """Search for videos matching keywords.

        Args:
            keywords: List of search terms
            min_duration: Minimum video duration in seconds
            max_results: Maximum number of results to return
            orientation: Filter by orientation ('landscape', 'portrait', 'square', or 'any')
            page: Result page (1-based) of the Pexels search

        Returns:
            List of VideoInfo objects

        Raises:
            requests.RequestException: If API request fails
        """
        # Join keywords into search query
        query = " ".join(keywords)

        # Request more results than needed to account for orientation filtering
        per_page = min(max_results * 3, 80) if orientation != 'any' else min(max_results, 80)

        params = {
            'query': query,
            'per_page': per_page,
            'page': max(1, int(page or 1)),
            'orientation': orientation if orientation in ['landscape', 'portrait', 'square'] else None
        }

        # Remove None values from params
        params = {k: v for k, v in params.items() if v is not None}

        response = requests.get(
            self.PEXELS_API_URL,
            headers=self.headers,
            params=params,
            timeout=10
        )

        response.raise_for_status()
        data = response.json()

        # Parse results
        videos = []
        for video_data in data.get('videos', []):
            video_info = VideoInfo(video_data, provider="Pexels")

            # Filter by duration and orientation
            if video_info.duration >= min_duration and video_info.download_url:
                # Additional client-side orientation filtering for accuracy
                if orientation == 'any' or video_info.orientation == orientation:
                    videos.append(video_info)

            if len(videos) >= max_results:
                break

        return videos

    def download(self, video_info: VideoInfo, output_path: Path,
                 chunk_size: int = 8192) -> Path:
        """Download video file.

        Args:
            video_info: Video information with download URL
            output_path: Where to save video file
            chunk_size: Download chunk size in bytes

        Returns:
            Path to downloaded file

        Raises:
            requests.RequestException: If download fails
        """
        download_file(video_info.download_url, output_path, chunk_size=max(chunk_size, 65536))
        return output_path

    def search_and_download(self, keywords: List[str], output_dir: Path,
                            max_videos: int = 5) -> List[Dict]:
        """Search and download videos in one operation.

        Args:
            keywords: Search terms
            output_dir: Directory to save videos
            max_videos: Maximum number of videos to download

        Returns:
            List of dicts with 'path' and 'info' keys

        Raises:
            requests.RequestException: If API or download fails
        """
        # Search
        videos = self.search(keywords, max_results=max_videos)

        if not videos:
            raise ValueError(f"No videos found for keywords: {keywords}")

        # Download
        downloaded = []
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, video_info in enumerate(videos[:max_videos]):
            try:
                output_path = output_dir / f"video_{i}_{video_info.id}.mp4"
                downloaded_path = self.download(video_info, output_path)

                downloaded.append({
                    'path': downloaded_path,
                    'info': video_info
                })

                # Rate limiting - be nice to API
                if i < len(videos) - 1:
                    time.sleep(0.5)

            except Exception as e:
                print(f"Warning: Failed to download video {i}: {e}")
                continue

        return downloaded

    def test_api_key(self) -> bool:
        """Test if API key is valid.

        Returns:
            True if API key works, False otherwise
        """
        try:
            params = {
                'query': 'test',
                'per_page': 1,
                'page': 1
            }

            response = requests.get(
                self.PEXELS_API_URL,
                headers=self.headers,
                params=params,
                timeout=5
            )

            return response.status_code == 200

        except Exception:
            return False


def cached_results(raw, max_results: int) -> Optional[list]:
    """Cached result list if it can answer a request for ``max_results``, else None.

    Entries are stored as {"limit": n, "items": [...]}. An entry made for a
    smaller request is only reused if it holds fewer items than its limit
    (the source had no more). Legacy bare lists are reused when long enough.
    """
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


class CachedVideoSearcher(VideoSearcher):
    """VideoSearcher with Redis-backed result caching."""

    def __init__(self, api_key: str, redis_client=None, cache_ttl: int = 21600):
        super().__init__(api_key)
        self._redis = redis_client
        self._cache_ttl = cache_ttl  # default 6 hours
        self._cache_hits = 0
        self._cache_misses = 0

    @staticmethod
    def _cache_key(keywords: List[str], orientation: str, page: int = 1) -> str:
        """Build deterministic cache key from sorted keywords + orientation (+ page)."""
        normalized = "|".join(sorted(k.lower().strip() for k in keywords))
        raw = f"pexels:{orientation}:{normalized}"
        if page and int(page) > 1:
            raw += f":page={int(page)}"
        return f"pexels_cache:{hashlib.md5(raw.encode()).hexdigest()}"

    def search(self, keywords: List[str], min_duration: float = 5.0,
               max_results: int = 20,
               orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any',
               page: int = 1) -> List[VideoInfo]:
        """Search with Redis cache layer."""
        key = self._cache_key(keywords, orientation or 'any', page)
        if self._redis:
            try:
                cached = self._redis.get(key)
                hit = cached_results(cached, max_results) if cached else None
                if hit is not None:
                    self._cache_hits += 1
                    videos = [VideoInfo(d) for d in hit]
                    return [v for v in videos if v.duration >= min_duration][:max_results]
            except Exception:
                pass  # cache read failure is non-fatal

        self._cache_misses += 1
        if page and int(page) > 1:
            videos = super().search(keywords, min_duration, max_results, orientation, page=page)
        else:
            videos = super().search(keywords, min_duration, max_results, orientation)

        # Store raw API data in cache for reconstruction
        if self._redis and videos:
            try:
                # Store enough data to reconstruct VideoInfo objects
                serializable = []
                for v in videos:
                    serializable.append({
                        'id': v.id,
                        'url': v.url,
                        'user': {'name': v.author},
                        'duration': v.duration,
                        'video_files': [
                            {'link': v.download_url, 'width': v.width, 'height': v.height, 'file_type': 'video/mp4'},
                            {'link': v.preview_url, 'width': 960, 'height': 540, 'file_type': 'video/mp4'},
                        ],
                    })
                self._redis.setex(key, self._cache_ttl,
                                  json.dumps({'limit': max_results, 'items': serializable}))
            except Exception:
                pass  # cache write failure is non-fatal

        return videos

    @property
    def cache_stats(self) -> Dict:
        return {
            'hits': self._cache_hits,
            'misses': self._cache_misses,
            'hit_rate': self._cache_hits / max(1, self._cache_hits + self._cache_misses),
        }
