"""
资产拆解接口
POST   /api/tasks/{task_id}/assets/extract       — AI 自动提取
GET    /api/tasks/{task_id}/assets               — 获取资产列表
POST   /api/tasks/{task_id}/assets               — 手动添加
PUT    /api/tasks/{task_id}/assets/{asset_id}     — 编辑
DELETE /api/tasks/{task_id}/assets/{asset_id}     — 删除
POST   /api/tasks/{task_id}/assets/{asset_id}/generate-image  — 生成参考图
POST   /api/tasks/{task_id}/assets/{asset_id}/upload-image     — 上传图片
"""
import asyncio
import os
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.task import Task
from app.models.storyboard import Storyboard
from app.models.asset import AssetItem
from app.services.events import event_bus
from app.schemas.asset import (
    AssetCreateRequest, AssetUpdateRequest, AssetResponse,
    AssetListResponse, AssetExtractResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["资产拆解"])


def _get_task_or_404(task_id: str, db: Session) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


# ── AI 自动提取 ──

@router.post("/{task_id}/assets/extract", response_model=AssetExtractResponse)
async def extract_assets(task_id: str, db: Session = Depends(get_db)):
    """AI 自动从分镜脚本中提取角色/场景/道具"""
    _get_task_or_404(task_id, db)

    # 获取分镜脚本
    storyboards = (
        db.query(Storyboard)
        .filter(Storyboard.task_id == task_id)
        .order_by(Storyboard.scene_number.asc())
        .all()
    )
    if not storyboards:
        raise HTTPException(status_code=400, detail="该任务尚未生成分镜脚本，请先完成分镜生成")

    # 拼接分镜文本（精简关键字段，减少 token 消耗加速响应）
    sb_text = "\n".join([
        f"镜头{s.scene_number}: 主体={s.subject or ''}, 环境={s.environment or ''}"
        for s in storyboards
    ])[:8000]

    # 调用 LLM 提取（独立超时 600s）
    from app.services.llm_service import llm_service
    try:
        result = await asyncio.wait_for(
            llm_service.generate_asset_breakdown(sb_text),
            timeout=1800,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI 提取超时，请重试")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 提取失败: {str(e)[:200]}")

    # 清除旧资产
    db.query(AssetItem).filter(AssetItem.task_id == task_id).delete()

    # 存入数据库
    extracted = 0
    chars, scenes, props = [], [], []

    for cat_key, cat_label in [("characters", "character"), ("scenes", "scene"), ("props", "prop")]:
        items = result.get(cat_key, [])
        for item in items:
            asset = AssetItem(
                task_id=task_id,
                category=cat_label,
                name=item.get("name", "")[:200],
                description=item.get("description", "")[:5000],
                image_prompt=item.get("visual_prompt", "")[:2000],
                spatial_layout=item.get("spatial_layout", "")[:2000] or None,
                portrait_prompt=item.get("portrait_prompt", "")[:1000] or None,
                image_status="pending",
            )
            db.add(asset)
            extracted += 1
            if cat_label == "character":
                chars.append(item.get("name", ""))
            elif cat_label == "scene":
                scenes.append(item.get("name", ""))
            else:
                props.append(item.get("name", ""))

    db.commit()
    logger.info(f"[{task_id}] 资产提取完成: {extracted} 个资产")

    # 服装字段体检：找出缺规范「服装」字段的角色，提示补全
    wardrobe_warnings = []
    try:
        from app.services.consistency import check_wardrobe_completeness
        wardrobe_warnings = check_wardrobe_completeness(task_id)
        if wardrobe_warnings:
            logger.warning(
                f"[{task_id}] 以下角色缺规范服装字段，跨镜头服装可能不一致: "
                f"{wardrobe_warnings}"
            )
    except Exception as e:
        logger.warning(f"[{task_id}] 服装字段体检失败（非致命）: {e}")

    return AssetExtractResponse(
        extracted=extracted,
        characters=chars,
        scenes=scenes,
        props=props,
        wardrobe_warnings=wardrobe_warnings,
    )


# ── CRUD ──

@router.get("/{task_id}/assets", response_model=AssetListResponse)
def list_assets(
    task_id: str,
    category: str = Query(default=None, description="筛选: character/scene/prop"),
    db: Session = Depends(get_db),
):
    """获取资产列表，可按分类筛选"""
    _get_task_or_404(task_id, db)
    q = db.query(AssetItem).filter(AssetItem.task_id == task_id)
    if category:
        q = q.filter(AssetItem.category == category)
    assets = q.order_by(AssetItem.category.asc(), AssetItem.name.asc()).all()
    return AssetListResponse(
        task_id=task_id,
        total=len(assets),
        assets=[AssetResponse.model_validate(a) for a in assets],
    )


@router.post("/{task_id}/assets", response_model=AssetResponse, status_code=201)
def create_asset(
    task_id: str,
    payload: AssetCreateRequest,
    db: Session = Depends(get_db),
):
    """手动添加资产"""
    _get_task_or_404(task_id, db)
    asset = AssetItem(
        task_id=task_id,
        category=payload.category,
        name=payload.name,
        description=payload.description,
        image_status="pending",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return AssetResponse.model_validate(asset)


@router.put("/{task_id}/assets/{asset_id}", response_model=AssetResponse)
def update_asset(
    task_id: str,
    asset_id: str,
    payload: AssetUpdateRequest,
    db: Session = Depends(get_db),
):
    """编辑资产"""
    _get_task_or_404(task_id, db)
    asset = db.query(AssetItem).filter(
        AssetItem.id == asset_id, AssetItem.task_id == task_id
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    if payload.name is not None:
        asset.name = payload.name
    if payload.description is not None:
        asset.description = payload.description
    if payload.category is not None:
        asset.category = payload.category
    db.commit()
    db.refresh(asset)
    return AssetResponse.model_validate(asset)


@router.delete("/{task_id}/assets/{asset_id}", status_code=204)
def delete_asset(task_id: str, asset_id: str, db: Session = Depends(get_db)):
    """删除资产（含本地图片文件）"""
    _get_task_or_404(task_id, db)
    asset = db.query(AssetItem).filter(
        AssetItem.id == asset_id, AssetItem.task_id == task_id
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    # 删除本地图片
    if asset.image_path and os.path.isfile(asset.image_path):
        try:
            os.remove(asset.image_path)
        except OSError:
            pass
    db.delete(asset)
    db.commit()


# ── 图片生成 ──

# 角色三视图设定表模板（对应 character-three-view skill 第二步固定格式）
# character 类资产参考图：左侧面部特写 + 右侧全身三视图（正/侧/背）+ 差异化服饰
CHARACTER_TURNAROUND_CONSTRAINT = (
    "Professional character design sheet, horizontal 16:9 layout, "
    "clean white minimalist background, no extra elements. "
    "Left panel: high-detail close-up portrait of the face, exact age and clear gender, "
    "natural black hair unless specified, detailed hairstyle, "
    "dark brown or black eyes with both eyes identical, cel-shaded anime skin with soft shading, non-realistic. "
    "Right panel: full-body standard turnaround in three views (front view, side view, back view), "
    "natural upright standing pose, showing full-body proportions. "
    "Outfit: complete and undamaged clothing, distinct style and color scheme, "
    "no redundant jewelry, no cross-gender accessories. "
    "Negative: no realistic, no photo, no photograph, no 3D render, no 3D model"
)

@router.post("/{task_id}/assets/{asset_id}/generate-image")
async def generate_asset_image(
    task_id: str, asset_id: str, db: Session = Depends(get_db)
):
    """为单个资产生成参考图（GPT-Image-2）"""
    _get_task_or_404(task_id, db)
    asset = db.query(AssetItem).filter(
        AssetItem.id == asset_id, AssetItem.task_id == task_id
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    if settings.image_provider != "gpt-image-2":
        raise HTTPException(status_code=400, detail="资产参考图需要 GPT-Image-2，请在 .env 中设置 IMAGE_PROVIDER=gpt-image-2")

    # 标记运行中
    asset.image_status = "running"
    asset.error_message = None
    db.commit()

    # 后台生成
    asyncio.create_task(_run_asset_image_gen(task_id, asset_id))
    return {"status": "started", "asset_id": asset_id}


async def _run_asset_image_gen(task_id: str, asset_id: str):
    """后台执行资产图片生成"""
    from app.database import SessionLocal
    from app.services.gpt_image_service import gpt_image_service

    db = SessionLocal()
    try:
        asset = db.query(AssetItem).filter(AssetItem.id == asset_id).first()
        if not asset:
            return

        # 构建 prompt
        category_label = {"character": "character design sheet", "scene": "environment concept art", "prop": "prop design reference"}.get(asset.category, "concept art")
        prompt = asset.image_prompt or asset.description or asset.name

        # 使用任务全局风格前缀作为风格约束
        task = db.query(Task).filter(Task.id == task_id).first()
        task_prefix = (task.global_prefix or "") if task else ""

        if task_prefix:
            style_clause = f"Style: {task_prefix[:400]}"
        else:
            style_clause = "2D Japanese anime style, cel-shaded, hand-drawn look"

        # 按分类定制约束：场景无人物，道具独立展示
        if asset.category == "scene":
            category_constraint = (
                "Empty environment, no characters or people visible, "
                "spatial layout and atmosphere only, architectural detail, lighting and mood"
            )
        elif asset.category == "prop":
            category_constraint = (
                "Isolated object on neutral background, no characters, "
                "product design reference sheet, front and side view, "
                "detailed material and shape, no hands or people holding it"
            )
        else:  # character —— 三视图设定表
            category_constraint = CHARACTER_TURNAROUND_CONSTRAINT

        full_prompt = (
            f"{style_clause}. "
            f"{category_label}: {prompt[:800]}. "
            f"{category_constraint}."
        )[:2000]

        # 生成
        from app.services.prompt_builder import prompt_builder

        if asset.category == "character":
            # 三视图设定表：版式约束必须是英文原文，不能被 prompt_builder 场景化重写。
            # 只让 prompt_builder 翻译「风格前缀 + 角色描述」（中文全局前缀 → 英文）。
            try:
                base_prompt = await prompt_builder.build_image_prompt(
                    f"{style_clause}. {category_label}: {prompt[:800]}."
                )
            except Exception:
                base_prompt = f"{style_clause}. {category_label}: {prompt[:800]}."
            # 版式约束前置，权重最高，避免被立绘描述覆盖
            english_prompt = f"{CHARACTER_TURNAROUND_CONSTRAINT}. {base_prompt}"[:2000]
        else:
            try:
                english_prompt = await prompt_builder.build_image_prompt(full_prompt)
            except Exception:
                english_prompt = full_prompt

        local_path, remote_url = await gpt_image_service.generate_asset_image(
            task_id, f"{asset.category}_{asset.name}", english_prompt
        )

        # 上传到 OSS（失败降级为本地 /media）
        oss_key = None
        try:
            from app.services.storage import storage
            oss_key = await asyncio.to_thread(storage.upload, local_path)
        except Exception as e:
            logger.warning(f"[{task_id}] OSS 上传失败（忽略）: {e}")

        # 更新
        asset.image_path = local_path
        asset.image_url = remote_url
        asset.image_oss_key = oss_key
        asset.image_status = "success"
        db.commit()

        # 角色额外生成正脸定妆图（纯 GPT-Image-2 体系的外貌锚点）
        if asset.category == "character" and (asset.portrait_prompt or "").strip():
            try:
                portrait_path, portrait_url = await gpt_image_service.generate_portrait(
                    task_id, asset.name, asset.portrait_prompt
                )
                asset.portrait_path = portrait_path
                asset.portrait_url = portrait_url
                db.commit()
                logger.info(f"[{task_id}] 角色正脸定妆图已生成: {asset.name}")
            except Exception as e:
                logger.warning(f"[{task_id}] 角色定妆图生成失败（非致命）: {asset.name}: {e}")

        event_bus.publish(task_id, "asset", {
            "asset_id": asset.id,
            "task_id": task_id,
            "image_status": "success",
            "error_message": None,
            "url": storage.get_signed_url(oss_key) if oss_key else None,
            # 补发原始 URL / 本地路径，OSS 未配置（url=None）时前端也能反显
            "image_url": asset.image_url,
            "image_path": asset.image_path,
        })
        logger.info(f"[{task_id}] 资产图片生成成功: {asset.name}")

    except Exception as e:
        asset = db.query(AssetItem).filter(AssetItem.id == asset_id).first()
        if asset:
            asset.image_status = "failed"
            asset.error_message = str(e)[:500]
            db.commit()
            event_bus.publish(task_id, "asset", {
                "asset_id": asset.id,
                "task_id": task_id,
                "image_status": "failed",
                "error_message": asset.error_message,
                "url": None,
            })
        logger.error(f"[{task_id}] 资产图片生成失败: {e}")
    finally:
        db.close()


# ── 图片上传 ──

@router.post("/{task_id}/assets/{asset_id}/upload-image")
async def upload_asset_image(
    task_id: str,
    asset_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """自定义上传资产参考图"""
    _get_task_or_404(task_id, db)
    asset = db.query(AssetItem).filter(
        AssetItem.id == asset_id, AssetItem.task_id == task_id
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    # 校验文件类型
    allowed = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}，仅支持 PNG/JPG/WebP/GIF")

    # 保存文件
    safe_name = asset.name.replace("/", "_").replace("\\", "_")[:50]
    ext = os.path.splitext(file.filename or ".png")[1] or ".png"
    dir_path = os.path.join(settings.media_dir, task_id, "assets")
    os.makedirs(dir_path, exist_ok=True)
    filename = f"{asset.category}_{safe_name}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = os.path.join(dir_path, filename)

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # 上传到 OSS（失败降级为本地 /media）
    oss_key = None
    try:
        from app.services.storage import storage
        oss_key = await asyncio.to_thread(storage.upload, filepath)
    except Exception as e:
        logger.warning(f"[{task_id}] OSS 上传失败（忽略）: {e}")

    # 删除旧图片
    if asset.image_path and os.path.isfile(asset.image_path):
        try:
            os.remove(asset.image_path)
        except OSError:
            pass

    # 更新
    asset.image_path = filepath
    asset.image_url = None
    asset.image_oss_key = oss_key
    asset.image_status = "success"
    asset.error_message = None
    db.commit()
    event_bus.publish(task_id, "asset", {
        "asset_id": asset.id,
        "task_id": task_id,
        "image_status": "success",
        "error_message": None,
        "url": storage.get_signed_url(oss_key) if oss_key else None,
    })

    return {"status": "uploaded", "file_path": filepath, "asset_id": asset_id}
