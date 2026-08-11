"""资产拆解 Pydantic Schema"""
from datetime import datetime
from pydantic import BaseModel, Field


class AssetCreateRequest(BaseModel):
    """手动添加资产"""
    category: str = Field(..., description="character / scene / prop")
    name: str = Field(..., max_length=200)
    description: str = Field(default="", max_length=5000)


class AssetUpdateRequest(BaseModel):
    """编辑资产"""
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    category: str | None = None


class AssetResponse(BaseModel):
    id: str
    task_id: str
    category: str
    name: str
    description: str | None = None
    image_prompt: str | None = None
    image_path: str | None = None
    image_url: str | None = None
    image_status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AssetListResponse(BaseModel):
    task_id: str
    total: int
    assets: list[AssetResponse]


class AssetExtractResponse(BaseModel):
    """AI 提取结果"""
    extracted: int
    characters: list[str]
    scenes: list[str]
    props: list[str]
