"""
任务相关 Pydantic Schema
"""
from datetime import datetime
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    title: str = Field(..., max_length=200, description="任务名称")
    content: str = Field(..., min_length=10, description="原著文本内容")
    source_type: str = Field(default="text", description="text / file")


class TaskResponse(BaseModel):
    """任务响应"""
    id: str
    title: str
    status: str
    source_type: str
    progress: int
    global_prefix: str | None = None
    post_constraint: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """任务列表"""
    total: int
    tasks: list[TaskResponse]


class TaskStatsResponse(BaseModel):
    """任务统计"""
    total: int
    pending: int
    running: int
    success: int
    failed: int
