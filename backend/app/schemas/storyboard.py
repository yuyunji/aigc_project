"""分镜脚本 Schema"""
from datetime import datetime
from pydantic import BaseModel


class StoryboardResponse(BaseModel):
    id: str
    task_id: str
    scene_number: int
    description: str
    created_at: datetime

    class Config:
        from_attributes = True
