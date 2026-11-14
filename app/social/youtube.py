"""YouTube provider — OAuth + resumable video upload via YouTube Data API v3."""

import logging
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "openid",
    "email",
    "profile",
]

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


class YouTubeError(Exception):
    """Base YouTube provider error."""


class RetryableYouTubeError(YouTubeError):
    """Transient error that can be retried (5xx, 429, network)."""


class AuthError(YouTubeError):
    """Authentication/authorization error (401/403 — token expired or revoked)."""


class QuotaExceededError(YouTubeError):
    """The project's daily API quota or the channel's upload limit is used up.

    YouTube resets quotas at midnight Pacific time; retrying before then fails.
    """


# Error reasons that mean "out of quota / upload limit" rather than bad credentials.
QUOTA_REASONS = frozenset({
    "quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg",
})
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


def error_reasons(resp) -> set:
    """The ``error.errors[].reason`` values of a Google API error response."""
    try:
        data = resp.json()
    except Exception:
        return set()
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return set()
    reasons = {e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)}
    status = err.get("status")
    if status == "RESOURCE_EXHAUSTED":
        reasons.add("quotaExceeded")
    return {r for r in reasons if r}


def _raise_for_api_error(resp, during: str) -> None:
    """Map a failed upload response to the right error class (quota before auth)."""
    reasons = error_reasons(resp)
    if reasons & QUOTA_REASONS:
        raise QuotaExceededError(
            f"YouTube quota exceeded ({', '.join(sorted(reasons & QUOTA_REASONS))}) {during}"
        )
    if reasons & RATE_LIMIT_REASONS or resp.status_code == 429:
        raise RetryableYouTubeError(f"YouTube rate limited {during} ({resp.status_code})")
    if resp.status_code in (401, 403):
        raise AuthError(f"Upload auth failed {during}: {resp.status_code}")
    if resp.status_code >= 500:
        raise RetryableYouTubeError(f"YouTube server error {during}: {resp.status_code}")


def build_auth_url(state: str) -> str:
    """Build Google OAuth2 consent URL for YouTube access."""
    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Exchange authorization code for tokens.

    Returns: {access_token, refresh_token, expires_in, scope, token_type}
    """
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "redirect_uri": settings.youtube_redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=30)

    if resp.status_code != 200:
        logger.error("YouTube code exchange failed: %s %s", resp.status_code, resp.text)
        raise YouTubeError(f"Code exchange failed: {resp.status_code}")

    data = resp.json()
    if "access_token" not in data:
        raise YouTubeError("No access_token in token response")
    return data


def refresh_token(refresh_token_plain: str) -> dict:
    """Refresh an access token using a refresh token.

    Returns: {access_token, expires_in, scope, token_type}
    """
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "refresh_token": refresh_token_plain,
        "grant_type": "refresh_token",
    }, timeout=30)

    if resp.status_code == 401 or resp.status_code == 403:
        raise AuthError("Refresh token revoked or expired")
    if resp.status_code != 200:
        logger.error("YouTube token refresh failed: %s %s", resp.status_code, resp.text)
        raise RetryableYouTubeError(f"Token refresh failed: {resp.status_code}")

    data = resp.json()
    if "access_token" not in data:
        raise YouTubeError("No access_token in refresh response")
    return data


def fetch_identity(access_token: str) -> dict:
    """Fetch the authenticated user's YouTube channel info.

    Returns: {provider_user_id, display_name, username, avatar_url, metadata}
    """
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )

    if resp.status_code != 200:
        raise YouTubeError(f"Failed to fetch channel info: {resp.status_code}")

    data = resp.json()
    items = data.get("items", [])
    if not items:
        raise YouTubeError("No YouTube channel found for this account")

    channel = items[0]
    snippet = channel.get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})
    avatar = (thumbnails.get("default") or {}).get("url", "")

    return {
        "provider_user_id": channel["id"],
        "display_name": snippet.get("title", ""),
        "username": snippet.get("customUrl", ""),
        "avatar_url": avatar,
        "metadata": {"channel_id": channel["id"]},
    }


def upload_video(
    access_token: str,
    video_path: Path,
    metadata: dict,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """Upload a video to YouTube using resumable upload.

    Args:
        access_token: Valid OAuth2 access token with youtube.upload scope.
        video_path: Path to the video file on disk.
        metadata: {title, description, tags, privacy_status, category_id, made_for_kids,
            publish_at (optional RFC 3339 UTC time: upload private, YouTube publishes then)}
        on_progress: Optional callback(percent, message).

    Returns: {platform_media_id, platform_post_url, provider_status}
    """
    file_size = video_path.stat().st_size

    # Build video resource body
    body = {
        "snippet": {
            "title": metadata.get("title", "Untitled"),
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", []),
            "categoryId": str(metadata.get("category_id", "22")),  # 22 = People & Blogs
        },
        "status": {
            "privacyStatus": metadata.get("privacy_status", "private"),
            "selfDeclaredMadeForKids": metadata.get("made_for_kids", False),
        },
    }

    # Native YouTube scheduling: the video stays private until publishAt
    # (RFC 3339, UTC), when YouTube makes it public.
    publish_at = metadata.get("publish_at")
    if publish_at:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at

    # Step 1: Initiate resumable upload
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/*",
        "X-Upload-Content-Length": str(file_size),
    }

    init_resp = requests.post(
        YOUTUBE_UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers=headers,
        json=body,
        timeout=30,
    )

    if init_resp.status_code != 200:
        _raise_for_api_error(init_resp, "on init")
        raise YouTubeError(f"Upload init failed: {init_resp.status_code} — {init_resp.text}")

    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise YouTubeError("No upload URL in resumable init response")

    # Step 2: Upload file in chunks
    uploaded = 0
    with open(video_path, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(CHUNK_SIZE)
            chunk_end = uploaded + len(chunk) - 1

            chunk_headers = {
                "Content-Type": "video/*",
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {uploaded}-{chunk_end}/{file_size}",
            }

            try:
                resp = requests.put(upload_url, headers=chunk_headers, data=chunk, timeout=300)
            except (requests.ConnectionError, requests.Timeout) as e:
                raise RetryableYouTubeError(f"Network error during upload: {e}") from e

            if resp.status_code not in (200, 201, 308):
                _raise_for_api_error(resp, "mid-upload")

            uploaded += len(chunk)
            pct = int((uploaded / file_size) * 100)

            if resp.status_code in (200, 201):
                # Upload complete
                result = resp.json()
                video_id = result.get("id", "")
                logger.info("YouTube upload complete: video_id=%s", video_id)
                if on_progress:
                    on_progress(100, "Upload complete")
                return {
                    "platform_media_id": video_id,
                    "platform_post_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    "provider_status": "uploaded",
                }
            elif resp.status_code == 308:
                # Chunk accepted, continue
                if on_progress:
                    on_progress(pct, f"Uploading... {pct}%")
            else:
                raise YouTubeError(f"Unexpected upload response: {resp.status_code} — {resp.text}")

    raise YouTubeError("Upload loop ended without completion")


def revoke_token(token: str) -> bool:
    """Revoke an access or refresh token. Best-effort, returns success."""
    try:
        resp = requests.post(GOOGLE_REVOKE_URL, params={"token": token}, timeout=15)
        if resp.status_code == 200:
            logger.info("YouTube token revoked successfully")
            return True
        else:
            logger.warning("YouTube token revocation returned %s", resp.status_code)
            return False
    except Exception as e:
        logger.warning("YouTube token revocation failed: %s", e)
        return False
