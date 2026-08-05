"""人物角色 Schema"""
from datetime import datetime
from pydantic import BaseModel


class CharacterResponse(BaseModel):
    id: str
    task_id: str
    name: str
    description: str
    created_at: datetime

    class Config:
        from_attributes = True
