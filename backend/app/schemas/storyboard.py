"""分镜脚本 Schema"""
from datetime import datetime
from pydantic import BaseModel, Field


class StoryboardUpdate(BaseModel):
    """结果页编辑保存：提交整段导演脚本原文，服务端按同一套解析逻辑重算全部字段"""

    raw_script: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="导演镜头脚本原文块（含「镜头NN：标题（时长：N秒）」标题行）",
    )


class StoryboardResponse(BaseModel):
    id: str
    task_id: str
    scene_number: int
    scene_title: str | None = None
    location: str | None = None
    time_of_day: str | None = None
    characters_in_scene: str | None = None
    camera_movement: str | None = None
    dialogue: str | None = None
    visual_description: str | None = None
    image_prompt: str | None = None
    duration_seconds: float | None = None
    description: str
    # 25 镜模板字段
    shot_size: str | None = None
    camera_angle: str | None = None
    subject: str | None = None
    environment: str | None = None
    mood: str | None = None
    composition: str | None = None
    quality_notes: str | None = None
    transition: str | None = None
    dialogue_text: str | None = None
    raw_script: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
