"""Schemas for social publishing API."""

from typing import List, Optional
from pydantic import BaseModel, Field


class StartOAuthRequest(BaseModel):
    return_to: str = "/account"


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
