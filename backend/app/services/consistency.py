"""
一致性上下文构建器 —— 统一角色/场景/道具的「一致性圣经」构建。

解决的问题：
1. 原来 _asset_name_matches 在 task_manager 里重复实现 3 处，且「任 2 字子串包含」
   会误命中（资产「大殿」命中「大殿外/偏殿」）或漏配（分镜「李清尘」vs 资产「李清尘（少年）」）。
2. 原 _get_character_bible / _get_scene_reference 只取第一个匹配，同框多角色时其余角色无锚点。
3. 场景一致性只有 environment 一句文字，缺空间布局硬约束。

本模块统一提供：
- match_asset(name, text): 精确 > 全名包含 > 去停用词分词匹配
- build_shot_context(task_id, shot): 返回结构化 {characters, scene, props, layout}
- build_character_bible / build_scene_bible: 生成逐字锁定文本段
"""
from __future__ import annotations

import logging
import re

from app.database import SessionLocal
from app.models.asset import AssetItem

logger = logging.getLogger(__name__)

# 停用词：分词匹配时忽略这些字，降低误命中
_STOPWORDS = set("的与和及在了是就都要对从她他它你我这那有")

# 角色外貌锚点关键词（从 description 中抽取这些字段，剥离版式词）
_APPEARANCE_KEYS = (
    "发型", "发色", "瞳色", "眼睛", "肤色", "脸型", "五官",
    "外貌", "特征", "衣着", "服装", "服饰", "发型", "身材",
    "标志", "配饰", "体型", "面容", "发式", "妆容",
)

# 服装专属关键词：单独剥离成「服装锁」（一致性最强锚点）
_WARDROBE_KEYS = (
    "服装", "衣着", "服饰", "穿着", "衣裳", "打扮", "衣装", "装束",
)

# 服装颜色/款式断言关键词（用于抽取颜色 + 材质，做关键属性断言）
_COLOR_WORDS = (
    "黑", "白", "红", "金", "银", "蓝", "绿", "紫", "灰", "棕",
    "黄", "青", "粉", "橙", "藏", "墨", "米", "咖",
)
_MATERIAL_WORDS = (
    "劲装", "长袍", "短打", "盔甲", "甲胄", "纱", "丝绸", "棉", "皮",
    "革", "战袍", "裙", "衫", "衣", "袍", "兜帽", "斗篷", "风衣",
    "牛仔", "西装", "礼服", "校服", "汉服", "铠甲",
)

# 版式词（三视图设定表里的排版描述，定妆锚点里要剥离）
_LAYOUT_WORDS = (
    "character design sheet", "turnaround", "front view", "side view",
    "back view", "三视图", "设定表", "正面", "侧面", "背面", "正视图",
    "侧视图", "背视图", "reference sheet",
)


def match_asset(name: str, text: str) -> bool:
    """
    资产名与文本匹配。精确 > 全名包含 > 去停用词分词匹配。
    返回 True 表示该资产命中该文本（出现在该镜头）。
    """
    asset_name = (name or "").strip()
    text = (text or "")
    if not asset_name:
        return False
    # 1) 精确匹配
    if asset_name == text:
        return True
    # 2) 全名包含
    if asset_name in text:
        return True
    # 3) 去停用词分词匹配：连续 >=2 字且非纯停用词的子串命中
    for length in range(len(asset_name), 1, -1):
        for i in range(len(asset_name) - length + 1):
            token = asset_name[i:i + length]
            if len(token) < 2:
                continue
            if all(ch in _STOPWORDS for ch in token):
                continue
            if token in text:
                return True
    return False


def _extract_appearance(description: str) -> str:
    """从角色 description 提取外貌锚点，剥离版式词。"""
    desc = description or ""
    parts: list[str] = []
    # 结构化字段：- **外貌**：xxx
    for m in re.finditer(r"[-*]\s*\*\*(.+?)\*\*[：:]\s*(.+)", desc):
        key = m.group(1).strip()
        value = m.group(2).strip()
        if any(kw in key for kw in _APPEARANCE_KEYS):
            parts.append(value)
    if parts:
        joined = "；".join(parts)
    else:
        joined = desc
    # 剥离版式词
    for w in _LAYOUT_WORDS:
        joined = joined.replace(w, "")
    return re.sub(r"\s+", " ", joined).strip()


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


def _extract_wardrobe_assertion(wardrobe: str) -> str:
    """
    从服装锁里抽取「颜色 + 款式材质」做关键属性断言（英文短句）。
    用于压制最容易漂的颜色维度。
    """
    if not wardrobe:
        return ""
    colors = [c for c in _COLOR_WORDS if c in wardrobe]
    materials = [m for m in _MATERIAL_WORDS if m in wardrobe]
    parts = []
    if colors:
        parts.append("colors: " + " & ".join(colors))
    if materials:
        parts.append("style: " + " ".join(materials))
    return "; ".join(parts)


def _load_success_assets(task_id: str, category: str | None = None) -> list[AssetItem]:
    db = SessionLocal()
    try:
        q = db.query(AssetItem).filter(
            AssetItem.task_id == task_id,
            AssetItem.image_status == "success",
        )
        if category:
            q = q.filter(AssetItem.category == category)
        return q.all()
    finally:
        db.close()


def build_character_bible(task_id: str, subject_text: str) -> list[dict]:
    """
    返回本镜头所有出场角色的「一致性锚点」列表。
    每个元素: {name, appearance, wardrobe(服装锁), wardrobe_assertion(颜色断言),
              portrait_prompt, image_prompt}
    """
    if not (subject_text or "").strip():
        return []
    assets = _load_success_assets(task_id, "character")
    result: list[dict] = []
    for a in assets:
        if not match_asset(a.name or "", subject_text):
            continue
        wardrobe = _extract_wardrobe(
            a.description or "", a.portrait_prompt or "", a.image_prompt or ""
        )
        result.append({
            "name": a.name,
            "raw_description": a.description or "",
            "appearance": _extract_appearance(a.description or ""),
            "wardrobe": wardrobe,
            "wardrobe_assertion": _extract_wardrobe_assertion(wardrobe),
            "portrait_prompt": (a.portrait_prompt or "").strip(),
            "image_prompt": (a.image_prompt or "").strip(),
        })
    return result


def build_scene_reference(task_id: str, scene_text: str) -> dict | None:
    """返回本镜头命中的场景参考：{name, layout, image_prompt}"""
    if not (scene_text or "").strip():
        return None
    assets = _load_success_assets(task_id, "scene")
    for a in assets:
        if match_asset(a.name or "", scene_text):
            return {
                "name": a.name,
                "layout": (a.spatial_layout or "").strip(),
                "image_prompt": (a.image_prompt or a.description or "").strip(),
            }
    return None


def build_prop_references(task_id: str, subject_text: str) -> list[dict]:
    """返回命中的道具锚点列表 [{name, image_prompt}]"""
    if not (subject_text or "").strip():
        return []
    assets = _load_success_assets(task_id, "prop")
    result: list[dict] = []
    for a in assets:
        if match_asset(a.name or "", subject_text):
            result.append({
                "name": a.name,
                "image_prompt": (a.image_prompt or a.description or "").strip(),
            })
    return result


def build_shot_context(task_id: str, shot: dict) -> dict:
    """
    一镜上下文聚合入口，供 generate_scene_image / generate_scene_video 复用。

    Returns:
        {
          "characters": [{name, appearance, portrait_prompt, image_prompt}],
          "scene": {name, layout, image_prompt} | None,
          "props": [{name, image_prompt}],
        }
    """
    subject = shot.get("subject", "") or ""
    environment = shot.get("environment", "") or shot.get("location", "") or ""
    combined = f"{subject} {environment}"
    return {
        "characters": build_character_bible(task_id, combined),
        "scene": build_scene_reference(task_id, f"{environment} {shot.get('location','')}"),
        "props": build_prop_references(task_id, combined),
    }


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


def _match_wardrobe_state(states: list[dict], shot_text: str) -> str:
    """
    根据当前镜头的 subject/environment 文本，匹配最相关着装状态。
    命中则返回该状态着装描述；无法判定时返回空（不盲锁）。
    """
    shot_text = shot_text or ""
    if not states:
        return ""
    # 1) 若只有一个状态片段，直接返回（单状态角色）
    if len(states) == 1:
        return states[0]["wardrobe"]
    # 2) 多状态：状态触发词命中镜头文本即返回（优先强状态词：赤裸/绷带/湿身等）
    STRONG = ("赤裸", "裸露", "光着", "上身", "绷带", "湿身", "袒露", "缠满", "贴身")
    WEAK = ("实验", "日常", "卫衣", "战斗", "作战", "居家", "睡衣", "礼服", "劲装",
            "制服", "白大褂", "斗篷", "风衣", "汉服", "盔甲", "甲胄")
    for st in states:
        key = st.get("state_key", "")
        if not key:
            continue
        for kw in STRONG:
            if kw in key and kw in shot_text:
                return st["wardrobe"]
    for st in states:
        key = st.get("state_key", "")
        for kw in WEAK:
            if kw in key and kw in shot_text:
                return st["wardrobe"]
    # 3) 兜底：无法判定则返回空，避免锁错多状态角色的着装
    return ""


def render_character_bible_text(characters: list[dict], shot_text: str = "") -> str:
    """
    将角色锚点渲染为「逐字锁定」的英文文本段（供纯文字 image prompt 注入）。

    着装锁改为「状态感知」：
    - 单状态角色 → 锁其唯一着装；
    - 多状态角色（如韩萧：实验赤裸 vs 日常卫衣）→ 根据当前镜头 shot_text 匹配对应状态，
      只锁命中状态；无法判定则不锁着装（避免锁错，交由分镜 subject 显式描述兜底）。

    锁文本前置 + 强否定，压制靠后镜头因注意力衰减导致的漂移。
    """
    lines: list[str] = []
    for c in characters:
        name = c["name"]
        # 1) 状态感知着装锁
        description = c.get("raw_description", "")
        wardrobe = c.get("wardrobe", "")
        # 优先用拆分后的多状态做状态匹配
        states = _extract_wardrobe_states(description)
        matched_wardrobe = _match_wardrobe_state(states, shot_text) if description else wardrobe
        if matched_wardrobe:
            lock_line = (
                f"[{name}] WARDROBE LOCK — outfit in THIS shot MUST be exactly: "
                f"{matched_wardrobe}. NEVER change its color, style, cut, or details."
            )
            # 颜色/款式断言必须与当前锁定的着装（而非预存字段）同源，避免自相矛盾
            assertion = _extract_wardrobe_assertion(matched_wardrobe)
            if assertion:
                lock_line += f" (OUTFIT {assertion})"
            lines.append(lock_line)
        # 2) 其他外貌锚点（发型/发色/瞳色等）作为次级锁定
        anchor = c.get("portrait_prompt") or c.get("appearance") or c.get("image_prompt")
        if anchor:
            lines.append(
                f"[{name}] appearance MUST remain identical: {anchor}"
            )
    return "\n".join(lines)


def render_scene_lock_text(scene: dict | None) -> str:
    """场景几何锁定文本段。"""
    if not scene:
        return ""
    parts = []
    if scene.get("layout"):
        parts.append(
            f"SCENE LAYOUT (do not alter): {scene['layout']}"
        )
    if scene.get("image_prompt"):
        parts.append(f"SCENE REFERENCE: {scene['image_prompt']}")
    return "\n".join(parts)


def render_prop_lock_text(props: list[dict]) -> str:
    """道具锁定文本段。"""
    lines: list[str] = []
    for p in props:
        if p.get("image_prompt"):
            lines.append(f"PROP LOCK [{p['name']}]: {p['image_prompt']}")
    return "\n".join(lines)


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
