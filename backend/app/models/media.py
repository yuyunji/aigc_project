"""
媒体资源模型 —— 分镜图片 / 视频片段 / 配音音频 / 合成视频
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MediaAsset(Base):
    """
    统一媒体资源表，通过 asset_type 区分类型：
    - image:      分镜图片（Wan-X-Turbo 生成）
    - video:      图生视频片段（Seedance 生成）
    - audio:      角色配音（Volcengine TTS）
    - composite:  最终合成视频（FFmpeg 拼接）
    """
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    asset_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="image / video / audio / composite"
    )

    # 关联分镜（image/video 类型使用）
    storyboard_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, comment="关联分镜脚本 ID"
    )
    scene_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="分镜序号"
    )

    # 关联角色（audio 类型使用）
    character_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="配音角色名"
    )

    # 核心字段
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True, comment="生成提示词")
    file_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="本地文件路径"
    )
    file_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="远程 URL（临时）"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="pending / running / success / failed"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(
        nullable=True, comment="时长（秒）"
    )

    # 合成视频专用字段
    source_ids: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON: 组成视频的 asset ID 列表"
    )
    subtitle_srt: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="SRT 字幕内容"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
