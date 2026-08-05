"""
剧本大纲模型 —— 级联任务第1阶段产出
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Outline(Base):
    __tablename__ = "outlines"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="大纲内容（Markdown）")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
