"""Cloudflare R2 storage service using boto3 S3-compatible API."""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class R2StorageService:
    """Cloudflare R2 storage operations via the S3-compatible API."""

    def __init__(self):
        from app.core.config import settings
        self._settings = settings
        self._client_obj = None

    @property
    def _bucket(self):
        return self._settings.r2_bucket_name

    @property
    def _client(self):
        """boto3 client, created on first use so importing this module never
        needs R2 credentials (R2 is optional and off by default)."""
        if self._client_obj is None and self._settings.use_r2_storage:
            import boto3
            from botocore.config import Config as BotoConfig

            account = (self._settings.r2_account_id or "").strip()
            if not account:
                raise RuntimeError("USE_R2_STORAGE is on but R2_ACCOUNT_ID is empty")
            self._client_obj = boto3.client(
                "s3",
                endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                aws_access_key_id=self._settings.r2_access_key_id,
                aws_secret_access_key=self._settings.r2_secret_access_key,
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "adaptive"},
                ),
                region_name="auto",
            )
        return self._client_obj

    @property
    def enabled(self) -> bool:
        return bool(self._settings.use_r2_storage)

    # ─── Content-type helpers ───

    _CONTENT_TYPES = {
        ".mp4": "video/mp4",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ass": "text/plain",
        ".json": "application/json",
    }

    def _content_type(self, path: Path) -> Optional[str]:
        return self._CONTENT_TYPES.get(path.suffix.lower())

    # ─── Core operations ───

    def upload_file(self, local_path: Path, r2_key: str,
                    content_type: Optional[str] = None) -> str:
        """Upload a local file to R2. Returns the R2 key.

        Raises on failure — no silent fallback.
        """
        extra_args = {}
        ct = content_type or self._content_type(local_path)
        if ct:
            extra_args["ContentType"] = ct

        self._client.upload_file(
            str(local_path), self._bucket, r2_key, ExtraArgs=extra_args or None
        )
        logger.info("Uploaded %s → r2://%s/%s", local_path.name, self._bucket, r2_key)
        return r2_key

    def download_file(self, r2_key: str, local_path: Path) -> Path:
        """Download an R2 object to a local file. Returns the local path."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, r2_key, str(local_path))
        logger.info("Downloaded r2://%s/%s → %s", self._bucket, r2_key, local_path)
        return local_path

    def delete_file(self, r2_key: str) -> bool:
        """Delete a single R2 object. Returns True on success."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=r2_key)
            return True
        except Exception as e:
            logger.warning("Failed to delete R2 key %s: %s", r2_key, e)
            return False

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a key prefix. Returns count deleted."""
        deleted = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={
                    "Objects": [{"Key": obj["Key"]} for obj in objects],
                    "Quiet": True,
                },
            )
            deleted += len(objects)
        logger.info("Deleted %d object(s) under prefix %s", deleted, prefix)
        return deleted

    def generate_signed_url(self, r2_key: str,
                            expiry_seconds: Optional[int] = None,
                            content_disposition: Optional[str] = None) -> str:
        """Generate a pre-signed GET URL for an R2 object."""
        params: dict = {"Bucket": self._bucket, "Key": r2_key}
        if content_disposition:
            params["ResponseContentDisposition"] = content_disposition
        return self._client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expiry_seconds or self._settings.r2_signed_url_expiry_seconds,
        )

    def file_exists(self, r2_key: str) -> bool:
        """Check whether an object exists in R2."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=r2_key)
            return True
        except Exception:
            return False

    def upload_directory(self, local_dir: Path, r2_prefix: str) -> int:
        """Upload all files in a local directory to R2, preserving structure.

        Returns count of files uploaded.
        """
        count = 0
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_dir).as_posix()
                r2_key = r2_prefix + relative
                self.upload_file(file_path, r2_key)
                count += 1
        logger.info("Uploaded %d file(s) from %s → %s", count, local_dir, r2_prefix)
        return count

    def download_directory(self, r2_prefix: str, local_dir: Path) -> int:
        """Download all objects under a prefix to a local directory.

        Returns count of files downloaded.
        """
        count = 0
        local_dir.mkdir(parents=True, exist_ok=True)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=r2_prefix):
            for obj in page.get("Contents", []):
                r2_key = obj["Key"]
                relative = r2_key[len(r2_prefix):]
                if not relative:
                    continue
                local_path = local_dir / relative
                self.download_file(r2_key, local_path)
                count += 1
        logger.info("Downloaded %d file(s) from %s → %s", count, r2_prefix, local_dir)
        return count


# ─── Key builder functions — centralized naming convention ───

def r2_video_key(user_id: int, video_id: int, version: int = 1) -> str:
    """R2 key for the final rendered video."""
    suffix = f"_v{version}" if version > 1 else ""
    return f"users/{user_id}/videos/{video_id}/video{suffix}.mp4"


def r2_thumbnail_key(user_id: int, video_id: int) -> str:
    """R2 key for the video thumbnail."""
    return f"users/{user_id}/videos/{video_id}/thumbnail.jpg"


def r2_project_prefix(user_id: int, video_id: int) -> str:
    """R2 prefix for all project/editor files (project.json, clips, audio, etc.)."""
    return f"users/{user_id}/videos/{video_id}/project/"


def r2_preview_key(user_id: int, video_id: int) -> str:
    """R2 key for the subtitle-free editor preview video."""
    return f"users/{user_id}/videos/{video_id}/preview.mp4"


def r2_video_prefix(user_id: int, video_id: int) -> str:
    """R2 prefix covering all objects for a video (used with delete_prefix)."""
    return f"users/{user_id}/videos/{video_id}/"


# Global singleton — initialised once at import time
def r2_tts_preview_key(user_id: int, fish_voice_id: str) -> str:
    """R2 key for a private cloned Fish voice preview clip."""
    return f"users/{user_id}/tts/fish/{fish_voice_id}/preview.wav"


r2_storage = R2StorageService()
