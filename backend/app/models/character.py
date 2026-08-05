"""
人物角色模型 —— 级联任务第2阶段产出
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="角色名")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="角色设定详情")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
