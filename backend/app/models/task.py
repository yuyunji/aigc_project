"""
任务模型 —— 对应一条完整的生成链路
状态流转: pending → running → success / failed
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务名称")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="pending/running/success/failed"
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=True, comment="原始文本内容（摘要）")
    source_type: Mapped[str] = mapped_column(
        String(10), default="text", comment="输入类型: text/file"
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="进度 0-100")
    error_message: Mapped[str] = mapped_column(Text, nullable=True, comment="失败时错误信息")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
