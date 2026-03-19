"""SQLAlchemy models for User, Video, UserTTSVoice, SocialAccount, and PublishJob tables."""

from datetime import datetime
import sqlalchemy as sa
from sqlalchemy import Column, Integer, String, Text, BigInteger, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    google_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    picture_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    videos = relationship("Video", back_populates="user", cascade="all, delete-orphan")
    tts_voices = relationship("UserTTSVoice", back_populates="user", cascade="all, delete-orphan")
    social_accounts = relationship("SocialAccount", back_populates="user", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="user", cascade="all, delete-orphan")


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(50), default="completed")
    input_type = Column(String(20), nullable=False)
    prompt = Column(Text, nullable=True)
    script_text = Column(Text, nullable=True)
    narration_style = Column(String(50), nullable=True)
    duration_seconds = Column(Integer, nullable=False)
    orientation = Column(String(20), nullable=False)
    output_filename = Column(String(512), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=True)
    thumbnail_filename = Column(String(512), nullable=True)
    project_data_path = Column(String(512), nullable=True)
    r2_video_key = Column(String(512), nullable=True)
    r2_thumbnail_key = Column(String(512), nullable=True)
    r2_project_prefix = Column(String(512), nullable=True)
    render_version = Column(Integer, default=1, server_default="1", nullable=False)
    tts_provider = Column(String(50), nullable=True)
    tts_voice_key = Column(String(255), nullable=True)
    tts_voice_label = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="videos")
    publish_jobs = relationship("PublishJob", back_populates="video", cascade="all, delete-orphan")


class UserTTSVoice(Base):
    """Tracks user-created (cloned) TTS voices."""

    __tablename__ = "user_tts_voices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    fish_voice_id = Column(String(255), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    status = Column(String(50), default="processing")  # processing, ready, failed
    is_preset = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="tts_voices")


class SocialAccount(Base):
    """Connected social media accounts for video publishing."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "provider_user_id", name="uq_social_account"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    provider_user_id = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    username = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=True)
    scopes = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="active", server_default="active", nullable=False)
    provider_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=sa.func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=sa.func.now())

    user = relationship("User", back_populates="social_accounts")
    publish_jobs = relationship("PublishJob", back_populates="social_account", cascade="all, delete-orphan")


class PublishJob(Base):
    """Tracks video publish jobs to social media platforms."""

    __tablename__ = "publish_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False, index=True)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    provider = Column(String(50), nullable=False)
    mode = Column(String(50), nullable=False)
    request_payload = Column(JSON, nullable=True)
    status = Column(String(50), default="queued", server_default="queued", nullable=False)
    provider_status = Column(String(100), nullable=True)
    platform_media_id = Column(String(255), nullable=True)
    platform_post_url = Column(String(512), nullable=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, server_default="0", nullable=False)
    max_retries = Column(Integer, default=3, server_default="3", nullable=False)
    next_retry_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=sa.func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=sa.func.now())

    user = relationship("User", back_populates="publish_jobs")
    video = relationship("Video", back_populates="publish_jobs")
    social_account = relationship("SocialAccount", back_populates="publish_jobs")
