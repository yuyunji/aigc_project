"""
资产拆解服务 —— 从原著源文本提取角色/场景/道具并合并入库。

链路位置：文本分片 → 【资产拆解】 → 导演镜头拆解（见 task_manager._run_pipeline）。
输入是用户上传/粘贴的小说源文本（task.source_text），不依赖分镜脚本（此时分镜尚未生成）。

合并语义（对应前端「AI 重新提取」按钮）：
- 名称匹配到已有资产 → 只覆盖文本字段（名称/描述/image_prompt/空间布局/定妆 prompt），
  已生成的图片及其状态（image_path/url/oss_key/portrait_*/image_status）一律原样保留
- 新资产 → 插入，image_status=pending 待生成图片
- 旧资产未出现在新提取结果中 → 已出图的保留（不动已生成的资产图），未出图的删除
"""
from __future__ import annotations

import asyncio
import logging
import re

from app.config import settings
from app.database import SessionLocal
from app.models.asset import AssetItem
from app.services.llm_service import llm_service
from app.services.text_processor import TextProcessor
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

# LLM 结果分类键 → 资产分类
_CATEGORY_KEYS = (("characters", "character"), ("scenes", "scene"), ("props", "prop"))


def build_source_excerpt(source_text: str) -> str:
    """
    按与导演镜头拆解一致的分片口径截取源文本（最多 max_chunks_for_llm 片），
    保证资产拆解与镜头拆解看到的是同一段原著。
    """
    chunks = TextProcessor.chunk_text(source_text or "")
    if not chunks:
        return ""
    joined = "\n\n".join(chunks[: settings.max_chunks_for_llm])
    return joined[: settings.max_chunks_for_llm * settings.max_chunk_size]


def _norm_name(name: str) -> str:
    """名称归一化：去空白 + 小写，用于跨次提取匹配同一资产"""
    return re.sub(r"\s+", "", name or "").strip().lower()


def _has_generated_image(asset: AssetItem) -> bool:
    """是否已产出过图片（含定妆图）—— 有此标记的资产在重新提取时一律保留"""
    return bool(
        asset.image_status == "success"
        or asset.image_path
        or asset.image_url
        or asset.image_oss_key
        or asset.portrait_path
        or asset.portrait_url
    )


def _iter_items(result: dict):
    """展平 LLM 提取结果 → (category, item)"""
    for key, category in _CATEGORY_KEYS:
        for item in result.get(key) or []:
            if isinstance(item, dict) and (item.get("name") or "").strip():
                yield category, item


def _apply_text_fields(asset: AssetItem, item: dict, category: str) -> None:
    """只覆盖文本字段：新结果缺字段时保留旧值，不把已有描述清空"""
    asset.name = (item.get("name") or "").strip()[:200]

    description = (item.get("description") or "").strip()
    if description:
        asset.description = description[:5000]

    image_prompt = (item.get("visual_prompt") or "").strip()
    if image_prompt:
        asset.image_prompt = image_prompt[:2000]

    spatial_layout = (item.get("spatial_layout") or "").strip()
    if spatial_layout:
        asset.spatial_layout = spatial_layout[:2000]

    portrait_prompt = (item.get("portrait_prompt") or "").strip()
    if portrait_prompt:
        asset.portrait_prompt = portrait_prompt[:1000]

    # 分类只在没出图时跟随新结果：已生成的角色三视图不能挂到场景行上
    if not _has_generated_image(asset):
        asset.category = category


def _merge_assets(task_id: str, result: dict) -> dict:
    """
    把提取结果合并入库（图片字段不动），返回统计：
    {"extracted", "added", "updated", "kept", "removed", "characters", "scenes", "props"}
    """
    db = SessionLocal()
    try:
        existing = db.query(AssetItem).filter(AssetItem.task_id == task_id).all()

        # 先按「分类+名称」精确匹配，再退化为仅名称匹配（模型可能重新分类）
        exact: dict[tuple[str, str], AssetItem] = {}
        loose: dict[str, AssetItem] = {}
        for a in existing:
            key = _norm_name(a.name)
            if not key:
                continue
            exact.setdefault((a.category, key), a)
            loose.setdefault(key, a)

        matched: set[str] = set()
        names: dict[str, list[str]] = {"character": [], "scene": [], "prop": []}
        added = updated = 0

        for category, item in _iter_items(result):
            name = (item.get("name") or "").strip()[:200]
            key = _norm_name(name)
            asset = exact.get((category, key)) or loose.get(key)

            if asset is not None:
                if asset.id in matched:
                    logger.warning(f"[{task_id}] 资产提取结果重复条目，已跳过: {name}")
                    continue
                matched.add(asset.id)
                _apply_text_fields(asset, item, category)
                updated += 1
            else:
                asset = AssetItem(
                    task_id=task_id,
                    category=category,
                    name=name,
                    description=(item.get("description") or "")[:5000],
                    image_prompt=(item.get("visual_prompt") or "")[:2000],
                    spatial_layout=(item.get("spatial_layout") or "")[:2000] or None,
                    portrait_prompt=(item.get("portrait_prompt") or "")[:1000] or None,
                    image_status="pending",
                )
                db.add(asset)
                added += 1

            names[category].append(name)

        # 未出现在新结果里的旧资产：已出图的保留，未出图的删除
        kept = removed = 0
        for a in existing:
            if a.id in matched:
                continue
            if _has_generated_image(a):
                kept += 1
            else:
                db.delete(a)
                removed += 1

        db.commit()
        return {
            "extracted": added + updated,
            "added": added,
            "updated": updated,
            "kept": kept,
            "removed": removed,
            "characters": names["character"],
            "scenes": names["scene"],
            "props": names["prop"],
        }
    finally:
        db.close()


async def extract_assets_from_source(task_id: str, source_text: str) -> dict:
    """
    从原著源文本提取资产并合并入库。

    Raises:
        LLMAPIError: 源文本为空，或 LLM 调用/结果解析失败
    """
    excerpt = build_source_excerpt(source_text)
    if not excerpt.strip():
        raise LLMAPIError("原著文本为空，无法提取资产")

    result = await llm_service.generate_asset_breakdown(excerpt)
    stats = await asyncio.to_thread(_merge_assets, task_id, result)

    logger.info(
        f"[{task_id}] 资产拆解: 新增 {stats['added']} / 更新 {stats['updated']} / "
        f"保留已出图 {stats['kept']} / 删除未出图 {stats['removed']}"
    )
    return stats
