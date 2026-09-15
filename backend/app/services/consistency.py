"""
着装一致性 —— 角色资产着装状态的抽取、体检与提示词拼装。

解决的问题：
- 角色资产若缺着装字段，跨镜头会出现服装漂移。
- check_wardrobe_completeness(task_id) 返回「缺着装描述」的角色名清单，
  供前端提示用户补全。
- build_character_core_prompts(assets) 把角色资产拼成「角色设定」文本，
  注入分镜 LLM 调用，成为每镜「人物角色核心提示词：」行的唯一事实来源。
"""
from __future__ import annotations

import logging
import re

from app.database import SessionLocal
from app.models.asset import AssetItem

logger = logging.getLogger(__name__)

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
        # 匹配中英文服装关键词附近的短语。断点只认句末标点与换行：
        # 英文提示词用空格分词（"outfit: white lab coat, …"），
        # 按空白断会把整段服装截成一个 "white"，比不提取更误导。
        m = re.search(
            r"(?:outfit|costume|clothing|attire|服装|衣着|服饰|穿着)[：:是，, ]+(.{2,80}?)(?=[。；;\n]|$)",
            src, re.IGNORECASE,
        )
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip().rstrip("，,。.;")
    return ""


def _split_wardrobe_segments(description: str) -> tuple[list[dict], list[str]]:
    """
    把角色 description 按句拆成（着装状态段，非着装外貌段）。

    STATE_WORDS 是「哪一段算着装状态」的唯一判定源，两个返回值互补，
    调用方不必各自再判一遍，避免口径漂移。
    """
    desc = description or ""
    states: list[dict] = []
    appearance: list[str] = []
    # 状态/服装触发词：命中即认为该片段是着装状态（避免单字「衣/衫/裙」过宽误命中）
    STATE_WORDS = (
        "赤裸", "裸露", "光着", "上身", "贴身", "绷带", "湿身", "披挂",
        "袒露", "缠满", "包裹", "日常", "实验", "战斗", "作战", "居家",
        "睡衣", "礼服", "运动", "卫衣", "劲装", "长袍", "短打", "盔甲",
        "甲胄", "战袍", "兜帽", "斗篷", "风衣", "西装", "校服", "汉服",
        "铠甲", "制服", "白大褂", "夜行衣", "衬衫", "外衣", "连体",
        "着装", "穿着", "穿戴", "打扮", "衣装", "装束", "衣裳",
    )
    # 换行也当断句：资产拆解常输出 `**外貌**：…\n**服装**：…` 这种结构化描述，
    # 只按中文句号/分号切会把外貌与服装粘成一段
    for seg in re.split(r"[；;。\n]", desc):
        seg = re.sub(r"\s+", " ", seg).strip()
        if not seg:
            continue
        # 仅保留含着装/状态词的片段，其余归外貌
        if not any(kw in seg for kw in STATE_WORDS):
            appearance.append(seg)
            continue
        # 状态触发词（用于匹配镜头 environment/subject）
        state_key = "".join(kw for kw in STATE_WORDS if kw in seg)
        states.append({"state_key": state_key, "wardrobe": seg})
    return states, appearance


def _extract_wardrobe_states(description: str) -> list[dict]:
    """
    从角色 description 拆出「多个着装状态」。

    韩萧这类角色 description 含分号分隔的多个状态（如
    「实验状态下赤裸上身，身上常贴有仪器导线；前世日常穿休闲卫衣」）。
    每段抽成 {state_key(触发词), wardrobe(着装描述)}，供状态感知匹配。

    只保留「含着装/状态语义」的片段；纯外貌句（无服装、无状态词）跳过，
    避免把「银发红瞳」之类也算成着装状态。
    """
    return _split_wardrobe_segments(description)[0]


# 注入分镜 LLM 的角色设定文本上限（字符）——超出的角色丢弃并在日志点名，
# 而不是把整段截断（截断会让最后一个角色的着装状态只剩半句，比没有更误导）
CHARACTER_PROMPT_MAX_CHARS = 4000

# 无着装信息时的占位：给模板的「从原文提炼」兜底规则一个确定性触发点
_NO_WARDROBE_HINT = "未提供，请从原文中该角色的着装描写提炼"

# 资产 description 里的 Markdown 标记（`**服装**：`、`- ` 列表符）：
# 它们对下游 LLM 是噪音，拼装时清掉
_MD_MARKS = re.compile(r"[*#`]+")
# 结构化描述的字段标签：`服装：黑色劲装` → `黑色劲装`。
# 着装与外形的标签一起剥——拼装后每行只有「@名字：外貌描述」+「着装状态：…」，
# 再留一层「服装：」「外貌：」既冗余又和行首的「着装状态：」打架。
_FIELD_LABEL = re.compile(
    r"^(服装|衣着|服饰|穿着|着装|外貌|长相|外形|面容|体貌|形象)\s*[：:]\s*"
)


def _clean_segment(seg: str) -> str:
    """去掉 Markdown 标记与列表符，压平空白"""
    return re.sub(r"\s+", " ", _MD_MARKS.sub("", seg)).strip(" -·、")


def build_character_core_prompts(assets: list) -> str:
    """
    从角色资产拼装「角色设定」文本，作为 user_message 注入分镜 LLM 调用。

    这是每镜「人物角色核心提示词：」行的唯一事实来源：LLM 只能从这里选名字与着装状态，
    因此资产拆解（含服装锁）与分镜脚本的口径天然一致。
    着装状态复用 _split_wardrobe_segments，与 check_wardrobe_completeness 判定同源。

    输出形如：
        @韩萧：银发红瞳，身形瘦削的十八岁青年。
          着装状态：实验状态下赤裸上身，身上贴有仪器导线；日常穿着休闲卫衣
    """
    blocks: list[str] = []
    used = 0
    dropped: list[str] = []

    for a in assets:
        name = (a.name or "").strip()
        if not name:
            continue
        states, appearance = _split_wardrobe_segments(a.description or "")
        if states:
            wardrobe = "；".join(
                _FIELD_LABEL.sub("", _clean_segment(s["wardrobe"])) for s in states
            )
        else:
            # 兜底：结构化「服装」字段 / portrait_prompt 里的服装描述
            wardrobe = _extract_wardrobe(
                a.description or "", a.portrait_prompt or "", a.image_prompt or ""
            )

        appearance_text = " ".join(
            _FIELD_LABEL.sub("", _clean_segment(s)) for s in appearance
        )
        head = f"@{name}：{appearance_text}" if appearance_text else f"@{name}"
        block = f"{head}。\n   着装状态：{wardrobe}" if wardrobe else f"{head}。\n   着装状态：{_NO_WARDROBE_HINT}"

        if used + len(block) > CHARACTER_PROMPT_MAX_CHARS:
            dropped.append(name)
            continue
        blocks.append(block)
        used += len(block)

    if dropped:
        logger.warning(
            f"角色设定超出 {CHARACTER_PROMPT_MAX_CHARS} 字符上限，已丢弃角色: {'、'.join(dropped)}"
        )
    return "\n".join(blocks)


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
