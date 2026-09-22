"""
媒体资源 Pydantic Schema
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


# ── 请求 ──

class GenerateVideoRequest(BaseModel):
    """图生视频请求"""
    task_id: str = Field(..., description="任务 ID")
    scene_numbers: list[int] | None = Field(None, description="指定分镜序号")


class GenerateTTSRequest(BaseModel):
    """TTS 配音请求"""
    task_id: str = Field(..., description="任务 ID")
    character_names: list[str] | None = Field(None, description="指定角色名")


class CompositeRequest(BaseModel):
    """视频合成请求"""
    task_id: str = Field(..., description="任务 ID")


class DirectorImageRequest(BaseModel):
    """分镜导演图（6 宫格）请求"""
    style: Literal["guoman3d", "riman2d", "zhenren"] = Field(
        "guoman3d", description="画面风格：guoman3d 国漫3D / riman2d 日漫2D / zhenren 真人写实"
    )


# ── 响应 ──

class MediaAssetResponse(BaseModel):
    id: str
    task_id: str
    asset_type: str
    storyboard_id: str | None = None
    scene_number: int | None = None
    character_name: str | None = None
    prompt: str | None = None
    file_path: str | None = None
    file_url: str | None = None
    url: str | None = None
    status: str
    error_message: str | None = None
    duration: float | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MediaAssetListResponse(BaseModel):
    """媒体资源列表"""
    total: int
    assets: list[MediaAssetResponse]


# ── 历史轮次归档 ──

class MediaArchiveItemResponse(BaseModel):
    id: str
    asset_type: str
    storyboard_id: str | None = None
    scene_number: int | None = None
    character_name: str | None = None
    prompt: str | None = None
    file_path: str | None = None
    url: str | None = None
    status: str
    error_message: str | None = None
    duration: float | None = None
    rescue_status: str = "none"
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class MediaArchiveRoundResponse(BaseModel):
    """一轮归档产物"""
    round_no: int
    archived_at: datetime | None = None
    item_count: int
    items: list[MediaArchiveItemResponse]


class MediaArchiveListResponse(BaseModel):
    """任务的历史轮次列表（新轮次在前）"""
    task_id: str
    total_rounds: int
    rounds: list[MediaArchiveRoundResponse]


class PipelineProgressResponse(BaseModel):
    """全流程进度"""
    task_id: str
    task_status: str
    stages: list[dict]  # [{stage, label, status, progress, assets_count}]
