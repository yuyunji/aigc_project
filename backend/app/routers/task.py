"""
任务管理接口
POST /api/tasks            — 创建生成任务，入队异步处理
GET  /api/tasks            — 获取任务列表（按创建时间倒序）
GET  /api/tasks/stats      — 获取任务数量统计
GET  /api/tasks/{task_id}  — 查询单个任务状态与进度
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.schemas.task import (
    TaskCreateRequest,
    TaskResponse,
    TaskListResponse,
    TaskStatsResponse,
)
from app.services.task_queue import task_queue

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
