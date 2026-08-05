"""
结果查询接口
GET /api/results/{task_id}/outline      — 获取大纲
GET /api/results/{task_id}/characters   — 获取人物设定列表
GET /api/results/{task_id}/storyboards  — 获取分镜脚本列表（按分镜序号排序）
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.models.outline import Outline
from app.models.character import Character
from app.models.storyboard import Storyboard
from app.schemas.outline import OutlineResponse
from app.schemas.character import CharacterResponse
from app.schemas.storyboard import StoryboardResponse

router = APIRouter(prefix="/results", tags=["结果查询"])


# ── 辅助：校验任务存在 ──
def _get_task_or_404(task_id: str, db: Session) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("/{task_id}/outline", response_model=OutlineResponse | None)
def get_outline(task_id: str, db: Session = Depends(get_db)):
    """
    获取指定任务生成的大纲（一个任务只有一篇大纲）。
    如果任务尚未完成大纲阶段，返回 null。
    """
    _get_task_or_404(task_id, db)

    outline = (
        db.query(Outline)
        .filter(Outline.task_id == task_id)
        .order_by(Outline.created_at.desc())
        .first()
    )
    if not outline:
        return None
    return OutlineResponse.model_validate(outline)


@router.get("/{task_id}/characters", response_model=list[CharacterResponse])
def get_characters(task_id: str, db: Session = Depends(get_db)):
    """
    获取指定任务的人物角色列表。
    按创建顺序返回。
    """
    _get_task_or_404(task_id, db)

    characters = (
        db.query(Character)
        .filter(Character.task_id == task_id)
        .order_by(Character.created_at.asc())
        .all()
    )
    return [CharacterResponse.model_validate(c) for c in characters]


@router.get("/{task_id}/storyboards", response_model=list[StoryboardResponse])
def get_storyboards(task_id: str, db: Session = Depends(get_db)):
    """
    获取指定任务的分镜脚本列表。
    按分镜序号升序排列。
    """
    _get_task_or_404(task_id, db)

    storyboards = (
        db.query(Storyboard)
        .filter(Storyboard.task_id == task_id)
        .order_by(Storyboard.scene_number.asc())
        .all()
    )
    return [StoryboardResponse.model_validate(s) for s in storyboards]
