"""
媒体归档模型 —— 「重新生成」时被替换掉的视频 / 配音 / 合成成片

与 MediaAsset 字段镜像，额外记录轮次（round_no）与云端补传状态（rescue_status）。
重新生成时活表行迁入本表，OSS 对象保持原 key 不动，页面「历史版本」可回看。
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MediaArchive(Base):
    """
    历史轮次媒体归档表。
    - round_no:      第几轮产物（1 起，由归档时 MAX+1 推导）
    - rescue_status: none=归档时已有 OSS 副本 / pending=待补传 / done=补传完成 / failed=补传失败（保留本地文件）
    """
    __tablename__ = "media_archives"
    __table_args__ = (
        Index("ix_media_archives_task_round", "task_id", "round_no"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    asset_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="video / audio / composite"
    )
    round_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="第几轮产物（1 起）"
    )

    storyboard_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, comment="关联分镜脚本 ID"
    )
    scene_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="分镜序号"
    )
    character_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="配音角色名"
    )

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True, comment="生成提示词")
    file_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="本地文件路径（OSS 有副本时可能已删除）"
    )
    file_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="远程 URL（临时）"
    )
    oss_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="阿里云 OSS object key"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="success", comment="归档时的生成状态"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(nullable=True, comment="时长（秒）")

    source_ids: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON: 组成视频的 asset ID 列表"
    )
    subtitle_srt: Mapped[str | None] = mapped_column(Text, nullable=True, comment="SRT 字幕内容")

    rescue_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="none",
        comment="none / pending / done / failed（云端补传状态）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=True, comment="原媒体资源的创建时间"
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, comment="归档时间"
    )

    @property
    def url(self) -> str | None:
        """供前端展示的签名 URL（私有 Bucket）"""
        if not self.oss_key:
            return None
        from app.services.storage import storage
        return storage.get_signed_url(self.oss_key)
