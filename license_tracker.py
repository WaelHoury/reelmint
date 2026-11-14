"""License tracking for video assets used in generation."""

from dataclasses import dataclass, asdict
from typing import Iterable, List, Optional, Tuple
from pathlib import Path
import json
from datetime import datetime


@dataclass
class Asset:
    """Represents a single video asset used in generation."""
    video_url: str
    author: str
    provider: str  # "Pexels", "Pixabay", etc.
    license_name: str
    scene_index: int
    download_timestamp: str = None

    def __post_init__(self):
        if self.download_timestamp is None:
            self.download_timestamp = datetime.now().isoformat()


class LicenseTracker:
    """Tracks all assets used in video generation for proper attribution."""

    def __init__(self):
        self.assets: List[Asset] = []

    def add_asset(self, video_info: dict, scene_index: int) -> None:
        """Add an asset to the tracking list.

        Args:
            video_info: Dictionary with keys: url, author, provider, license
            scene_index: Index of the scene this asset is used in
        """
        asset = Asset(
            video_url=video_info.get('url', ''),
            author=video_info.get('author', 'Unknown'),
            provider=video_info.get('provider', 'Unknown'),
            license_name=video_info.get('license', 'Unknown License'),
            scene_index=scene_index
        )
        self.assets.append(asset)

    def export_credits_txt(self, output_path: Path) -> None:
        """Export credits in human-readable text format.

        Args:
            output_path: Path to write credits.txt file
        """
        output_path = Path(output_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("VIDEO CREDITS & ATTRIBUTIONS\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total assets used: {len(self.assets)}\n\n")

            # Group by provider
            by_provider = {}
            for asset in self.assets:
                if asset.provider not in by_provider:
                    by_provider[asset.provider] = []
                by_provider[asset.provider].append(asset)

            for provider, assets in by_provider.items():
                f.write(f"\n{provider.upper()}\n")
                f.write("-" * 70 + "\n")

                for asset in assets:
                    f.write(f"\nScene {asset.scene_index + 1}:\n")
                    f.write(f"  Author: {asset.author}\n")
                    f.write(f"  License: {asset.license_name}\n")
                    f.write(f"  URL: {asset.video_url}\n")

            # Add license summaries
            f.write("\n\n" + "=" * 70 + "\n")
            f.write("LICENSE INFORMATION\n")
            f.write("=" * 70 + "\n\n")

            if "Pexels" in by_provider:
                f.write("PEXELS LICENSE:\n")
                f.write("All photos and videos on Pexels are free to use.\n")
                f.write("Attribution is not required but appreciated.\n")
                f.write("https://www.pexels.com/license/\n\n")

            if "Pixabay" in by_provider:
                f.write("PIXABAY LICENSE:\n")
                f.write("All content on Pixabay is free for commercial use.\n")
                f.write("No attribution required.\n")
                f.write("https://pixabay.com/service/license/\n\n")

    def export_credits_json(self, output_path: Path) -> None:
        """Export credits in machine-readable JSON format.

        Args:
            output_path: Path to write credits.json file
        """
        output_path = Path(output_path)

        data = {
            "generated_at": datetime.now().isoformat(),
            "total_assets": len(self.assets),
            "assets": [asdict(asset) for asset in self.assets]
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def clear(self) -> None:
        """Clear all tracked assets."""
        self.assets.clear()

    def get_asset_count(self) -> int:
        """Get total number of tracked assets."""
        return len(self.assets)

    def get_assets_by_scene(self, scene_index: int) -> List[Asset]:
        """Get all assets used in a specific scene."""
        return [asset for asset in self.assets if asset.scene_index == scene_index]


def license_for_provider(provider: Optional[str]) -> Tuple[str, str]:
    """(provider display name, license name) for a stock media provider."""
    name = (provider or "Pexels").strip() or "Pexels"
    if name.lower() == "pixabay":
        return "Pixabay", "Pixabay License"
    if name.lower() == "pexels":
        return "Pexels", "Pexels License"
    return name, "Unknown License"


def credits_paths(output_path: Path) -> Tuple[Path, Path]:
    """The ``<name>_credits.txt`` / ``.json`` files that sit next to a render."""
    output_path = Path(output_path)
    stem = output_path.parent / f"{output_path.stem}_credits"
    return stem.with_suffix(".txt"), stem.with_suffix(".json")


def write_credits_for_clips(clips: Iterable[dict], output_path: Path) -> Tuple[Path, Path]:
    """Rewrite a render's credits files from its current clip list.

    ``clips`` are project/session clip dicts (``author``, ``author_url``,
    ``provider``). Loop copies repeat shot 0, so they are not listed twice.
    Returns the (txt, json) paths written.
    """
    tracker = LicenseTracker()
    for i, clip in enumerate(clips or []):
        if clip.get("loop_copy"):
            continue
        provider, license_name = license_for_provider(clip.get("provider"))
        tracker.add_asset({
            "url": clip.get("author_url") or "",
            "author": clip.get("author") or "Unknown",
            "provider": provider,
            "license": license_name,
        }, clip.get("index", i))
    txt_path, json_path = credits_paths(output_path)
    tracker.export_credits_txt(txt_path)
    tracker.export_credits_json(json_path)
    return txt_path, json_path
