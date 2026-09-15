"""
媒体资源接口
GET  /api/media/{task_id}/pipeline    — 全流程进度
GET  /api/media/{task_id}/videos      — 视频片段列表
GET  /api/media/{task_id}/audio       — 配音列表
GET  /api/media/{task_id}/composite   — 合成视频
GET  /api/media/{task_id}/archive     — 历史轮次归档（重新生成保留的上一轮产物）
"""
import asyncio
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models.task import Task
from app.models.storyboard import Storyboard
from app.models.media import MediaAsset
from app.models.media_archive import MediaArchive
from app.schemas.media import (
    MediaAssetResponse,
    MediaAssetListResponse,
    MediaArchiveItemResponse,
    MediaArchiveRoundResponse,
    MediaArchiveListResponse,
    PipelineProgressResponse,
)
from app.services.task_manager import task_manager
from app.services.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["媒体资源"])


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
            "dialogue_text": s.dialogue_text or "",
            "subject": s.subject or "",
            "environment": s.environment or "",
            "visual_description": s.visual_description or s.description or "",
            "image_prompt": s.image_prompt or "",
            "duration_seconds": s.duration_seconds or 5.0,
            "description": s.description or "",
            # 单镜重生成视频走的是与链路不同的第二条 prompt 组装路径，
            # 这两个字段缺一不可：raw_script 是 @ 引用的解析源，
            # character_core_prompt 是前置的角色服装锁
            "raw_script": s.raw_script or "",
            "character_core_prompt": s.character_core_prompt or "",
        }
    finally:
        db.close()


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


@router.post("/{task_id}/composite")
async def trigger_composite(task_id: str):
    """拼接已生成的视频片段，带转场效果"""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        videos = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.task_id == task_id,
                MediaAsset.asset_type == "video",
                MediaAsset.status == "success",
            )
            .order_by(MediaAsset.scene_number.asc())
            .all()
        )
        if len(videos) < 2:
            raise HTTPException(status_code=400, detail="至少需要 2 个成功的视频片段才能拼接")

        asyncio.create_task(_run_composite(task_id))
        return {"status": "started", "video_count": len(videos)}
    finally:
        db.close()


async def _run_composite(task_id: str):
    """后台执行视频拼接"""
    from app.services.task_manager import task_manager
    from app.models.storyboard import Storyboard
    db = SessionLocal()
    try:
        videos = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.task_id == task_id,
                MediaAsset.asset_type == "video",
                MediaAsset.status == "success",
            )
            .order_by(MediaAsset.scene_number.asc())
            .all()
        )
        video_paths = [v.file_path for v in videos if v.file_path and os.path.isfile(v.file_path)]

        # 读取转场信息（按 scene_number 建立映射，供精确对齐）
        storyboards = (
            db.query(Storyboard)
            .filter(Storyboard.task_id == task_id)
            .order_by(Storyboard.scene_number.asc())
            .all()
        )
        transition_by_scene = {s.scene_number: (s.transition or "硬切") for s in storyboards}

        if len(video_paths) >= 2:
            from app.services.video_composer import video_composer
            # 用每个视频的 scene_number 精确对齐转场，而非依赖数组下标
            transitions = [
                transition_by_scene.get(v.scene_number, "硬切")
                for v in videos
                if v.file_path and os.path.isfile(v.file_path)
            ]
            output = await video_composer.composite_with_transitions(
                task_id, video_paths, transitions
            )
            # 上传合成视频到 OSS（失败降级为本地 /media）；key 带轮次前缀，不覆盖归档轮
            oss_key = None
            try:
                from app.services.storage import storage
                from app.services.media_archive import current_round, round_oss_key
                key = round_oss_key(output, task_id, current_round(task_id))
                oss_key = await asyncio.to_thread(storage.upload, output, key)
            except Exception as e:
                logger.warning(f"[{task_id}] OSS 上传失败（忽略）: {e}")
            # 保存合成记录
            asset = MediaAsset(
                task_id=task_id,
                asset_type="composite",
                status="success",
                file_path=output,
                oss_key=oss_key,
                prompt="video composite with transitions",
            )
            db.add(asset)
            db.commit()
            event_bus.publish(task_id, "media", {
                "asset_id": asset.id,
                "task_id": task_id,
                "asset_type": "composite",
                "scene_number": None,
                "status": "success",
                "error_message": None,
                "file_path": output,
                "url": storage.get_signed_url(oss_key) if oss_key else None,
            })
            logger.info(f"[{task_id}] 视频拼接完成: {output}")
    except Exception as e:
        logger.exception(f"[{task_id}] 视频拼接失败: {e}")
        asset = MediaAsset(
            task_id=task_id,
            asset_type="composite",
            status="failed",
            error_message=str(e)[:500],
        )
        db.add(asset)
        db.commit()
        event_bus.publish(task_id, "media", {
            "asset_id": asset.id,
            "task_id": task_id,
            "asset_type": "composite",
            "scene_number": None,
            "status": "failed",
            "error_message": str(e)[:500],
            "file_path": None,
            "url": None,
        })
    finally:
        db.close()


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

    vid_total, vid_ok = count_by_type("video")
    comp_total, comp_ok = count_by_type("composite")
    vid_label = "MiniMax-H3"

    stages = [
        {"stage": 1, "label": "文本预处理", "status": "success" if task.progress >= 20 else ("running" if task.progress >= 10 else "pending"), "progress": min(task.progress, 20), "assets_count": 0},
        {"stage": 2, "label": "导演镜头拆解", "status": "success" if task.progress >= 78 else ("running" if task.progress >= 25 else "pending"), "progress": min(max(task.progress - 20, 0), 58), "assets_count": 0},
        {"stage": 3, "label": f"{vid_label} 视频", "status": "success" if vid_total > 0 and vid_ok == vid_total else ("running" if vid_total > 0 else "pending"), "progress": 0, "assets_count": vid_ok},
        {"stage": 4, "label": "视频拼接", "status": "success" if comp_ok > 0 else ("running" if comp_total > 0 else "pending"), "progress": 0, "assets_count": comp_ok},
    ]

    return PipelineProgressResponse(
        task_id=task_id,
        task_status=task.status,
        stages=stages,
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


@router.get("/{task_id}/archive", response_model=MediaArchiveListResponse)
def get_archive(task_id: str, db: Session = Depends(get_db)):
    """
    历史轮次归档 —— 「重新生成」时被替换掉的视频 / 配音 / 合成成片。

    按轮次分组（新轮次在前），本地无副本的条目由后台补传 OSS
    （rescue_status: pending / done / failed）。
    """
    _get_task_or_404(task_id, db)
    rows = (
        db.query(MediaArchive)
        .filter(MediaArchive.task_id == task_id)
        .order_by(
            MediaArchive.round_no.desc(),
            MediaArchive.asset_type.asc(),
            MediaArchive.scene_number.asc(),
        )
        .all()
    )

    rounds: list[MediaArchiveRoundResponse] = []
    for row in rows:
        if not rounds or rounds[-1].round_no != row.round_no:
            rounds.append(
                MediaArchiveRoundResponse(
                    round_no=row.round_no,
                    archived_at=row.archived_at,
                    item_count=0,
                    items=[],
                )
            )
        rounds[-1].items.append(MediaArchiveItemResponse.model_validate(row))
        rounds[-1].item_count += 1

    return MediaArchiveListResponse(
        task_id=task_id, total_rounds=len(rounds), rounds=rounds
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
