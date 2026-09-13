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
    spatial_layout: str | None = None
    portrait_prompt: str | None = None
    portrait_url: str | None = None
    image_path: str | None = None
    image_url: str | None = None
    url: str | None = None
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
    extracted: int = Field(description="本次提取命中的资产数（新增 + 更新）")
    characters: list[str]
    scenes: list[str]
    props: list[str]
    added: int = Field(default=0, description="新增资产数")
    updated: int = Field(default=0, description="匹配到已有资产并覆盖其文本的资产数（图片保留）")
    kept: int = Field(default=0, description="新结果未覆盖、但因已出图而保留的资产数")
    removed: int = Field(default=0, description="新结果未覆盖且未出图而被删除的资产数")
    wardrobe_warnings: list[str] = Field(
        default_factory=list,
        description="缺规范「服装」字段的角色清单，用于提示用户补全以保证跨镜头服装一致",
    )
