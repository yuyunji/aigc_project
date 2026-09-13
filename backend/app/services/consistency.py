"""
着装一致性检查器 —— 校验角色资产是否具备可用的着装描述。

解决的问题：
- 角色资产若缺着装字段，跨镜头会出现服装漂移。
- check_wardrobe_completeness(task_id) 返回「缺着装描述」的角色名清单，
  供前端提示用户补全。
"""
from __future__ import annotations

import re

from app.database import SessionLocal
from app.models.asset import AssetItem

# 服装专属关键词：单独剥离成「服装锁」（一致性最强锚点）
_WARDROBE_KEYS = (
    "服装", "衣着", "服饰", "穿着", "衣裳", "打扮", "衣装", "装束",
)


def _extract_wardrobe(description: str, portrait_prompt: str, image_prompt: str) -> str:
    """
    单独剥离「服装锁」：从 description 的服装字段优先取中文；
    兜底从 portrait_prompt / image_prompt 里按服装关键词提取。
    返回措辞固定的服装描述（可为空）。
    """
    desc = description or ""
    # 1) 优先从结构化描述里取「服装/衣着/服饰」字段
    for m in re.finditer(r"[-*]\s*\*\*(.+?)\*\*[：:]\s*(.+)", desc):
        key = m.group(1).strip()
        value = m.group(2).strip()
        if any(kw in key for kw in _WARDROBE_KEYS):
            return re.sub(r"\s+", " ", value).strip()
    # 2) 兜底：从 portrait_prompt / image_prompt 里提取服装相关句
    for src in (portrait_prompt, image_prompt):
        if not src:
            continue
        # 匹配中英文服装关键词附近的短语
        m = re.search(
            r"(?:outfit|costume|clothing|attire|服装|衣着|服饰|穿着)[：:是，, ]+(.{2,80}?)(?=[，。,.;\s]|$)",
            src, re.IGNORECASE,
        )
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip().rstrip("，,。.;")
    return ""


def _extract_wardrobe_states(description: str) -> list[dict]:
    """
    从角色 description 拆出「多个着装状态」。

    韩萧这类角色 description 含分号分隔的多个状态（如
    「实验状态下赤裸上身，身上常贴有仪器导线；前世日常穿休闲卫衣」）。
    每段抽成 {state_key(触发词), wardrobe(着装描述)}，供状态感知匹配。

    只保留「含着装/状态语义」的片段；纯外貌句（无服装、无状态词）跳过，
    避免把「银发红瞳」之类也算成着装状态。
    """
    desc = description or ""
    states: list[dict] = []
    # 状态/服装触发词：命中即认为该片段是着装状态（避免单字「衣/衫/裙」过宽误命中）
    STATE_WORDS = (
        "赤裸", "裸露", "光着", "上身", "贴身", "绷带", "湿身", "披挂",
        "袒露", "缠满", "包裹", "日常", "实验", "战斗", "作战", "居家",
        "睡衣", "礼服", "运动", "卫衣", "劲装", "长袍", "短打", "盔甲",
        "甲胄", "战袍", "兜帽", "斗篷", "风衣", "西装", "校服", "汉服",
        "铠甲", "制服", "白大褂", "夜行衣", "衬衫", "外衣", "连体",
        "着装", "穿着", "穿戴", "打扮", "衣装", "装束", "衣裳",
    )
    for seg in re.split(r"[；;。]", desc):
        seg = seg.strip()
        if not seg:
            continue
        # 仅保留含着装/状态词的片段
        if not any(kw in seg for kw in STATE_WORDS):
            continue
        # 状态触发词（用于匹配镜头 environment/subject）
        state_key = "".join(kw for kw in STATE_WORDS if kw in seg)
        states.append({
            "state_key": state_key,
            "wardrobe": re.sub(r"\s+", " ", seg).strip(),
        })
    return states


def check_wardrobe_completeness(task_id: str) -> list[str]:
    """
    体检一个任务的角色资产，返回「缺着装描述」的角色名清单。

    判定标准（与状态感知锁保持一致）：角色的 description / portrait_prompt /
    image_prompt 里能抽出至少一个着装状态（含自然语言的「实验状态赤裸上身」、
    「日常穿卫衣」等，不强制结构化「服装：」字段）。三者皆抽不出 → 记入告警。

    用于前端提示用户哪些角色需要补全着装，以保证跨镜头服装一致。
    """
    db = SessionLocal()
    try:
        assets = (
            db.query(AssetItem)
            .filter(
                AssetItem.task_id == task_id,
                AssetItem.category == "character",
            )
            .all()
        )
        warnings: list[str] = []
        for a in assets:
            # 1) 状态感知：description 拆出的着装状态
            states = _extract_wardrobe_states(a.description or "")
            has_wardrobe = bool(states)
            # 2) 兜底：结构化「服装」字段 或 prompt 里的服装描述
            if not has_wardrobe:
                has_wardrobe = bool(_extract_wardrobe(
                    a.description or "", a.portrait_prompt or "", a.image_prompt or ""
                ))
            if not has_wardrobe:
                warnings.append(a.name or a.id)
        return warnings
    finally:
        db.close()
