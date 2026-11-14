"""Schemas for the batch queue / autopilot API."""

import re
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_BATCH_ITEMS = 100
MAX_PROMPT_CHARS = 500
MAX_SCRIPT_CHARS = 20000
MIN_INTERVAL_MINUTES = 15
MAX_SUGGESTED_TOPICS = 50


class BatchItemInput(BaseModel):
    input_type: Literal["prompt", "script"]
    text: str
    title: Optional[str] = None

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Text can't be empty")
        return v

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            return None
        if len(v) > 200:
            raise ValueError("Title must be at most 200 characters")
        if any(c in v for c in ("/", "\\", "\0")) or ".." in v:
            raise ValueError("Title must not contain '/', '\\' or '..'")
        return v

    @model_validator(mode="after")
    def _check_length(self):
        if self.input_type == "prompt" and len(self.text) > MAX_PROMPT_CHARS:
            raise ValueError(
                f"Topic is too long ({len(self.text)} characters, max {MAX_PROMPT_CHARS}). "
                "Use a script item for full narration."
            )
        if self.input_type == "script" and len(self.text) > MAX_SCRIPT_CHARS:
            raise ValueError(
                f"Script is too long ({len(self.text)} characters, max {MAX_SCRIPT_CHARS:,})"
            )
        return self


def _check_item_count(items: list, allow_empty: bool = False) -> list:
    if not items and not allow_empty:
        raise ValueError("Add at least one video")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"A batch can hold at most {MAX_BATCH_ITEMS} videos (got {len(items)})")
    return items


def _clean_niche(v: str) -> str:
    v = re.sub(r"\s+", " ", v or "").strip()
    if len(v) < 3:
        raise ValueError("Describe the niche in at least 3 characters")
    if len(v) > 200:
        raise ValueError("The niche must be at most 200 characters")
    return v


class BatchTheme(BaseModel):
    """Generate the batch's topics from a niche instead of listing them."""

    niche: str
    count: int = Field(default=10, ge=1, le=MAX_SUGGESTED_TOPICS)

    @field_validator("niche")
    @classmethod
    def _check_niche(cls, v: str) -> str:
        return _clean_niche(v)


class SuggestTopicsRequest(BaseModel):
    niche: str
    count: int = Field(default=10, ge=1, le=MAX_SUGGESTED_TOPICS)
    style: Optional[str] = Field(default=None, max_length=50)

    @field_validator("niche")
    @classmethod
    def _check_niche(cls, v: str) -> str:
        return _clean_niche(v)

    @field_validator("style")
    @classmethod
    def _check_style(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None


class SuggestTopicsResponse(BaseModel):
    topics: List[str]


class BatchSettings(BaseModel):
    orientation: Literal["portrait", "landscape", "square"] = "landscape"
    duration_seconds: int = Field(default=60, ge=15, le=600)
    narration_style: Optional[str] = Field(default=None, max_length=50)
    loop_mode: bool = False
    subtitles_enabled: bool = True
    subtitle_style: Optional[str] = Field(default=None, max_length=50)
    tts_voice_key: Optional[str] = Field(default=None, max_length=255)
    tts_provider: Optional[str] = Field(default=None, max_length=50)


class BatchSchedule(BaseModel):
    mode: Literal["immediate", "interval"] = "immediate"
    start_at: Optional[str] = None
    interval_minutes: Optional[int] = None

    @field_validator("start_at")
    @classmethod
    def _parse_start(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return None
        text = str(v).strip()
        try:
            dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
        except ValueError:
            raise ValueError("Start time must be an ISO 8601 date-time, e.g. 2026-11-15T09:00:00Z")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @model_validator(mode="after")
    def _check_interval(self):
        if self.mode == "interval":
            if not self.start_at:
                raise ValueError("Choose a start time for scheduled publishing")
            if self.interval_minutes is None or self.interval_minutes < MIN_INTERVAL_MINUTES:
                raise ValueError(
                    f"The publishing interval must be at least {MIN_INTERVAL_MINUTES} minutes"
                )
        return self


class BatchPublish(BaseModel):
    enabled: bool = False
    account_id: Optional[int] = None
    privacy_status: Literal["public", "unlisted", "private"] = "private"
    made_for_kids: bool = False
    category_id: Optional[str] = "22"
    extra_tags: List[str] = Field(default_factory=list)
    description_footer: Optional[str] = None
    schedule: Optional[BatchSchedule] = None

    @field_validator("category_id")
    @classmethod
    def _check_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return "22"
        v = str(v).strip()
        if not v.isdigit():
            raise ValueError("YouTube category must be a numeric id (e.g. 22)")
        return v

    @field_validator("extra_tags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        tags = [re.sub(r"\s+", " ", t).strip().lstrip("#") for t in v or []]
        tags = [t for t in tags if t]
        if len(tags) > 15:
            raise ValueError("Use at most 15 extra tags")
        if any(len(t) > 100 for t in tags):
            raise ValueError("Each tag must be at most 100 characters")
        return tags

    @field_validator("description_footer")
    @classmethod
    def _check_footer(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if len(v) > 2000:
            raise ValueError("Description footer must be at most 2000 characters")
        return v or None

    @model_validator(mode="after")
    def _require_account(self):
        if self.enabled and self.account_id is None:
            raise ValueError("Choose a YouTube account to publish to")
        return self


class CreateBatchRequest(BaseModel):
    name: str
    items: List[BatchItemInput] = Field(default_factory=list)
    settings: BatchSettings = Field(default_factory=BatchSettings)
    publish: BatchPublish = Field(default_factory=BatchPublish)
    start: bool = False
    # Retry failures and wait out YouTube quota limits without the user.
    hands_off: bool = False
    # Used only when ``items`` is empty: topics are generated from the niche.
    theme: Optional[BatchTheme] = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v or "").strip()
        if not v:
            raise ValueError("Give the batch a name")
        if len(v) > 200:
            raise ValueError("Batch name must be at most 200 characters")
        return v

    @field_validator("items")
    @classmethod
    def _check_items(cls, v: list) -> list:
        return _check_item_count(v, allow_empty=True)

    @model_validator(mode="after")
    def _require_items_or_theme(self):
        if not self.items and self.theme is None:
            raise ValueError("Add at least one video")
        return self


class AddBatchItemsRequest(BaseModel):
    items: List[BatchItemInput]

    @field_validator("items")
    @classmethod
    def _check_items(cls, v: list) -> list:
        return _check_item_count(v)


class BatchCounts(BaseModel):
    pending: int = 0
    in_progress: int = 0
    done: int = 0
    published: int = 0
    failed: int = 0
    cancelled: int = 0


class BatchSummary(BaseModel):
    id: int
    name: str
    status: str
    created_at: Optional[str] = None
    total: int
    counts: BatchCounts
    publish_enabled: bool
    hands_off: bool = False


class BatchListResponse(BaseModel):
    batches: List[BatchSummary]


class BatchItemResponse(BaseModel):
    id: int
    position: int
    input_type: str
    title: Optional[str] = None
    text_preview: str
    status: str
    progress_percent: Optional[int] = None
    current_step: Optional[str] = None
    video_id: Optional[int] = None
    youtube_url: Optional[str] = None
    scheduled_publish_at: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 0
    next_attempt_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class BatchResponse(BatchSummary):
    settings: dict
    publish: dict
    items: List[BatchItemResponse]


class YouTubeQuotaResponse(BaseModel):
    uploads_per_day_estimate: Optional[int] = None
    note: str
