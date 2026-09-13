"""
任务管理接口
POST /api/tasks                — 创建生成任务，入队异步处理
GET  /api/tasks                — 获取任务列表（按创建时间倒序）
GET  /api/tasks/stats          — 获取任务数量统计
GET  /api/tasks/{task_id}      — 查询单个任务状态与进度
POST /api/tasks/{task_id}/regenerate — 重置任务并重新生成
"""
import asyncio
import os
import shutil
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.task import Task
from app.models.outline import Outline
from app.models.character import Character
from app.models.storyboard import Storyboard
from app.models.media import MediaAsset
from app.models.media_archive import MediaArchive
from app.models.asset import AssetItem
from app.schemas.task import (
    TaskCreateRequest,
    TaskResponse,
    TaskListResponse,
    TaskStatsResponse,
)
from app.services.task_queue import task_queue
from app.services.media_archive import archive_task_media, rescue_archive_uploads

router = APIRouter(prefix="/tasks", tags=["任务管理"])


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    payload: TaskCreateRequest,
    db: Session = Depends(get_db),
):
    """
    创建新的剧本生成任务。

    1. 在数据库中创建任务记录（status=pending）
    2. 放入内存队列，异步执行级联生成链路
    3. 立即返回任务信息，前端通过轮询 GET /tasks/{id} 获取进度
    """
    # ── 创建任务记录 ──
    task = Task(
        title=payload.title,
        source_text=payload.content,
        source_type=payload.source_type,
        status="pending",
        progress=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # ── 入队异步处理（不阻塞响应） ──
    await task_queue.enqueue(task_id=task.id, source_text=payload.content)

    return task


@router.get("", response_model=TaskListResponse)
def list_tasks(db: Session = Depends(get_db)):
    """
    获取全部任务列表，按创建时间倒序排列。
    前端用此接口渲染任务管理页面，配合定时轮询更新进度。
    """
    tasks = (
        db.query(Task)
        .order_by(Task.created_at.desc())
        .all()
    )
    return TaskListResponse(
        total=len(tasks),
        tasks=[TaskResponse.model_validate(t) for t in tasks],
    )


@router.get("/stats", response_model=TaskStatsResponse)
def task_stats(db: Session = Depends(get_db)):
    """
    返回各状态任务数量统计，供前端统计看板使用。
    """
    counts = (
        db.query(Task.status, func.count(Task.id))
        .group_by(Task.status)
        .all()
    )
    count_map = {status: cnt for status, cnt in counts}

    total = sum(count_map.values())
    return TaskStatsResponse(
        total=total,
        pending=count_map.get("pending", 0),
        running=count_map.get("running", 0),
        success=count_map.get("success", 0),
        failed=count_map.get("failed", 0),
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(get_db)):
    """
    查询单个任务详情，包括当前状态、进度百分比和错误信息。
    前端轮询此接口以实时更新任务进度。
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return TaskResponse.model_validate(task)


# 重新生成时保留的媒体子目录：资产参考图 / 角色定妆图（无需重跑图片生成）、历史轮次归档
_PRESERVED_MEDIA_DIRS = {"assets", "characters", "rounds"}


def _clear_task_media(task_id: str) -> None:
    """删除任务媒体产物（视频等），保留资产图与角色定妆图"""
    media_path = os.path.join(settings.media_dir, task_id)
    if not os.path.isdir(media_path):
        return
    for name in os.listdir(media_path):
        if name in _PRESERVED_MEDIA_DIRS:
            continue
        path = os.path.join(media_path, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


@router.post("/{task_id}/regenerate")
async def regenerate_task(task_id: str, db: Session = Depends(get_db)):
    """
    重置任务并重新生成（支持成功/失败/卡住的 running/pending 任务）。
    1. 把上一轮视频 / 配音 / 合成成片归档（保留 OSS 副本 + 可在「历史版本」回看）
    2. 删除已生成的数据（大纲、角色、分镜、其余媒体）
    3. 重置任务状态为 pending
    4. 重新入队（已有资产时链路会跳过资产拆解）
    资产拆解结果与已生成的资产图、定妆图保留；需要重做资产请用「AI 重新提取」。
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    source_text = task.source_text

    # ── 归档上一轮媒体（视频/配音/合成成片）──
    archive = await asyncio.to_thread(archive_task_media, task_id)

    # ── 清理关联数据（资产保留）──
    db.query(Outline).filter(Outline.task_id == task_id).delete()
    db.query(Character).filter(Character.task_id == task_id).delete()
    db.query(Storyboard).filter(Storyboard.task_id == task_id).delete()
    db.query(MediaAsset).filter(MediaAsset.task_id == task_id).delete()

    assets_preserved = (
        db.query(AssetItem).filter(AssetItem.task_id == task_id).count()
    )

    # ── 清理媒体文件（保留资产图 / 定妆图 / 归档目录）──
    _clear_task_media(task_id)

    # ── 重置任务状态 ──
    task.status = "pending"
    task.progress = 0
    task.error_message = None
    db.commit()

    # ── 没有 OSS 副本的归档文件后台补传（不阻塞本次请求）──
    if archive and archive["rescue"]:
        asyncio.create_task(
            rescue_archive_uploads(task_id, archive["round_no"], archive["rescue"])
        )

    # ── 重新入队 ──
    await task_queue.enqueue(task_id=task.id, source_text=source_text)

    return {
        "status": "regenerated",
        "task_id": task_id,
        "assets_preserved": assets_preserved,
        "archived_round": archive["round_no"] if archive else None,
        "archived_count": archive["archived_count"] if archive else 0,
    }


@router.delete("/{task_id}")
async def delete_task(task_id: str, db: Session = Depends(get_db)):
    """
    删除任务及其所有关联数据。

    限制：正在执行（running）的任务不可删除，避免删除后台仍在写入的数据。
    pending / success / failed 状态均可删除。
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    # ── 正在执行的任务禁止删除 ──
    if task.status == "running":
        raise HTTPException(
            status_code=409,
            detail="任务正在执行中，无法删除。请等待完成或先强制重置。",
        )

    # ── 清理关联数据（按外键从属顺序）──
    db.query(Outline).filter(Outline.task_id == task_id).delete()
    db.query(Character).filter(Character.task_id == task_id).delete()
    db.query(Storyboard).filter(Storyboard.task_id == task_id).delete()
    db.query(MediaArchive).filter(MediaArchive.task_id == task_id).delete()
    db.query(MediaAsset).filter(MediaAsset.task_id == task_id).delete()
    db.query(AssetItem).filter(AssetItem.task_id == task_id).delete()
    db.delete(task)
    db.commit()

    # ── 清理媒体文件 ──
    media_path = os.path.join(settings.media_dir, task_id)
    if os.path.isdir(media_path):
        shutil.rmtree(media_path, ignore_errors=True)

    return {"status": "deleted", "task_id": task_id}
