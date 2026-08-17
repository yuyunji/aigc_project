"""
资产拆解模型 —— 角色/场景/道具参考图
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AssetItem(Base):
    __tablename__ = "asset_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="character / scene / prop"
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="资产名称"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=True, comment="资产描述（外观/材质/用途）"
    )
    image_prompt: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="GPT-Image-2 生成用英文 prompt"
    )
    spatial_layout: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="场景空间布局（机位/标志物方位/光源，用于跨镜头场景一致性）"
    )
    portrait_prompt: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="角色正脸定妆图 prompt（干净背景半身定妆）"
    )
    portrait_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="角色正脸定妆图本地路径"
    )
    portrait_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="角色正脸定妆图远程 URL"
    )
    image_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="本地图片路径"
    )
    image_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="远程图片 URL"
    )
    image_oss_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="阿里云 OSS object key"
    )
    image_status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="pending / running / success / failed"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def url(self) -> str | None:
        """供前端展示的签名 URL（私有 Bucket）"""
        if not self.image_oss_key:
            return None
        from app.services.storage import storage
        return storage.get_signed_url(self.image_oss_key)
