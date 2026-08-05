"""分镜脚本 Schema"""
from datetime import datetime
from pydantic import BaseModel


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
    created_at: datetime

    class Config:
        from_attributes = True
