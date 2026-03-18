"""SQLAlchemy models for User, Video, and UserTTSVoice tables."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, BigInteger, Boolean, DateTime, ForeignKey
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
