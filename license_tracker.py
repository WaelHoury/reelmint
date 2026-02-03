"""License tracking for video assets used in generation."""

from dataclasses import dataclass, asdict
from typing import List
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
