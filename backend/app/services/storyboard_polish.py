"""
分镜后处理校验器 —— 将脚本层「衔接约束」变成确定性硬约束。

在 LLM 生成分镜、解析成 scene_list 之后、入库之前调用。职责：
1. mood 规范化：严格两字情绪词，非法值兜底
2. 景别衔接校验：连续 >= 3 镜同景别时自动改景别（不再依赖模型自觉）
3. 转场规范化：把模型输出任意转场词归一化到白名单，非法/空缺兜底为「硬切」
4. 情绪断崖检测：相邻镜头情绪跳变超阈值时记警告（不强制改写，日志可审计）
5. 段首转场：第一个镜头强制「淡入」（开场专用）

所有规则是纯函数式、确定性、可单测的；不改动原有字段结构，
只在 scene_list 的 dict 上做原地修正，下游 _save_storyboards 无需改动。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 转场白名单（与 STORYBOARD_SINGLE_PROMPT 规则 14 保持同步）
TRANSITION_WHITELIST = {
    "淡入", "硬切", "溶解", "推入", "拉出",
    "闪白", "黑场过渡", "叠化", "匹配剪辑", "模糊过渡",
    "淡出",
}

# 转场归一化别名：模型可能输出近似词，统一映射到白名单
_TRANSITION_ALIASES = {
    "淡入淡出": "淡入", "黑场": "黑场过渡", "淡出过渡": "淡出",
    "溶接": "溶解", "叠接": "叠化", "交叉溶解": "溶解", "交叉叠化": "叠化",
    "闪屏": "闪白", "白闪": "闪白", "闪黑": "黑场过渡",
    "模糊转场": "模糊过渡", "模糊": "模糊过渡",
    "推近": "推入", "拉远": "拉出", "匹配转场": "匹配剪辑",
    "入画": "推入", "出画": "拉出", "快切": "硬切", "直接切换": "硬切",
    "无": "硬切", "无转场": "硬切", "切": "硬切",
}

# 景别阶梯（用于自动改景别时选取相邻档位）
_SHOT_SIZES = ["大特写", "特写", "近景", "中近", "中景", "全景", "远景"]


def _normalize_transition(value: str | None, scene_number: int) -> str:
    """归一化转场词。空值/非法值兜底；第一个镜头强制淡入。"""
    raw = (value or "").strip()
    if scene_number <= 1:
        return "淡入"
    if not raw:
        return "硬切"
    if raw in TRANSITION_WHITELIST:
        return raw
    if raw in _TRANSITION_ALIASES:
        return _TRANSITION_ALIASES[raw]
    # 兜底：可能带额外文字，做包含匹配
    for key in TRANSITION_WHITELIST:
        if key in raw:
            return key
    logger.debug(f"转场词无法识别，兜底硬切: {raw!r}")
    return "硬切"


def _normalize_mood(value: str | None) -> str:
    """情绪氛围严格两字；非法/超长/空值给一个安全兜底。"""
    raw = (value or "").strip()
    if len(raw) == 2:
        return raw
    if raw:
        # 长文本里截取前两字（中文）作为近似
        return raw[:2]
    return "平静"


def _alternate_shot_size(prev: str, current: str) -> str:
    """当出现连续同景别时，把当前镜改到相邻档位拉开距离。"""
    current_clean = current.strip() or "中景"
    if current_clean == prev:
        idx = _SHOT_SIZES.index(current_clean) if current_clean in _SHOT_SIZES else 3
        # 往更紧或更松的方向挪一档（默认向更宽的镜头挪，拉开节奏）
        new_idx = min(idx + 1, len(_SHOT_SIZES) - 1) if idx + 1 < len(_SHOT_SIZES) else idx - 1
        return _SHOT_SIZES[new_idx]
    return current_clean


def polish_storyboards(scene_list: list[dict]) -> list[dict]:
    """
    对解析后的 scene_list 做确定性后处理，返回原地修正后的同一列表。

    规则：
    1. 每个镜头的 mood 归一化为两字
    2. 每个镜头的 transition 归一化到白名单；首镜强制淡入
    3. 连续 >= 3 镜同景别 -> 从第 3 镜起自动改景别（同时同步到 image_prompt 文案）
    4. 情绪断崖检测 -> 记录 warning 日志，不改写
    """
    if not scene_list:
        return scene_list

    # 情绪断崖关键词：从极正向跳到极负向或反之，视为跳变
    _NEG = {"悲伤", "愤怒", "恐惧", "痛苦", "绝望", "仇恨", "崩溃"}
    _POS = {"温馨", "释然", "期待", "幸福", "喜悦", "平静", "安心"}

    for i, scene in enumerate(scene_list):
        scene_num = int(scene.get("scene_number", i + 1))

        # 1) mood 规范化
        scene["mood"] = _normalize_mood(scene.get("mood"))

        # 2) transition 规范化
        scene["transition"] = _normalize_transition(
            scene.get("transition"), scene_num
        )

        # 3) 景别交替校验（连续 >=3 同景别才干预，避免过度改写）
        if i >= 2:
            cur = (scene.get("shot_size") or "").strip()
            p1 = ((scene_list[i - 1].get("shot_size") or "").strip())
            p2 = ((scene_list[i - 2].get("shot_size") or "").strip())
            if cur and cur == p1 == p2:
                new_size = _alternate_shot_size(p1, cur)
                logger.warning(
                    f"镜头 {scene_num} 景别与上两镜连续相同({cur})，自动改为 {new_size}"
                )
                old_ip = scene.get("image_prompt", "")
                if old_ip and cur in old_ip:
                    scene["image_prompt"] = old_ip.replace(cur, new_size, 1)
                scene["shot_size"] = new_size

        # 4) 情绪断崖检测（日志仅提示）
        if i >= 1:
            prev_mood = scene_list[i - 1].get("mood", "")
            cur_mood = scene.get("mood", "")
            prev_neg = prev_mood in _NEG
            cur_pos = cur_mood in _POS
            prev_pos = prev_mood in _POS
            cur_neg = cur_mood in _NEG
            if (prev_neg and cur_pos) or (prev_pos and cur_neg):
                logger.warning(
                    f"镜头 {scene_num} 情绪跳变: {prev_mood} -> {cur_mood}，"
                    "建议人工复核是否缺少过渡镜头"
                )

    return scene_list


# 转场白名单导出（供 video_composer 复用，避免两处定义漂移）
EXPORTED_TRANSITION_WHITELIST = frozenset(TRANSITION_WHITELIST)
