"""SQLAlchemy models for User and Video tables."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, BigInteger, DateTime, ForeignKey
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
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="videos")
