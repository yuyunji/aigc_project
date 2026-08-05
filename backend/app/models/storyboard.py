"""
分镜脚本模型 —— 级联任务第3阶段产出
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Storyboard(Base):
    __tablename__ = "storyboards"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, comment="关联任务"
    )
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False, comment="分镜序号")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="分镜描述")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
