"""
媒体资源接口
GET  /api/media/{task_id}/pipeline  — 全流程进度
GET  /api/media/{task_id}/images    — 分镜图片列表
GET  /api/media/{task_id}/videos    — 视频片段列表
GET  /api/media/{task_id}/audio     — 配音列表
GET  /api/media/{task_id}/composite — 合成视频
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.models.media import MediaAsset
from app.schemas.media import (
    MediaAssetResponse,
    MediaAssetListResponse,
    PipelineProgressResponse,
)

router = APIRouter(prefix="/media", tags=["媒体资源"])


def _get_task_or_404(task_id: str, db: Session) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("/{task_id}/pipeline", response_model=PipelineProgressResponse)
def get_pipeline_progress(task_id: str, db: Session = Depends(get_db)):
    """
    获取全流程进度（8 个阶段的状态 + 媒体资源数量）。
    """
    task = _get_task_or_404(task_id, db)

    assets = (
        db.query(MediaAsset)
        .filter(MediaAsset.task_id == task_id)
        .all()
    )

    # 按类型分组统计
    def count_by_type(asset_type: str) -> tuple[int, int]:
        matching = [a for a in assets if a.asset_type == asset_type]
        success = sum(1 for a in matching if a.status == "success")
        return len(matching), success

    img_total, img_ok = count_by_type("image")
    vid_total, vid_ok = count_by_type("video")
    aud_total, aud_ok = count_by_type("audio")
    comp_total, comp_ok = count_by_type("composite")

    stages = [
        {"stage": 1, "label": "文本预处理", "status": "success" if task.progress >= 20 else ("running" if task.progress >= 10 else "pending"), "progress": min(task.progress, 20), "assets_count": 0},
        {"stage": 2, "label": "剧本大纲", "status": "success" if task.progress >= 45 else ("running" if task.progress >= 25 else "pending"), "progress": min(max(task.progress - 20, 0), 25), "assets_count": 0},
        {"stage": 3, "label": "人物角色", "status": "success" if task.progress >= 70 else ("running" if task.progress >= 50 else "pending"), "progress": min(max(task.progress - 45, 0), 25), "assets_count": 0},
        {"stage": 4, "label": "分镜脚本", "status": "success" if task.progress >= 78 else ("running" if task.progress >= 70 else "pending"), "progress": min(max(task.progress - 70, 0), 8), "assets_count": 0},
        {"stage": 5, "label": "分镜图片", "status": "success" if img_total > 0 and img_ok == img_total else ("running" if img_total > 0 else "pending"), "progress": 0, "assets_count": img_ok},
        {"stage": 6, "label": "图生视频", "status": "success" if vid_total > 0 and vid_ok == vid_total else ("running" if vid_total > 0 else "pending"), "progress": 0, "assets_count": vid_ok},
        {"stage": 7, "label": "角色配音", "status": "success" if aud_total > 0 and aud_ok == aud_total else ("running" if aud_total > 0 else "pending"), "progress": 0, "assets_count": aud_ok},
        {"stage": 8, "label": "字幕合成", "status": "success" if comp_ok > 0 else ("running" if comp_total > 0 else "pending"), "progress": 0, "assets_count": comp_ok},
    ]

    return PipelineProgressResponse(
        task_id=task_id,
        task_status=task.status,
        stages=stages,
    )


@router.get("/{task_id}/images", response_model=MediaAssetListResponse)
def get_images(task_id: str, db: Session = Depends(get_db)):
    _get_task_or_404(task_id, db)
    assets = (
        db.query(MediaAsset)
        .filter(MediaAsset.task_id == task_id, MediaAsset.asset_type == "image")
        .order_by(MediaAsset.scene_number.asc())
        .all()
    )
    return MediaAssetListResponse(
        total=len(assets),
        assets=[MediaAssetResponse.model_validate(a) for a in assets],
    )


@router.get("/{task_id}/videos", response_model=MediaAssetListResponse)
def get_videos(task_id: str, db: Session = Depends(get_db)):
    _get_task_or_404(task_id, db)
    assets = (
        db.query(MediaAsset)
        .filter(MediaAsset.task_id == task_id, MediaAsset.asset_type == "video")
        .order_by(MediaAsset.scene_number.asc())
        .all()
    )
    return MediaAssetListResponse(
        total=len(assets),
        assets=[MediaAssetResponse.model_validate(a) for a in assets],
    )


@router.get("/{task_id}/audio", response_model=MediaAssetListResponse)
def get_audio(task_id: str, db: Session = Depends(get_db)):
    _get_task_or_404(task_id, db)
    assets = (
        db.query(MediaAsset)
        .filter(MediaAsset.task_id == task_id, MediaAsset.asset_type == "audio")
        .order_by(MediaAsset.created_at.asc())
        .all()
    )
    return MediaAssetListResponse(
        total=len(assets),
        assets=[MediaAssetResponse.model_validate(a) for a in assets],
    )


@router.get("/{task_id}/composite", response_model=MediaAssetResponse | None)
def get_composite(task_id: str, db: Session = Depends(get_db)):
    _get_task_or_404(task_id, db)
    asset = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.task_id == task_id,
            MediaAsset.asset_type == "composite",
        )
        .order_by(MediaAsset.created_at.desc())
        .first()
    )
    if not asset:
        return None
    return MediaAssetResponse.model_validate(asset)
