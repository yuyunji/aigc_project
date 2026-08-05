"""大纲 Schema"""
from datetime import datetime
from pydantic import BaseModel


class OutlineResponse(BaseModel):
    id: str
    task_id: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
