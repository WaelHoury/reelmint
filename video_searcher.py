"""Video search and download from stock video providers (Pexels)."""

import hashlib
import json
import logging
import requests
from typing import List, Dict, Optional, Literal
from pathlib import Path
import time

logger = logging.getLogger(__name__)


class VideoInfo:
    """Information about a stock video."""

    def __init__(self, data: dict, provider: str = "Pexels"):
        """Initialize from API response data.

        Args:
            data: Video data from API
            provider: Source provider name
        """
        self.provider = provider
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

    def _select_best_quality(self, video_files: List[Dict]) -> Dict:
        """Select best quality video file.

        Prioritizes:
        1. HD quality (1920x1080)
        2. Highest width
        3. MP4 format

        Args:
            video_files: List of video file options from API

        Returns:
            Best quality video file dict with link, width, height
        """
        if not video_files:
            return {}

        # Filter for MP4 files
        mp4_files = [vf for vf in video_files if vf.get('file_type') == 'video/mp4']

        if not mp4_files:
            mp4_files = video_files

        # Sort by width (descending)
        mp4_files.sort(key=lambda x: x.get('width', 0), reverse=True)

        # Return first (best quality) file
        return mp4_files[0] if mp4_files else {}

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
               orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> List[VideoInfo]:
        """Search for videos matching keywords.

        Args:
            keywords: List of search terms
            min_duration: Minimum video duration in seconds
            max_results: Maximum number of results to return
            orientation: Filter by orientation ('landscape', 'portrait', 'square', or 'any')

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
            'page': 1,
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
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        response = requests.get(video_info.download_url, stream=True, timeout=30)
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)

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


class CachedVideoSearcher(VideoSearcher):
    """VideoSearcher with Redis-backed result caching."""

    def __init__(self, api_key: str, redis_client=None, cache_ttl: int = 21600):
        super().__init__(api_key)
        self._redis = redis_client
        self._cache_ttl = cache_ttl  # default 6 hours
        self._cache_hits = 0
        self._cache_misses = 0

    @staticmethod
    def _cache_key(keywords: List[str], orientation: str) -> str:
        """Build deterministic cache key from sorted keywords + orientation."""
        normalized = "|".join(sorted(k.lower().strip() for k in keywords))
        raw = f"pexels:{orientation}:{normalized}"
        return f"pexels_cache:{hashlib.md5(raw.encode()).hexdigest()}"

    def search(self, keywords: List[str], min_duration: float = 5.0,
               max_results: int = 20,
               orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> List[VideoInfo]:
        """Search with Redis cache layer."""
        if self._redis:
            key = self._cache_key(keywords, orientation or 'any')
            try:
                cached = self._redis.get(key)
                if cached:
                    self._cache_hits += 1
                    data_list = json.loads(cached)
                    return [VideoInfo(d) for d in data_list][:max_results]
            except Exception:
                pass  # cache read failure is non-fatal

        self._cache_misses += 1
        videos = super().search(keywords, min_duration, max_results, orientation)

        # Store raw API data in cache for reconstruction
        if self._redis and videos:
            key = self._cache_key(keywords, orientation or 'any')
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
                self._redis.setex(key, self._cache_ttl, json.dumps(serializable))
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
