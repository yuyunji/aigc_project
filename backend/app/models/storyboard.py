"""
分镜脚本模型 —— JSON 结构化，含运镜/台词/场景/时长
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Integer, Float, ForeignKey
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

    # 结构化字段
    scene_title: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="分镜标题"
    )
    location: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="场景地点"
    )
    time_of_day: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="时间（白天/夜晚/黄昏等）"
    )
    characters_in_scene: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="出场角色（逗号分隔）"
    )
    camera_movement: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="运镜方式"
    )
    dialogue: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="台词对白"
    )
    visual_description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="画面描述（用于 image prompt 生成）"
    )
    image_prompt: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="完整图片/视频生成 prompt"
    )
    duration_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="建议时长（秒）"
    )

    # ── 25 镜模板字段 ──
    shot_size: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="镜头景别（远景/全景/中景/近景/特写/大特写）"
    )
    camera_angle: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="拍摄角度（平视/俯拍/仰拍/侧拍/低角度仰拍）"
    )
    subject: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="画面主体人物"
    )
    environment: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="场景环境"
    )
    mood: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="情绪氛围"
    )
    composition: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="构图"
    )
    quality_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="画质补充"
    )
    transition: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="转场衔接（淡入/硬切/溶解/推入/拉出/闪白/黑场/叠化/匹配剪辑）"
    )
    dialogue_text: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="台词对白"
    )

    # 原始完整数据（JSON 备查）
    description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="原始 JSON 或完整分镜内容"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
