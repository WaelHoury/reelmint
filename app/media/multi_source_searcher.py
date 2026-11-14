"""Multi-source media searcher aggregating Pexels + Pixabay results."""

import logging
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path
from typing import List, Optional, Literal

logger = logging.getLogger(__name__)


class MultiSourceSearcher:
    """Aggregates multiple stock media sources into a single search interface."""

    def __init__(self, pexels_searcher, pixabay_searcher=None,
                 enable_pixabay: bool = False, enable_image_fallback: bool = False):
        self.pexels = pexels_searcher
        self.pixabay = pixabay_searcher
        self.enable_pixabay = enable_pixabay and pixabay_searcher is not None
        self.enable_image_fallback = enable_image_fallback

    @staticmethod
    def _merge_results(pexels_results: list, pixabay_results: list,
                       max_results: int) -> list:
        """Interleave providers so one source does not starve the other."""
        merged = []
        for pair in zip_longest(pexels_results, pixabay_results):
            for item in pair:
                if item is None:
                    continue
                merged.append(item)
                if len(merged) >= max_results:
                    return merged
        return merged

    def search(self, keywords: List[str], min_duration: float = 5.0,
               max_results: int = 20,
               orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> list:
        """Search for videos across all enabled sources.

        Searches Pexels and Pixabay in parallel when Pixabay is enabled.
        Results are interleaved so both providers stay visible near the top.
        Pixabay failures are non-fatal.
        """
        if not self.enable_pixabay:
            return self.pexels.search(keywords, min_duration, max_results, orientation)

        with ThreadPoolExecutor(max_workers=2) as pool:
            pexels_future = pool.submit(
                self.pexels.search, keywords, min_duration, max_results, orientation)
            pixabay_future = pool.submit(
                self.pixabay.search_videos, keywords, min_duration, max_results, orientation)

            pexels_results = pexels_future.result()

            try:
                pixabay_results = pixabay_future.result()
            except Exception as e:
                logger.warning("Pixabay search failed (non-fatal): %s", e)
                pixabay_results = []

        merged = self._merge_results(pexels_results, pixabay_results, max_results)
        logger.debug(
            "Multi-source search merged %d Pexels and %d Pixabay results into %d candidates",
            len(pexels_results),
            len(pixabay_results),
            len(merged),
        )
        return merged

    def search_images(self, keywords: List[str], max_results: int = 5,
                      orientation: Optional[Literal['landscape', 'portrait', 'square', 'any']] = 'any') -> list:
        """Search for images (Pixabay only). Used as fallback when no videos found."""
        if not self.enable_image_fallback or not self.pixabay:
            return []
        try:
            return self.pixabay.search_images(keywords, max_results, orientation)
        except Exception as e:
            logger.warning("Image fallback search failed: %s", e)
            return []

    def download(self, media_info, output_path: Path) -> Path:
        """Route download to correct provider based on media_info.provider."""
        if media_info.provider == "Pixabay":
            return self.pixabay.download(media_info, output_path)
        return self.pexels.download(media_info, output_path)

    def download_image(self, media_info, output_path: Path) -> Path:
        """Download a still image (for Ken Burns fallback)."""
        return self.pixabay.download(media_info, output_path)
