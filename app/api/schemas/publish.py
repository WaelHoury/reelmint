"""Schemas for social publishing API."""

import re
from typing import List, Mapping, Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field, field_validator

DEFAULT_RETURN_TO = "/account"

# Only plain in-app paths: a single leading slash followed by letters, digits,
# "/", "_" or "-". No scheme, host, "@", ".", "\\", query string or fragment.
_SAFE_RETURN_TO_RE = re.compile(r"^/[A-Za-z0-9/_\-]*$")


def safe_return_to(value: Optional[str]) -> str:
    """Return ``value`` if it is a safe same-origin path, else the default.

    Prevents open redirects in the OAuth callback, where the path is appended
    to the frontend origin (e.g. ``"@evil.com"`` would turn
    ``http://localhost:3000@evil.com`` into a redirect to evil.com).
    """
    if not isinstance(value, str):
        return DEFAULT_RETURN_TO
    if not _SAFE_RETURN_TO_RE.fullmatch(value) or value.startswith("//"):
        return DEFAULT_RETURN_TO
    return value


def build_frontend_redirect(frontend_origin: str, return_to: Optional[str], params: Mapping[str, str]) -> str:
    """Build the post-OAuth redirect URL on the frontend origin."""
    path = safe_return_to(return_to)
    query = urlencode(dict(params))
    if not query:
        return f"{frontend_origin}{path}"
    sep = "&" if "?" in path else "?"
    return f"{frontend_origin}{path}{sep}{query}"


class StartOAuthRequest(BaseModel):
    return_to: str = DEFAULT_RETURN_TO

    @field_validator("return_to", mode="before")
    @classmethod
    def _validate_return_to(cls, v):
        return safe_return_to(v)


class StartOAuthResponse(BaseModel):
    auth_url: str


class ConnectedAccountResponse(BaseModel):
    id: int
    provider: str
    display_name: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    status: str
    created_at: str


class ConnectedAccountListResponse(BaseModel):
    accounts: List[ConnectedAccountResponse]


class CreatePublishJobRequest(BaseModel):
    account_id: int
    mode: str = "upload"
    metadata: dict = Field(default_factory=dict)


class PublishJobResponse(BaseModel):
    id: int
    video_id: int
    account_id: int
    provider: str
    mode: str
    status: str
    platform_post_url: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: str
    published_at: Optional[str] = None


class PublishJobListResponse(BaseModel):
    jobs: List[PublishJobResponse]
