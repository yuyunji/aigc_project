"""
媒体资源接口
POST /api/media/{task_id}/generate    — 触发图片生成（Stage 5）
GET  /api/media/{task_id}/pipeline    — 全流程进度
GET  /api/media/{task_id}/images      — 分镜图片列表
GET  /api/media/{task_id}/videos      — 视频片段列表
GET  /api/media/{task_id}/audio       — 配音列表
GET  /api/media/{task_id}/composite   — 合成视频
"""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models.task import Task
from app.models.storyboard import Storyboard
from app.models.media import MediaAsset
from app.schemas.media import (
    MediaAssetResponse,
    MediaAssetListResponse,
    PipelineProgressResponse,
)
from app.services.task_manager import task_manager
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["媒体资源"])


@router.post("/{task_id}/scene/{scene_number}/image")
async def generate_scene_image(task_id: str, scene_number: int):
    """为单个分镜生成图片 (Wan-X-Turbo)"""
    scene = _get_scene_or_404(task_id, scene_number)
    asyncio.create_task(_run_scene_image(task_id, scene))
    return {"status": "started", "task_id": task_id, "scene_number": scene_number}


@router.post("/{task_id}/scene/{scene_number}/video")
async def generate_scene_video(task_id: str, scene_number: int):
    """为单个分镜生成视频 (MiniMax-H3)"""
    scene = _get_scene_or_404(task_id, scene_number)
    asyncio.create_task(_run_scene_video(task_id, scene))
    return {"status": "started", "task_id": task_id, "scene_number": scene_number}


@router.post("/{task_id}/scene/{scene_number}/retry")
async def retry_scene(task_id: str, scene_number: int):
    """删除失败/完成/卡死的媒体记录，允许重新执行"""
    db = SessionLocal()
    try:
        stale = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.task_id == task_id,
                MediaAsset.scene_number == scene_number,
            )
            .all()
        )
        count = len(stale)
        for a in stale:
            db.delete(a)
        db.commit()
        return {"status": "reset", "count": count}
    finally:
        db.close()


@router.post("/{task_id}/flowchart")
async def generate_flowchart(task_id: str):
    """使用 GPT-Image-2 生成导演流程图（所有分镜合成一张视觉规划图）"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        scenes = (
            db.query(Storyboard)
            .filter(Storyboard.task_id == task_id)
            .order_by(Storyboard.scene_number.asc())
            .all()
        )
        if not scenes:
            raise HTTPException(status_code=400, detail="该任务尚无分镜，请先生成分镜脚本")

        scene_list = [
            {
                "scene_number": s.scene_number,
                "scene_title": s.scene_title or "",
                "location": s.location or "",
                "time_of_day": s.time_of_day or "",
                "characters_in_scene": s.characters_in_scene or "",
                "camera_movement": s.camera_movement or "",
                "visual_description": s.visual_description or s.description or "",
            }
            for s in scenes
        ]

        asyncio.create_task(_run_flowchart(task_id, scene_list))
        return {"status": "started", "task_id": task_id, "scene_count": len(scene_list)}
    finally:
        db.close()


@router.get("/{task_id}/flowchart")
def get_flowchart(task_id: str, db: Session = Depends(get_db)):
    """获取导演流程图"""
    _get_task_or_404(task_id, db)
    asset = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.task_id == task_id,
            MediaAsset.asset_type == "flowchart",
        )
        .order_by(MediaAsset.created_at.desc())
        .first()
    )
    if not asset:
        return None
    return MediaAssetResponse.model_validate(asset)


async def _run_flowchart(task_id: str, scene_list: list[dict]):
    """后台执行导演流程图生成"""
    try:
        await task_manager.generate_storyboard_flowchart(task_id, scene_list)
    except Exception as e:
        logger.exception(f"[{task_id}] flowchart generation: {e}")


def _get_scene_or_404(task_id: str, scene_number: int) -> dict:
    """获取单个分镜数据，不存在则 404"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        s = (
            db.query(Storyboard)
            .filter(Storyboard.task_id == task_id, Storyboard.scene_number == scene_number)
            .first()
        )
        if not s:
            raise HTTPException(status_code=404, detail=f"分镜 {scene_number} 不存在")
        return {
            "scene_number": s.scene_number,
            "scene_title": s.scene_title or "",
            "location": s.location or "",
            "time_of_day": s.time_of_day or "",
            "characters_in_scene": s.characters_in_scene or "",
            "camera_movement": s.camera_movement or "",
            "dialogue": s.dialogue or "",
            "visual_description": s.visual_description or s.description or "",
            "image_prompt": s.image_prompt or "",
            "duration_seconds": s.duration_seconds or 5.0,
            "description": s.description or "",
        }
    finally:
        db.close()


async def _run_scene_image(task_id: str, scene: dict):
    """后台执行单个分镜图片生成"""
    try:
        await task_manager.generate_scene_image(task_id, scene)
    except Exception as e:
        logger.exception(f"[{task_id}] scene {scene['scene_number']} image: {e}")


async def _run_scene_video(task_id: str, scene: dict):
    """后台执行单个分镜视频生成"""
    try:
        await task_manager.generate_scene_video(task_id, scene)
    except Exception as e:
        logger.exception(f"[{task_id}] scene {scene['scene_number']} video: {e}")


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
    flow_total, flow_ok = count_by_type("flowchart")

    # Provider 标签
    img_label = "GPT-Image-2" if settings.image_provider == "gpt-image-2" else "MiniMax image-01"
    vid_label = "MiniMax-H3"

    stages = [
        {"stage": 1, "label": "文本预处理", "status": "success" if task.progress >= 20 else ("running" if task.progress >= 10 else "pending"), "progress": min(task.progress, 20), "assets_count": 0},
        {"stage": 2, "label": "剧本大纲", "status": "success" if task.progress >= 45 else ("running" if task.progress >= 25 else "pending"), "progress": min(max(task.progress - 20, 0), 25), "assets_count": 0},
        {"stage": 3, "label": "人物角色", "status": "success" if task.progress >= 70 else ("running" if task.progress >= 50 else "pending"), "progress": min(max(task.progress - 45, 0), 25), "assets_count": 0},
        {"stage": 4, "label": "分镜脚本", "status": "success" if task.progress >= 78 else ("running" if task.progress >= 70 else "pending"), "progress": min(max(task.progress - 70, 0), 8), "assets_count": 0},
        {"stage": 5, "label": f"{img_label} 图片", "status": "success" if img_total > 0 and img_ok == img_total else ("running" if img_total > 0 else "pending"), "progress": 0, "assets_count": img_ok},
        {"stage": 6, "label": f"{vid_label} 视频", "status": "success" if vid_total > 0 and vid_ok == vid_total else ("running" if vid_total > 0 else "pending"), "progress": 0, "assets_count": vid_ok},
        {"stage": 7, "label": "视频拼接", "status": "success" if comp_ok > 0 else ("running" if comp_total > 0 else "pending"), "progress": 0, "assets_count": comp_ok},
    ]

    if settings.enable_storyboard_flowchart:
        stages.append(
            {"stage": 8, "label": "导演流程图", "status": "success" if flow_ok > 0 else ("running" if flow_total > 0 else "pending"), "progress": 0, "assets_count": flow_ok},
        )

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


@router.post("/{task_id}/images/generate-all")
async def generate_all_images(task_id: str):
    """一键为所有分镜生成图片"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        scenes = (
            db.query(Storyboard)
            .filter(Storyboard.task_id == task_id)
            .order_by(Storyboard.scene_number.asc())
            .all()
        )
        if not scenes:
            raise HTTPException(status_code=400, detail="该任务尚无分镜")

        scene_list = [{
            "scene_number": s.scene_number,
            "scene_title": s.scene_title or "",
            "location": s.location or "",
            "time_of_day": s.time_of_day or "",
            "characters_in_scene": s.characters_in_scene or "",
            "camera_movement": s.camera_movement or "",
            "dialogue": s.dialogue or "",
            "visual_description": s.visual_description or s.description or "",
            "image_prompt": s.image_prompt or "",
            "description": s.description or "",
        } for s in scenes]

        for scene in scene_list:
            asyncio.create_task(_run_scene_image(task_id, scene))

        return {"status": "started", "task_id": task_id, "count": len(scene_list)}
    finally:
        db.close()


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
