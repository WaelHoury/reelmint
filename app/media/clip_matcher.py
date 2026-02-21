"""Semantic video matching using OpenCLIP.

Extracts frames from videos and scores them against text descriptions.
Caches frame embeddings for reuse across scenes.
"""

import torch
from pathlib import Path
from typing import List, Dict
import open_clip
from PIL import Image
import subprocess
import json
import hashlib


class CLIPMatcher:
    """Scores videos against text descriptions using OpenCLIP."""

    def __init__(self,
                 model_name: str = "ViT-B-32",
                 pretrained: str = "laion2b_s34b_b79k",
                 cache_dir: Path = None,
                 ffmpeg_path: str = "ffmpeg",
                 ffprobe_path: str = "ffprobe"):
        """Initialize CLIP matcher.

        Args:
            model_name: OpenCLIP model architecture
            pretrained: Pretrained weights identifier
            cache_dir: Directory for caching embeddings
            ffmpeg_path: Path to ffmpeg executable
            ffprobe_path: Path to ffprobe executable
        """
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path

        # Load CLIP model
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
            device=self.device
        )
        self.tokenizer = open_clip.get_tokenizer(model_name)

        # Set to eval mode
        self.model.eval()

        # Setup cache
        self.cache_dir = Path(cache_dir) if cache_dir else Path("cache/clip")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_cache_index()

    def _load_cache_index(self):
        """Load cache index from disk."""
        self.cache_index_file = self.cache_dir / "index.json"
        if self.cache_index_file.exists():
            with open(self.cache_index_file, 'r') as f:
                self.cache_index = json.load(f)
        else:
            self.cache_index = {}

    def _save_cache_index(self):
        """Save cache index to disk."""
        with open(self.cache_index_file, 'w') as f:
            json.dump(self.cache_index, f)

    def _get_video_hash(self, video_path: Path) -> str:
        """Generate hash for video based on path, size, and mtime.

        Args:
            video_path: Path to video file

        Returns:
            Hash string for cache key
        """
        video_path = Path(video_path)
        stat = video_path.stat()

        hash_input = f"{video_path}_{stat.st_size}_{stat.st_mtime}"
        return hashlib.md5(hash_input.encode()).hexdigest()

    def _get_duration(self, video_path: Path) -> float:
        """Get video duration using ffprobe.

        Args:
            video_path: Path to video file

        Returns:
            Duration in seconds
        """
        cmd = [
            self.ffprobe, '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(video_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())

    def _extract_frames(self, video_path: Path, timestamps: List[float],
                       output_dir: Path) -> List[Path]:
        """Extract frames at specific timestamps in a single FFmpeg call.

        Uses the select filter to grab all frames in one decode pass,
        instead of launching a separate FFmpeg process per timestamp.

        Args:
            video_path: Path to video file
            timestamps: List of timestamps in seconds
            output_dir: Directory to save frames

        Returns:
            List of paths to extracted frame images
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build select expression: between(t,ts,ts+0.1) for each timestamp
        select_expr = '+'.join(f'between(t\\,{ts:.3f}\\,{ts+0.1:.3f})' for ts in timestamps)

        output_pattern = str(output_dir / 'frame_%d.jpg')

        cmd = [
            self.ffmpeg,
            '-i', str(video_path),
            '-vf', f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            '-frames:v', str(len(timestamps)),
            '-vsync', 'vfr',
            '-q:v', '2',
            '-start_number', '0',
            '-y',
            output_pattern
        ]

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        # Collect output paths, falling back to individual extraction if select fails
        frame_paths = []
        for i in range(len(timestamps)):
            path = output_dir / f"frame_{i}.jpg"
            if path.exists():
                frame_paths.append(path)

        # Fallback: if select filter didn't produce all frames, extract individually
        if len(frame_paths) < len(timestamps):
            frame_paths = []
            for i, timestamp in enumerate(timestamps):
                output_path = output_dir / f"frame_{i}.jpg"
                cmd = [
                    self.ffmpeg,
                    '-ss', str(timestamp),
                    '-i', str(video_path),
                    '-frames:v', '1',
                    '-q:v', '2',
                    '-y',
                    str(output_path)
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
                frame_paths.append(output_path)

        return frame_paths

    def _encode_frames(self, frame_paths: List[Path]) -> torch.Tensor:
        """Encode frames to CLIP embeddings.

        Args:
            frame_paths: Paths to frame image files

        Returns:
            Tensor of frame embeddings [num_frames, embedding_dim]
        """
        images = []

        for frame_path in frame_paths:
            image = Image.open(frame_path).convert('RGB')
            images.append(self.preprocess(image))

        # Batch process
        image_tensor = torch.stack(images).to(self.device)

        with torch.no_grad():
            image_features = self.model.encode_image(image_tensor)
            image_features /= image_features.norm(dim=-1, keepdim=True)

        return image_features.cpu()

    def _encode_text(self, text: str) -> torch.Tensor:
        """Encode text to CLIP embedding.

        Args:
            text: Text description

        Returns:
            Text embedding tensor [1, embedding_dim]
        """
        text_tokens = self.tokenizer([text]).to(self.device)

        with torch.no_grad():
            text_features = self.model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)

        return text_features.cpu()

    def _get_frame_embeddings(self, video_path: Path) -> torch.Tensor:
        """Get or compute frame embeddings for video.

        Uses cache if available, otherwise computes and caches.

        Args:
            video_path: Path to video file

        Returns:
            Frame embeddings tensor [num_frames, embedding_dim]
        """
        video_path = Path(video_path)
        video_hash = self._get_video_hash(video_path)

        # Check cache
        if video_hash in self.cache_index:
            cache_file = self.cache_dir / f"{video_hash}.pt"
            if cache_file.exists():
                return torch.load(cache_file)

        # Extract frames at 25%, 50%, 75%
        duration = self._get_duration(video_path)
        timestamps = [duration * 0.25, duration * 0.50, duration * 0.75]

        # Extract frames
        temp_dir = self.cache_dir / f"temp_{video_hash}"
        frame_paths = self._extract_frames(video_path, timestamps, temp_dir)

        # Encode frames
        frame_embeddings = self._encode_frames(frame_paths)

        # Cache embeddings
        cache_file = self.cache_dir / f"{video_hash}.pt"
        torch.save(frame_embeddings, cache_file)

        self.cache_index[video_hash] = {
            'video_path': str(video_path),
            'cache_file': str(cache_file)
        }
        self._save_cache_index()

        # Clean up temp frames
        for frame_path in frame_paths:
            frame_path.unlink(missing_ok=True)
        temp_dir.rmdir()

        return frame_embeddings

    def score_videos(self, video_paths: List[Path], scene_text: str) -> Dict[Path, float]:
        """Score multiple videos against scene text description.

        Args:
            video_paths: List of video file paths
            scene_text: Text description to match against

        Returns:
            Dictionary mapping video paths to similarity scores
        """
        # Encode text once
        text_embedding = self._encode_text(scene_text)

        scores = {}

        for video_path in video_paths:
            video_path = Path(video_path)

            # Get frame embeddings (cached or computed)
            frame_embeddings = self._get_frame_embeddings(video_path)

            # Average frame embeddings
            video_embedding = frame_embeddings.mean(dim=0, keepdim=True)

            # Compute similarity
            similarity = (video_embedding @ text_embedding.T).item()

            scores[video_path] = similarity

        return scores

    def clear_cache(self):
        """Clear all cached embeddings."""
        for cache_file in self.cache_dir.glob("*.pt"):
            cache_file.unlink()

        self.cache_index = {}
        self._save_cache_index()

    def get_cache_size(self) -> int:
        """Get number of cached video embeddings."""
        return len(self.cache_index)
