"""
级联任务编排器
完整链路：
  原著文本 → 分片预处理 → 资产拆解（角色/场景/道具）→ 导演镜头拆解
  → 图生视频 → FFmpeg 拼接

阶段 1-3 为文本链路，后续为媒体链路。
媒体链路在文本链完成后自动触发（可在配置中关闭）。
"""
import asyncio
import json
import logging
import math
import re
from contextlib import asynccontextmanager

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.task import Task
from app.models.outline import Outline
from app.models.character import Character
from app.models.storyboard import Storyboard
from app.models.media import MediaAsset
from app.services.text_processor import TextProcessor
from app.services.llm_service import llm_service
from app.services.minimax_service import minimax_service
from app.services.video_composer import video_composer
from app.services.storyboard_polish import polish_storyboards
from app.services.director_storyboard_skill import (
    SCENE_DURATION_MAX,
    SCENE_DURATION_MIN,
)
from app.services.asset_extractor import extract_assets_from_source
from app.services.media_archive import current_round, round_oss_key
from app.services.events import event_bus
from app.utils.exceptions import (
    LLMAPIError,
    TokenLimitError,
    TaskTimeoutError,
    InputTooLargeError,
    EmptyChunksError,
)

logger = logging.getLogger(__name__)

# 友好错误消息映射
FRIENDLY_ERRORS = {
    InputTooLargeError: "输入文本过长（{limit} 字符上限），请缩短后重试",
    EmptyChunksError: "文本分片后无有效内容，请检查输入格式",
    TokenLimitError: "文本超出 AI 处理上限，请缩短输入内容后重试",
    TaskTimeoutError: "任务处理超时，请尝试缩短输入文本后重试",
}

# 分镜导演图风格（前端下拉框的 value → 英文风格描述）
DIRECTOR_STYLE_EN = {
    "guoman3d": (
        "Chinese donghua 3D animation style: stylized non-photorealistic 3D characters, "
        "exaggerated stylized facial features, smooth toon-shaded skin, sculpted stylized hair, "
        "rendered like a high-end 3D animated series"
    ),
    "riman2d": "Japanese 2D anime look, flat cel-shading, clean ink lineart",
    "zhenren": "Cinematic live-action film still, real actors, natural lighting",
}

# 风格锁定语：任务自带的全局风格前缀（如「赛璐璐漫剧风格…电影质感」）在翻译后会与上面的
# 风格前缀争夺主导权，实测会让「国漫3D」出成写实片。故在 prompt 末尾再收一次口。
DIRECTOR_STYLE_LOCK = {
    "guoman3d": (
        "Render as a stylized 3D animated frame: obviously computer-animated, "
        "non-photorealistic stylized faces, not a photograph, not live action, not 2D anime"
    ),
    "riman2d": "Final render must be 2D anime: not live action, not photorealistic, not 3D CGI",
    "zhenren": "Final render must be photorealistic live action: not animation, not illustration",
}

# 6 宫格版式约束：必须拼在中文翻译**之后**，否则会被 prompt_builder 当成场景描述改写掉
DIRECTOR_BOARD_CONSTRAINT = (
    "Storyboard sheet: a six-panel storyboard board arranged in a 2x3 grid, "
    "six sequential frames showing the continuous progression of this shot, "
    "each panel clearly separated by thin white borders, "
    "consistent characters and camera style across all panels, "
    "numbered panels from top-left to bottom-right"
)

# 是否自动执行媒体链路（由 settings.auto_media_pipeline 控制）


class TaskManager:
    """
    级联任务调度器。

    链路阶段：
    1. 文本预处理（分片）
    2. 资产拆解 → 从原著源文本提取角色/场景/道具存入 asset_items 表
       （任务已有资产时跳过，保留已生成的资产图与手工编辑）
    3. 导演镜头拆解 → 解析后逐条存入 storyboards 表
    4. 媒体链路（可在配置中关闭）

    每阶段更新任务进度，异常时标记 failed 并记录友好错误信息。
    """

    # ------------------------------------------------------------------
    # 主链路
    # ------------------------------------------------------------------

    async def process_task(self, task_id: str, source_text: str) -> None:
        """
        执行完整级联链路，带总超时保护。

        Args:
            task_id:     任务唯一 ID
            source_text: 用户输入的原始文本
        """
        total_timeout = settings.task_total_timeout

        try:
            await asyncio.wait_for(
                self._run_pipeline(task_id, source_text),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            error_msg = f"任务执行超时（{total_timeout} 秒），请尝试缩短输入文本"
            self._update_status(task_id, "failed", error=error_msg)
            logger.error(f"[{task_id}] 任务总超时 ({total_timeout}s)")

        except TokenLimitError as e:
            friendly = self._friendly_error(e)
            self._update_status(task_id, "failed", error=friendly)
            logger.warning(f"[{task_id}] Token 超限: {e}")

        except LLMAPIError as e:
            friendly = self._friendly_error(e)
            self._update_status(task_id, "failed", error=friendly)
            logger.error(f"[{task_id}] AI 服务错误: {e}")

        except (InputTooLargeError, EmptyChunksError) as e:
            friendly = self._friendly_error(e)
            self._update_status(task_id, "failed", error=friendly)
            logger.warning(f"[{task_id}] 输入校验失败: {friendly}")

        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            self._update_status(task_id, "failed", error=error_msg)
            logger.exception(f"[{task_id}] 未预期错误")

    @staticmethod
    @asynccontextmanager
    async def _progress_heartbeat(
        task_id: str,
        start: int,
        end: int,
        interval: float = 20.0,
    ):
        """
        LLM 长调用期间的心跳进度。

        单次 LLM 调用实测可达 6 分钟以上（doubao 返回 363s），而 40% 恰好打在调用之前、
        下一个真实进度点（78%）在调用之后，中间没有任何写入 —— 前端只能看到进度条静止，
        无法区分「生成慢」和「已经卡死」。

        这里按指数渐近曲线把进度从 start 缓慢推向 end（永不越过 end），
        只为给前端一个「仍在推进」的信号；LLM 返回后立即被真实进度点覆盖。
        """
        async def tick() -> None:
            ticks = 0
            while True:
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    return
                ticks += 1
                # 渐近逼近：以「心跳次数」为尺度（每 6 跳衰减一档），
                # 曲线形状不随 interval 取值而变；跳数越多推进越慢，天然封顶在 end 之下。
                # interval=20s 时 6 跳≈2 分钟，实测 363s 的调用约推进到 74%。
                ratio = 1.0 - math.exp(-ticks / 6.0)
                TaskManager._update_status(
                    task_id, "running", progress=int(start + (end - start) * ratio)
                )

        ticker = asyncio.create_task(tick())
        try:
            yield
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

    async def _run_pipeline(self, task_id: str, source_text: str) -> None:
        """执行实际级联流水线（内部方法，由 process_task 超时包装调用）"""

        # ── 阶段 0：输入校验 ──
        self._update_status(task_id, "running", progress=5)
        logger.info(f"[{task_id}] 阶段0: 输入校验")
        TextProcessor.validate_input(source_text)

        # ── 阶段 1：文本预处理 ──
        self._update_status(task_id, "running", progress=10)
        logger.info(f"[{task_id}] 阶段1: 文本预处理开始")

        result = await asyncio.to_thread(TextProcessor.preprocess, source_text)
        chunks = result["chunks"]
        metadata = result["metadata"]

        logger.info(
            f"[{task_id}] 文本分片完成: "
            f"原文 {metadata['original_length']} 字 → {metadata['total_chunks']} 片"
            f"（LLM 使用前 {metadata['effective_chunks']} 片）"
        )
        self._update_status(task_id, "running", progress=20)

        # 阶段超时 = 单次 LLM 超时 × (1 + 重试次数) + 缓冲，保证内部重试有机会跑完
        stage_timeout = settings.llm_call_timeout * (settings.llm_max_retries + 1) + 60

        # ── 阶段 2：资产拆解（早于镜头拆解，输入为原著源文本）──
        # 已有资产则跳过：重新任务不重做资产，已生成的资产图与手工编辑一并保留；
        # 需要重做请在前端点「AI 重新提取」。
        asset_count = self._count_assets(task_id)
        if asset_count:
            logger.info(f"[{task_id}] 阶段2: 已有 {asset_count} 个资产，跳过资产拆解")
        else:
            logger.info(f"[{task_id}] 阶段2: 资产拆解（原著源文本）")
            self._update_status(task_id, "running", progress=25)
            try:
                async with TaskManager._progress_heartbeat(task_id, 25, 33):
                    stats = await asyncio.wait_for(
                        extract_assets_from_source(task_id, source_text),
                        timeout=stage_timeout,
                    )
                logger.info(
                    f"[{task_id}] 资产拆解完成: 新增 {stats['added']} / 更新 {stats['updated']} 个资产"
                )
            except Exception as e:
                # 资产拆解失败不阻断镜头链路：结果页仍可手动「AI 重新提取」
                logger.warning(f"[{task_id}] ⚠️ 资产拆解失败（非致命，可稍后重新提取）: {e}")
        self._update_status(task_id, "running", progress=35)

        # ── 阶段 3：导演镜头拆解（单次 LLM 调用，模板见 director_storyboard_skill）──
        logger.info(f"[{task_id}] 阶段3: 导演镜头拆解")
        self._update_status(task_id, "running", progress=40)

        # 角色设定：从资产拆解确定性拼装（阶段2 已先执行）。注入后每镜的
        # 「人物角色核心提示词：」行只能从这里取名字与着装状态，与资产图口径一致；
        # 资产缺失（拆解失败/无角色）时传空，模板规则退化为「从原文提炼」。
        character_prompts = TaskManager._load_character_prompts(task_id)

        async with TaskManager._progress_heartbeat(task_id, 40, 76):
            storyboard_text = await asyncio.wait_for(
                llm_service.generate_storyboard_single(
                    chunks, character_prompts=character_prompts
                ),
                timeout=stage_timeout,
            )

        if not storyboard_text or not storyboard_text.strip():
            raise LLMAPIError("分镜提示词生成结果为空，请重试")

        logger.info(
            f"[{task_id}] LLM 返回 {len(storyboard_text)} 字符: "
            f"{storyboard_text[:200].replace(chr(10), '↵')}..."
        )

        # 提取 GLOBAL_PREFIX
        global_prefix = self._extract_global_prefix(storyboard_text)
        if global_prefix:
            self._save_global_prefix(task_id, global_prefix)
            logger.info(f"[{task_id}] 全局前缀: {global_prefix[:80]}...")
        else:
            logger.warning(f"[{task_id}] 未提取到全局前缀")

        # POST_CONSTRAINT 提取已移除：导演模板对齐范本后不再产出该机器行，
        # 提取必然失败，此前每次运行都会打一条「未提取到后置约束」的假告警。
        # 旧的 post_constraint 数据仍被 _get_post_constraint 读取并注入视频提示词，见该函数注释。

        # 解析导演镜头脚本（失败则回退旧一行式模板解析）
        scene_list = self._parse_director_storyboard(storyboard_text)
        if not scene_list:
            logger.warning(f"[{task_id}] 导演模板解析无结果，回退旧模板解析")
            scene_list = self._parse_template_storyboard(storyboard_text)
        if not scene_list:
            raise LLMAPIError("导演镜头脚本解析失败，请重试")

        logger.info(f"[{task_id}] 导演镜头脚本解析完成: {len(scene_list)} 个镜头")

        # 分镜后处理校验：景别交替 / 情绪两字 / 转场白名单 / 情绪断崖检测
        scene_list = polish_storyboards(scene_list)
        logger.info(f"[{task_id}] 分镜后处理校验完成: {len(scene_list)} 个镜头")

        # 合理性检查
        if len(scene_list) < 8:
            logger.warning(
                f"[{task_id}] ⚠️ 仅解析到 {len(scene_list)} 镜，数量过少，LLM 可能未正确理解任务"
            )
        elif len(scene_list) > 80:
            # 硬截断：超出 80 镜部分直接丢弃，防止下游图片/视频管线失控
            logger.warning(
                f"[{task_id}] ⚠️ 解析到 {len(scene_list)} 镜，超过 80 上限，截断至前 80 镜"
            )
            scene_list = scene_list[:80]

        self._save_storyboards(task_id, scene_list)
        self._update_status(task_id, "running", progress=78)

        # ── 阶段 4-5：媒体链路（文生视频/图生视频 + FFmpeg 拼接）──
        video_paths = []
        if settings.auto_media_pipeline:
            video_paths = await self._run_storyboard_to_video(task_id, scene_list)
            await self._run_composite(task_id, video_paths, None, scene_list, [])

        # ── 完成 ──
        self._update_status(task_id, "success", progress=100)
        summary = f"分镜提示词 {len(scene_list)} 个"
        if settings.auto_media_pipeline:
            summary += f", 视频{len(video_paths)}段"
        logger.info(f"[{task_id}] ✅ 级联任务完成: {summary}")

    # ------------------------------------------------------------------
    # 友好错误消息
    # ------------------------------------------------------------------

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        """将已知异常转为用户可读的错误消息"""
        for exc_type, template in FRIENDLY_ERRORS.items():
            if isinstance(exc, exc_type):
                attrs = {
                    "limit": getattr(exc, "limit", "未知"),
                }
                try:
                    return template.format(**attrs)
                except KeyError:
                    return template
        return str(exc)

    # ------------------------------------------------------------------
    # 全局前缀 / 后置约束 管理
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_global_prefix(raw_text: str) -> str:
        """
        提取全局风格前缀（画风 + 世界观 + 色调 + 材质 + 角色外观设定）。

        优先取旧格式的 `GLOBAL_PREFIX：…` 机器行；新格式（对齐 docx 范本）没有该行，
        全局风格就是第一个「镜头NN：」标题之前的首行非空文本。
        """
        match = re.search(r"GLOBAL_PREFIX[：:]\s*(.+)", raw_text)
        if match:
            return match.group(1).strip()
        # 新格式：取第一个镜头标题之前的第一行非空文本
        first_header = re.search(
            r"^[ \t#>*]*镜头\s*\d+\s*[：:]", raw_text, flags=re.M
        )
        head = raw_text[: first_header.start()] if first_header else raw_text[:2000]
        for line in head.split("\n"):
            s = line.strip().strip("*#").strip()
            if s:
                return s
        return ""

    @staticmethod
    def _save_global_prefix(task_id: str, prefix: str) -> None:
        """将全局前缀保存到 tasks 表"""
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.global_prefix = prefix
                db.commit()
        finally:
            db.close()

    @staticmethod
    def _get_global_prefix(task_id: str) -> str:
        """从 tasks 表读取全局前缀"""
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            return (task.global_prefix or "") if task else ""
        finally:
            db.close()

    @staticmethod
    def _get_post_constraint(task_id: str) -> str:
        """
        从 tasks 表读取后置约束。

        ⚠️ 只读不写：新模板不再产出 POST_CONSTRAINT 行，因此新建任务该字段恒为空，
        本函数对它们是无操作。保留是因为**历史任务的存量值仍含有效的负向提示词**
        （无字幕 / 无水印 / 无手指畸变等），这些值还会被注入视频提示词；
        直接删掉读取链路会让老任务重出图时丢失这部分约束。
        待存量任务全部退役后可连同 tasks.post_constraint 列一起清理。
        """
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            return (task.post_constraint or "") if task else ""
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 模板格式解析（字段名分隔方式，兼容任意镜数）
    # ------------------------------------------------------------------

    # 画质补充默认值（LLM 未输出时使用）。注意：不得强制「服饰」，避免与「赤裸上身/裸露」等状态冲突
    DEFAULT_QUALITY_NOTES = "金属冷光，发丝清晰，轮廓与表面材质细节完整，抗锯齿高清渲染"

    @staticmethod
    def _parse_template_storyboard(raw_text: str) -> list[dict]:
        """
        解析模板格式的分镜输出。使用字段名作为分隔符（非逗号），
        因为「画面主体人物」「场景环境」的值内部可能包含逗号。
        镜数由 LLM 根据剧本内容自行判断，不做固定限制。

        镜像格式：
            镜头 1，镜头景别：全景，拍摄角度：俯拍，运镜方式：缓慢推镜，
            画面主体人物：...，场景环境：...，情绪氛围：...，
            构图：...，画质补充：...

        返回: [{"scene_number": N, "shot_size": "全景", ...}, ...]
        """
        results = []

        # 将文本按"镜头 N"分割为独立片段
        text = raw_text.replace("\n", " ").replace("\r", " ")
        text = re.sub(r"(镜头\s*\d+\s*[，,])", r"\n\1", text)
        segments = [s.strip() for s in text.split("\n") if s.strip() and s.startswith("镜头")]

        # 用字段名作为分隔符提取各字段值（而非逗号，避免值内含逗号被截断）
        FIELD_NAMES = [
            "镜头景别", "拍摄角度", "运镜方式",
            "画面主体人物", "场景环境", "情绪氛围",
            "构图", "画质补充", "台词对白", "转场衔接", "镜头时长",
        ]

        def _extract(field: str, src: str) -> str:
            """提取 `字段名：值`，到下一个字段名或文本末尾为止"""
            delim = "|".join(FIELD_NAMES)
            pattern = field + r"\s*[：:]\s*(.*?)(?:\s*(?:" + delim + r")\s*[：:]|\s*$)"
            m = re.search(pattern, src)
            if m:
                return m.group(1).strip().rstrip("，,。.")
            return ""

        for seg in segments:
            num_match = re.match(r"镜头\s*(\d+)", seg)
            if not num_match:
                continue
            scene_num = int(num_match.group(1))

            shot_size = _extract("镜头景别", seg)
            camera_angle = _extract("拍摄角度", seg)
            camera_movement = _extract("运镜方式", seg)
            subject = _extract("画面主体人物", seg)
            environment = _extract("场景环境", seg)
            mood = _extract("情绪氛围", seg)
            composition = _extract("构图", seg)
            quality_notes = _extract("画质补充", seg)
            dialogue_text = _extract("台词对白", seg)
            transition = _extract("转场衔接", seg)
            duration_str = _extract("镜头时长", seg)

            # 解析时长（如 "6秒" → 6.0）
            scene_duration = 6.0
            if duration_str:
                dur_match = re.search(r"(\d+)", duration_str)
                if dur_match:
                    scene_duration = max(
                        float(SCENE_DURATION_MIN),
                        min(float(dur_match.group(1)), float(SCENE_DURATION_MAX)),
                    )

            # 跳过不完整的镜头
            if not shot_size or not subject:
                logger.debug(f"镜头 {scene_num} 字段不全，跳过")
                continue

            full_prompt = (
                f"镜头 {scene_num}，镜头景别：{shot_size}，"
                f"拍摄角度：{camera_angle}，运镜方式：{camera_movement}，"
                f"画面主体人物：{subject}，"
                f"场景环境：{environment}，"
                f"情绪氛围：{mood}，"
                f"构图：{composition}，"
                f"画质补充：{quality_notes}"
                + (f"，转场衔接：{transition}" if transition else "")
            )

            human_desc = (
                f"## 镜头 {scene_num}\n\n"
                f"- **镜头景别**：{shot_size}\n"
                f"- **拍摄角度**：{camera_angle}\n"
                f"- **运镜方式**：{camera_movement}\n"
                f"- **画面主体人物**：{subject}\n"
                f"- **场景环境**：{environment}\n"
                f"- **情绪氛围**：{mood}\n"
                f"- **构图**：{composition}\n"
                f"- **画质补充**：{quality_notes}"
                + (f"\n- **台词对白**：{dialogue_text}" if dialogue_text and dialogue_text != "@无" else "")
                + (f"\n- **转场衔接**：{transition}" if transition else "")
            )

            results.append({
                "scene_number": scene_num,
                "shot_size": shot_size,
                "camera_angle": camera_angle,
                "camera_movement": camera_movement,
                "subject": subject,
                "environment": environment,
                "mood": mood,
                "composition": composition,
                "quality_notes": quality_notes,
                "transition": transition,
                "dialogue_text": dialogue_text,
                "duration_seconds": scene_duration,
                "image_prompt": full_prompt,
                "description": human_desc,
                "global_prefix": "",
                "scene_title": f"镜头{scene_num}",
                "location": environment[:80],
                "visual_description": f"{subject}，{environment}，{mood}氛围",
            })

        if results:
            logger.info(f"字段名解析成功: {len(results)} 个镜头")
        else:
            logger.warning("字段名解析无匹配，尝试回退 JSON 解析")
            return TaskManager._parse_storyboards(raw_text)

        return results

    # ------------------------------------------------------------------
    # 导演镜头脚本解析（director-storyboard skill 模板）
    # ------------------------------------------------------------------

    # 机器键值行：镜头：景别=特写｜角度=俯拍｜运镜=…｜情绪=…｜构图=…｜转场=…
    # 同时兼容模型退化为中文冒号/逗号分隔的写法（景别：特写，角度：俯拍）
    _KV_TO_FIELD = {
        "景别": "shot_size",
        "角度": "camera_angle",
        "运镜": "camera_movement",
        "情绪": "mood",
        "构图": "composition",
        "转场": "transition",
    }
    _KV_FIELD_RE = re.compile(r"(景别|角度|运镜|情绪|构图|转场)\s*[=＝：:]\s*([^｜|，,、;；]+)")

    @staticmethod
    def _split_kv_line(value: str) -> dict:
        """
        解析 `键=值｜键=值…` 形式的镜头参数行（兼容 `键：值，键：值`）。

        只认「字段名 + 分隔符」锚定的值。刻意不做全串关键词模糊匹配——
        散文里「放大特写」含「大特写」、「中近景」含「中景」，模糊匹配会静默猜错，
        比留空更糟：留空时 storyboard_polish 会给出安全的确定性兜底。
        """
        out: dict[str, str] = {}
        for key, val in TaskManager._KV_FIELD_RE.findall(value or ""):
            field = TaskManager._KV_TO_FIELD.get(key)
            v = val.strip().strip("*").strip()
            if field and v:
                out.setdefault(field, v)
        return out

    @staticmethod
    def _parse_director_storyboard(raw_text: str) -> list[dict]:
        """
        解析「导演镜头脚本」模板输出（skill: director-storyboard）。

        模板结构（对齐 docx 范本）：
            {首行：全局风格前缀 + 角色外观设定}
            镜头01：{标题}（时长：10秒）
                {氛围段}
                0-3秒： {画面}          ← 可多行
                {角色}： "{台词}"        ← 可多行，位于最后一行分秒画面之后
                摄影与视觉要求：
                    风格/画质/镜头(散文)/光影/动作/比例
                    镜头参数：景别=…｜角度=…｜运镜=…｜情绪=…｜构图=…｜转场=…   ← 机器行
                【首镜头】/【衔接提示→本镜】/【结尾桥接→镜头NN】
            …

        同时兼容旧格式（GLOBAL_PREFIX 机器行 + 作品信息卡 + 导演阐述 + POST_CONSTRAINT）。

        返回与 _parse_template_storyboard 相同结构的 dict 列表，
        以便下游 polish / _save_storyboards / 视频链路无需改动；
        额外带 raw_script（该镜的原文块，供结果页渲染与编辑回填）。
        """
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        # 去掉模型可能包裹的 Markdown 代码围栏
        text = re.sub(r"^\s*```[a-zA-Z]*\s*$", "", text, flags=re.M)
        text = re.sub(r"^\s*```\s*$", "", text, flags=re.M)

        # ── 切掉片尾（导演阐述 / POST_CONSTRAINT 之前的内容才是镜头块）──
        tail_start = len(text)
        for marker in (r"^[ \t#>*]*导演阐述", r"^[ \t#>*]*POST_CONSTRAINT\s*[：:]"):
            m = re.search(marker, text, flags=re.M)
            if m:
                tail_start = min(tail_start, m.start())

        # ── 定位每个镜头块的起始 ──
        headers = list(
            re.finditer(r"^[ \t#>*]*镜头\s*(\d+)\s*[：:]\s*(.*)$", text, flags=re.M)
        )
        if not headers:
            logger.warning("导演模板：未找到任何「镜头NN：」标题")
            return []

        results: list[dict] = []
        for i, h in enumerate(headers):
            header_rest = h.group(2).strip().strip("*").strip()
            block_end = headers[i + 1].start() if i + 1 < len(headers) else tail_start
            results.append(
                TaskManager._parse_director_shot_block(
                    int(h.group(1)),
                    header_rest,
                    text[h.end():block_end],
                    # 原文块：从标题行起，保留「镜头NN：标题」与正文之间的原始连接符
                    text[h.start():block_end].strip(),
                )
            )

        logger.info(
            f"导演模板解析完成: {len(results)} 个镜头"
            f"（分秒画面 {sum(1 for r in results if '秒：' in r['visual_description'])} 镜含节拍）"
        )

        # 镜数由导演按主线节点自行判断，没有标准答案；这里只兜底明显退化的输出
        # （模型偷懒给 1-2 镜，或失控拆出几十镜），不构成对创作判断的干预。
        if len(results) < 3 or len(results) > 30:
            logger.warning(
                f"镜数 {len(results)} 明显偏离常规区间（3-30），疑似模型退化或失控，建议人工复核"
            )
        return results

    @staticmethod
    def _split_header(block_text: str) -> tuple[str, str, str]:
        """
        拆出块首的「镜头NN：标题（时长：N秒）」标题行。

        返回 (header_rest, body, raw_block)：header_rest 是标题行 `镜头NN：` 之后的部分，
        body 是去掉标题行后的正文，raw_block 是整块原文（保持原样）。

        与 _parse_director_storyboard 的切块方式一致（header 匹配后面直接接正文）。
        """
        normalized = block_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        m = re.match(r"^[ \t#>*]*镜头\s*\d+\s*[：:]\s*(.*)$", normalized, flags=re.M)
        if not m:
            # 用户把标题行删了：用首行当标题、其余当正文，尽量不丢内容
            parts = normalized.split("\n", 1)
            return parts[0].strip(), parts[1] if len(parts) > 1 else "", normalized
        return m.group(1).strip(), normalized[m.end():], normalized

    @staticmethod
    def reparse_storyboard(task_id: str, scene_number: int, block_text: str):
        """
        用编辑后的原文块重新解析并落库（结果页「保存」调用）。

        保存的是原文，景别/角度/运镜/情绪/构图/转场、image_prompt、visual_description
        等派生字段全部由同一套解析逻辑重算，保证与视频生成链路的口径一致。
        返回更新后的 Storyboard ORM 对象（已脱离 session），不存在则返回 None。
        """
        db = SessionLocal()
        try:
            storyboard = (
                db.query(Storyboard)
                .filter(
                    Storyboard.task_id == task_id,
                    Storyboard.scene_number == scene_number,
                )
                .first()
            )
            if not storyboard:
                return None

            header_rest, body, raw_block = TaskManager._split_header(block_text)
            parsed = TaskManager._parse_director_shot_block(
                scene_number, header_rest, body, raw_block
            )
            polish_storyboards([parsed])

            storyboard.scene_title = parsed["scene_title"]
            storyboard.duration_seconds = parsed["duration_seconds"]
            storyboard.raw_script = parsed["raw_script"]
            storyboard.character_core_prompt = parsed["character_core_prompt"]
            storyboard.description = parsed["description"]
            storyboard.shot_size = parsed["shot_size"]
            storyboard.camera_angle = parsed["camera_angle"]
            storyboard.camera_movement = parsed["camera_movement"]
            storyboard.mood = parsed["mood"]
            storyboard.composition = parsed["composition"]
            storyboard.transition = parsed["transition"]
            storyboard.quality_notes = parsed["quality_notes"]
            storyboard.subject = parsed["subject"]
            storyboard.environment = parsed["environment"]
            storyboard.location = parsed["location"]
            storyboard.dialogue_text = parsed["dialogue_text"]
            storyboard.visual_description = parsed["visual_description"]
            storyboard.image_prompt = parsed["image_prompt"]

            # 返回字段快照而非 ORM 对象：session 关闭后对象即 detached，
            # 未过期属性虽可读，但下游任何 refresh 都会炸，不如直接给纯数据。
            snapshot = {
                c.name: getattr(storyboard, c.name)
                for c in Storyboard.__table__.columns
            }
            db.commit()
            return snapshot
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def save_global_prefix(task_id: str, prefix: str) -> bool:
        """
        保存全局风格前缀（结果页可编辑；注入所有图片/视频 prompt 的首段）。

        返回是否命中任务。
        """
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return False
            task.global_prefix = prefix
            db.commit()
            return True
        finally:
            db.close()

    @staticmethod
    def _parse_characters(markdown_text: str) -> list[dict]:
        """
        解析 Claude 返回的人物角色 Markdown。

        期望格式：
            ## 角色名
            描述内容...

        注意：角色描述内部可能包含 ## 分组标题（如 ## 性格特征、## 外貌描述），
        这些应合并到上一个角色中，而非拆分为独立角色。

        返回: [{"name": "角色名", "description": "描述"}, ...]
        """
        pattern = r"##\s+(.+?)\n(.*?)(?=##\s+|\Z)"
        matches = re.findall(pattern, markdown_text, re.DOTALL)

        # 非人名的分组标题关键词
        SECTION_KEYWORDS = [
            "性格", "外貌", "背景", "弧线", "能力", "关系", "定位",
            "年龄", "身份", "技能", "武功", "武器", "功法", "羁绊",
            "特征", "描述", "故事", "经历", "成长", "转变", "结局",
            "心理", "情绪", "脾气", "发型", "身材", "衣着", "服饰",
            "标志", "细节", "面容", "门派", "种族", "性别",
        ]

        raw_entries = []
        for name, desc in matches:
            name = name.strip()
            desc = desc.strip()
            if not name or not desc:
                continue
            if "分镜" in name:
                continue
            raw_entries.append({"name": name[:100], "description": desc[:3000]})

        # 合并分组标题到上一个角色
        results = []
        for entry in raw_entries:
            is_section = any(kw in entry["name"] for kw in SECTION_KEYWORDS)
            if is_section and results:
                # 将分组内容追加到上一个角色
                prev = results[-1]
                prev["description"] += f"\n\n## {entry['name']}\n{entry['description']}"
            else:
                results.append(entry)

        if not results:
            results.append({
                "name": "未解析角色",
                "description": markdown_text.strip()[:2000],
            })

        return results

    @staticmethod
    def _parse_storyboards(raw_text: str) -> list[dict]:
        """
        解析 Claude 返回的 JSON 结构化分镜。

        期望格式（response_format=json_object）：
            {"storyboards": [{scene_number, scene_title, location, ...}, ...]}

        返回: [{"scene_number": N, "scene_title": "...", ...}, ...]
        """
        try:
            # 清理可能的 markdown code block
            text = raw_text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            data = json.loads(text)

            # 支持 {"storyboards": [...]} 或直接数组 [...]
            if isinstance(data, dict):
                scenes = data.get("storyboards", [data.get("scenes", [])])
                if not scenes and "scene_number" in str(list(data.keys())):
                    scenes = [data]  # 单个分镜
            elif isinstance(data, list):
                scenes = data
            else:
                raise ValueError(f"Unexpected JSON type: {type(data)}")

            results = []
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                scene_num = scene.get("scene_number", len(results) + 1)
                # 处理 characters_in_scene：如果是 list 则 join
                chars = scene.get("characters_in_scene", [])
                if isinstance(chars, list):
                    chars = "、".join(chars)

                results.append({
                    "scene_number": int(scene_num),
                    "scene_title": str(scene.get("scene_title", "")),
                    "location": str(scene.get("location", "")),
                    "time_of_day": str(scene.get("time_of_day", "")),
                    "characters_in_scene": chars,
                    "shot_type": str(scene.get("shot_type", "")),
                    "camera_movement": str(scene.get("camera_movement", "")),
                    "action_instruction": str(scene.get("action_instruction", "")),
                    "dialogue": str(scene.get("dialogue", "")),
                    "visual_description": str(scene.get("visual_description", "")),
                    "image_prompt": str(scene.get("image_prompt", "")),
                    "duration_seconds": float(scene.get("duration_seconds", 5.0)),
                    # 保留完整 JSON 副本
                    "description": json.dumps(scene, ensure_ascii=False),
                })

            logger.info(f"JSON 解析分镜成功: {len(results)} 个")
            return results

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"JSON 解析失败，回退 Markdown 解析: {e}")
            # 回退：Markdown 兜底解析
            return TaskManager._parse_storyboards_fallback(raw_text)

    @staticmethod
    def _parse_storyboards_fallback(markdown_text: str) -> list[dict]:
        """Markdown 兜底解析（JSON 解析失败时使用）"""
        results = []
        pattern = r"##\s*分镜\s*(\d+)[：:](.*?)\n(.*?)(?=##\s*分镜\s*\d+|\Z)"
        matches = re.findall(pattern, markdown_text, re.DOTALL)

        for num_str, title, desc in matches:
            try:
                scene_num = int(num_str.strip())
            except ValueError:
                scene_num = len(results) + 1
            full_desc = f"## 分镜{scene_num}：{title.strip()}\n{desc.strip()}"
            results.append({
                "scene_number": scene_num,
                "scene_title": title.strip(),
                "location": "",
                "time_of_day": "",
                "characters_in_scene": "",
                "camera_movement": "",
                "dialogue": "",
                "visual_description": desc.strip()[:2000],
                "image_prompt": "",
                "duration_seconds": 5.0,
                "description": full_desc.strip()[:3000],
            })

        if not results:
            sections = re.split(r"\n(?=##\s)", markdown_text)
            for i, section in enumerate(sections, 1):
                section = section.strip()
                if section:
                    results.append({
                        "scene_number": i,
                        "scene_title": section[:80],
                        "location": "",
                        "time_of_day": "",
                        "characters_in_scene": "",
                        "camera_movement": "",
                        "dialogue": "",
                        "visual_description": section[:2000],
                        "image_prompt": "",
                        "duration_seconds": 5.0,
                        "description": section[:2000],
                    })

        return results

    @staticmethod
    def _rescale_beat_spans(
        beats: list[tuple[str, str]], duration: float
    ) -> list[tuple[str, str]]:
        """
        把分秒画面行的时间轴归一化到镜头时长，返回新的 (时间戳, 画面) 列表。

        只作用于**派生字段**（image_prompt / visual_description）。原因：时长被收口
        （越界钳制，或将原文留白补齐）后，若送进 MiniMax-H3 的画面描述仍写「14-20秒」，
        模型收到的就是一份与成片时长自相矛盾的指令。脚本原文块（raw_script /
        description）不在此列——它必须保持模型的原样输出，供结果页直出与编辑回填。

        时间戳不是模板格式、或已经对齐时，原样返回**同一个 list 对象**，
        调用方可用 `is` 判断是否发生了归一。顺带把不连续的行拉平。
        """
        if not beats:
            return beats

        spans: list[tuple[int, int]] = []
        for ts, _ in beats:
            m = re.match(r"(\d+)-(\d+)秒", ts)
            if not m:
                return beats          # 时间戳不是模板格式，不猜
            spans.append((int(m.group(1)), int(m.group(2))))

        total = int(round(duration))
        contiguous = (
            spans[0][0] == 0
            and all(start == spans[i - 1][1] for i, (start, _) in enumerate(spans) if i)
            and spans[-1][1] == total
        )
        if contiguous:
            return beats              # 首行从 0 起、逐行相接、末行等于镜头时长

        # 按比例缩放各行的结束秒数，保证严格递增、末行正好等于镜头时长
        count = len(spans)
        scale = total / (spans[-1][1] or 1)
        scaled: list[int] = []
        for i, (_, end) in enumerate(spans):
            if i == count - 1:
                value = total
            else:
                floor = (scaled[-1] + 1) if scaled else 1
                # 前面不早于上一行，后面给剩余每行留出至少 1 秒
                value = max(floor, min(int(round(end * scale)), total - (count - 1 - i)))
            scaled.append(value)

        result: list[tuple[str, str]] = []
        prev = 0
        for (_, desc), end in zip(beats, scaled):
            result.append((f"{prev}-{end}秒", desc))
            prev = end
        return result

    @staticmethod
    def _check_director_shot_contract(
        scene_num: int,
        duration: float,
        beats: list[tuple[str, str]],
        kv: dict,
        note_lines: list[str],
        dialogue_lines: list[str],
        atmosphere: str,
        character_core_prompt: str = "",
    ) -> list[str]:
        """
        按 director-storyboard 模板契约做确定性体检，返回问题清单。

        这里是提示词里那些「硬约束」的可测版本——模型是否照做，靠日志说话，
        而不是靠人肉通读脚本。**只报告，不改写**：脚本原文是结果页直出与编辑回填的
        唯一载体，静默重写会让用户看到的和数据库里的不一致。
        契约定义见 app/services/director_storyboard_skill.py。
        """
        issues: list[str] = []

        # 1) 分秒画面行：3-4 行、秒数连续无重叠、末行结束秒数等于镜头时长
        if not beats:
            issues.append("缺少分秒画面行")
        else:
            if not 3 <= len(beats) <= 4:
                issues.append(f"分秒画面 {len(beats)} 行（模板要求 3-4 行）")
            spans = [
                (int(m.group(1)), int(m.group(2)))
                for ts, _ in beats
                if (m := re.match(r"(\d+)-(\d+)秒", ts))
            ]
            for (_, prev_end), (cur_start, _) in zip(spans, spans[1:]):
                if cur_start != prev_end:
                    issues.append(f"分秒画面区间不连续: 接不上 {prev_end}→{cur_start} 秒")
                    break
            if spans and abs(spans[-1][1] - duration) > 0.5:
                issues.append(
                    f"末行结束秒数 {spans[-1][1]} 与镜头时长 {duration:g} 秒不一致"
                )

        # 2) 机器参数六键齐全：缺项会让景别/角度/情绪等字段落空，只能吃兜底值
        missing = [
            field
            for field in ("shot_size", "camera_angle", "camera_movement",
                          "mood", "composition", "transition")
            if not kv.get(field)
        ]
        if missing:
            issues.append(f"机器参数缺失: {'/'.join(missing)}")

        # 3) 氛围段
        if not atmosphere:
            issues.append("缺少氛围段")

        # 4) 人物角色核心提示词行（缺失时视频 prompt 少一道服装锁，不影响可用性）
        if not character_core_prompt:
            issues.append("缺少「人物角色核心提示词：」行")

        # 5) 对白条数上限
        if len(dialogue_lines) > 7:
            issues.append(f"对白 {len(dialogue_lines)} 条（模板上限 7 条）")

        # 6) 衔接标注：首镜用【首镜头】，其余用【衔接提示→本镜】；末镜桥接一律不可缺
        note_text = "\n".join(note_lines)
        if scene_num == 1:
            if "【首镜头】" not in note_text:
                issues.append("缺少【首镜头】标注")
        elif "【衔接提示" not in note_text:
            issues.append("缺少【衔接提示→本镜】标注")
        if "【结尾桥接" not in note_text:
            issues.append("缺少【结尾桥接】标注")

        return issues

    @staticmethod
    def _parse_director_shot_block(
        scene_num: int, header_rest: str, block: str, raw_block: str
    ) -> dict:
        """
        解析单个导演镜头块 → 入库字段 dict。

        抽出来是为了让「首次生成」与「结果页编辑保存」共用同一套解析逻辑：
        用户在结果页改的是原文块，保存时用它重新解析出景别/角度/运镜/情绪/构图/转场，
        视频生成链路读到的字段与首次生成时口径完全一致。
        """
        block_lines = [ln.rstrip() for ln in block.split("\n")]

        # ── 标题 + 时长 ──
        scene_title = header_rest
        duration = 10.0
        dur_match = re.search(
            r"^(.*?)\s*[（(]\s*时长\s*[：:]\s*(\d+(?:\.\d+)?)\s*秒?\s*[)）]", header_rest
        )
        if dur_match:
            scene_title = dur_match.group(1).strip()
            duration = float(dur_match.group(2))
        else:
            scene_title = re.sub(r"[（(]时长[：:].*$", "", header_rest).strip()
        # 时长与下游视频生成的钳制口径对齐（见 director_storyboard_skill）。
        # 越界时成片只会按边界出片，分秒画面行末尾与【结尾桥接】会落空，
        # 因此在此收口；原文块本身不改写，仅在日志留痕供审计。
        raw_duration = duration
        duration = max(float(SCENE_DURATION_MIN), min(duration, float(SCENE_DURATION_MAX)))
        if abs(raw_duration - duration) > 1e-6:
            logger.warning(
                f"镜头 {scene_num} 时长 {raw_duration:g} 秒超出 "
                f"{SCENE_DURATION_MIN}-{SCENE_DURATION_MAX} 秒，已收口为 {duration:g} 秒"
            )

        # ── 先无条件摘出机器行：镜头参数 + 人物角色核心提示词 ──
        # 不依赖「摄影与视觉要求：」块头存在：模型一旦漏写块头，机器行会掉进
        # 分秒画面之后的文本里被当成台词吞掉，字段静默丢失。
        # 角色行同理：它位于标题行与氛围段之间，留在文本里会被当成氛围段的一部分，
        # 污染 atmosphere → environment/location/image_prompt 首段。
        kv_line = ""
        character_core_prompt = ""
        kept: list[str] = []
        for ln in block_lines:
            if not kv_line and re.match(r"^\s*镜头参数\s*[：:]", ln):
                kv_line = ln.strip().strip("*").strip()
                continue
            if not character_core_prompt and re.match(
                r"^\s*人物角色核心提示词\s*[：:]", ln
            ):
                # 只存标签后的内容：字段名本身就是这个标签，
                # 视频 prompt 注入时会重新拼「人物角色核心提示词：」前缀，
                # 把标签一并存下会拼出双前缀
                character_core_prompt = re.sub(
                    r"^\s*人物角色核心提示词\s*[：:]\s*", "", ln.strip().strip("*").strip()
                )
                continue
            kept.append(ln)
        block_lines = kept

        # ── 拆出「摄影与视觉要求」块与末尾标注块 ──
        photo_idx = None
        for idx, ln in enumerate(block_lines):
            if re.match(r"^\s*摄影与视觉要求", ln):
                photo_idx = idx
                break
        if photo_idx is None:
            pre_lines, photo_lines, note_lines = block_lines, [], []
        else:
            note_idx = len(block_lines)
            for idx in range(photo_idx + 1, len(block_lines)):
                if block_lines[idx].lstrip().startswith("【"):
                    note_idx = idx
                    break
            pre_lines = block_lines[:photo_idx]
            photo_lines = block_lines[photo_idx + 1:note_idx]
            note_lines = block_lines[note_idx:]

        # ── 分秒画面行 / 氛围段 / 对白行 ──
        beat_re = re.compile(r"^\s*(\d+)\s*[-–~—]\s*(\d+)\s*秒\s*[：:]\s*(.+)$")
        beats: list[tuple[str, str]] = []   # (时间戳, 画面描述)
        atmosphere_parts: list[str] = []
        dialogue_lines: list[str] = []
        seen_beat = False

        for ln in pre_lines:
            s = ln.strip().strip("*").strip()
            if not s:
                continue
            beat = beat_re.match(ln.strip())
            if beat:
                seen_beat = True
                beats.append((f"{beat.group(1)}-{beat.group(2)}秒", beat.group(3).strip()))
            elif seen_beat:
                dialogue_lines.append(s)
            else:
                atmosphere_parts.append(s)

        atmosphere = " ".join(atmosphere_parts).strip()

        # ── 摄影与视觉要求 6 字段 ──
        # `镜头：` 是「散文｜键=值…」两段式：竖线之前给人读，之后给程序读。
        # 必须在此切开——否则参数串会被当成散文喂进 image_prompt，
        # 而散文里若出现「构图：三分」这类写法又会反过来污染键值解析。
        photo: dict[str, str] = {}
        inline_kv = ""
        for ln in photo_lines:
            s = ln.strip().strip("*").strip()
            if not s:
                continue
            m = re.match(r"^(风格|画质|镜头|光影|动作|比例)\s*[：:]\s*(.*)$", s)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "镜头" and not inline_kv:
                prose, sep, tail = val.partition("｜")
                if not sep:
                    prose, sep, tail = val.partition("|")
                # 只在竖线后确实是参数串时才切，避免散文里偶发竖线造成误切
                if sep and re.search(r"景别\s*[=＝：:]", tail):
                    inline_kv = tail.strip()
                    val = prose.strip()
            photo[key] = val

        # 机器键值：优取独占一行的旧式「镜头参数：」行，其次取 `镜头：` 行的竖线后缀
        kv_value = (
            re.sub(r"^镜头参数\s*[：:]\s*", "", kv_line) if kv_line
            else (inline_kv or photo.get("镜头", ""))
        )
        kv = TaskManager._split_kv_line(kv_value)

        # 模板契约体检：模型没照做的项在日志里点名，便于回看与调参
        contract_issues = TaskManager._check_director_shot_contract(
            scene_num, duration, beats, kv, note_lines, dialogue_lines, atmosphere,
            character_core_prompt,
        )
        if contract_issues:
            logger.warning(
                f"镜头 {scene_num} 未满足模板契约: " + "；".join(contract_issues)
            )

        # 派生字段的时间轴按镜头时长归一；原文块仍是模型原样输出
        beats_for_prompt = TaskManager._rescale_beat_spans(beats, duration)
        if beats_for_prompt is not beats:
            logger.warning(
                f"镜头 {scene_num} 分秒画面时间轴已按镜头时长 {duration:g} 秒归一"
                f"（原文块保留模型原值，末行原为 {beats[-1][0]}）"
            )

        # ── 组装字段 ──
        beat_text = "\n".join(f"{ts}： {desc}" for ts, desc in beats_for_prompt)
        subject = " ".join(desc for _, desc in beats).strip() or atmosphere
        dialogue_text = "\n".join(dialogue_lines).strip() or "@无"

        visual_bits = [b for b in (atmosphere, beat_text) if b]
        photo_text = " ".join(
            f"{k}：{photo[k]}" for k in ("风格", "镜头", "光影", "动作") if photo.get(k)
        )
        if photo_text:
            visual_bits.append(photo_text)
        visual_description = "\n".join(visual_bits).strip()

        # image_prompt：供视频生成用的单段重组文本（片头风格由 global_prefix 单独注入）
        def _trim(s: str) -> str:
            return s.strip().rstrip("。，,.;；、 ")

        prompt_bits = [atmosphere] if atmosphere else []
        if beat_text:
            prompt_bits.append(beat_text)
        if photo.get("镜头"):
            prompt_bits.append(f"镜头：{photo['镜头']}")
        if photo.get("光影"):
            prompt_bits.append(f"光影：{photo['光影']}")
        if photo.get("动作"):
            prompt_bits.append(f"动作：{photo['动作']}")
        image_prompt = "，".join(_trim(b) for b in prompt_bits if _trim(b))[:4000]
        # description / raw_script：该镜的原文脚本块，结果页直接渲染、编辑直接回填。
        # 不做 Markdown 再加工——重新拼装会丢格式，且编辑保存后无法与原文对齐。
        return {
            "scene_number": scene_num,
            "scene_title": scene_title,
            "shot_size": kv.get("shot_size", ""),
            "camera_angle": kv.get("camera_angle", ""),
            "camera_movement": kv.get("camera_movement", ""),
            "subject": subject,
            "environment": atmosphere or subject[:200],
            "mood": kv.get("mood", ""),
            "composition": kv.get("composition", ""),
            "quality_notes": photo.get("画质", TaskManager.DEFAULT_QUALITY_NOTES),
            "transition": kv.get("transition", ""),
            "dialogue_text": dialogue_text,
            "duration_seconds": duration,
            "image_prompt": image_prompt,
            "description": raw_block,
            "raw_script": raw_block,
            "character_core_prompt": character_core_prompt,
            "global_prefix": "",
            "location": (atmosphere or "")[:80],
            "visual_description": visual_description,
}


    # ------------------------------------------------------------------
    # 阶段 4：分镜→视频（MiniMax-H3 文生视频）
    # ------------------------------------------------------------------

    async def _run_storyboard_to_video(
        self, task_id: str, scene_list: list[dict]
    ) -> list[str]:
        """阶段4：MiniMax-H3 文生视频，每分镜一键生成，含内置音频。"""
        total = len(scene_list)
        logger.info(f"[{task_id}] 阶段4: 分镜→视频 MiniMax-H3 ({total} 个分镜)")
        self._update_status(task_id, "running", progress=78)

        video_paths = []
        MAX_RETRIES = 1

        for i, scene in enumerate(scene_list):
            scene_num = scene["scene_number"]
            base_progress = 78 + int((i / max(total, 1)) * 15)
            self._update_status(task_id, "running", progress=base_progress)

            # 从资产拆解中查找匹配的角色/场景/道具图片，传给 MiniMax
            asset_ref = TaskManager._get_asset_reference_for_shot(task_id, scene)
            image_url = asset_ref["image_url"]
            logger.info(f"[{task_id}] 分镜{scene_num}/{total} MiniMax {'图生视频' if image_url else '文生视频'}中... (进度 {base_progress}%)")

            # 构建 prompt：全局前缀 + 模板 image_prompt + 资产参考（强约束）
            image_prompt = scene.get("image_prompt", "")
            visual = scene.get("visual_description", "") or scene.get("description", "")
            camera = scene.get("camera_movement", "")
            action = scene.get("action_instruction", "")
            dialogue = scene.get("dialogue", "")
            location = scene.get("location", "")
            scene_duration = int(scene.get("duration_seconds", 6) or 6)
            scene_duration = max(SCENE_DURATION_MIN, min(scene_duration, SCENE_DURATION_MAX))

            global_prefix = TaskManager._get_global_prefix(task_id)
            char_core = (scene.get("character_core_prompt") or "").strip()

            if image_prompt and image_prompt.strip():
                prompt_parts = []
                if global_prefix:
                    prompt_parts.append(global_prefix[:800])
                elif settings.image_style:
                    prompt_parts.append(f"Style: {settings.image_style}")
                # 角色核心提示词紧跟风格之后：它是本镜角色的身份与服装锁，
                # 越靠前越不容易被后面几百字的画面描述稀释
                if char_core:
                    prompt_parts.append(f"人物角色核心提示词：{char_core[:500]}")
                prompt_parts.append(image_prompt[:1500])
                if asset_ref["ref_text"]:
                    prompt_parts.append(f"Design reference: {asset_ref['ref_text']}")
                postfix = TaskManager._get_post_constraint(task_id)
                if postfix:
                    prompt_parts.append(postfix[:500])
                prompt = "，".join(prompt_parts)[:3000]
            else:
                sound_clause = ""
                if dialogue:
                    sound_clause = f"Sound: characters speaking naturally, ambient {location or 'scene'} atmosphere"
                prompt_parts = []
                if global_prefix:
                    prompt_parts.append(global_prefix[:800])
                elif settings.image_style:
                    prompt_parts.append(f"Style: {settings.image_style}")
                if char_core:
                    prompt_parts.append(f"人物角色核心提示词：{char_core[:500]}")
                if camera:
                    prompt_parts.append(f"Camera: {camera}")
                if action:
                    prompt_parts.append(f"Motion: {action}")
                if visual:
                    prompt_parts.append(visual[:1200])
                if sound_clause:
                    prompt_parts.append(sound_clause)
                postfix = TaskManager._get_post_constraint(task_id)
                if postfix:
                    prompt_parts.append(postfix[:500])
                prompt = ". ".join(prompt_parts)[:3000]

            for retry in range(MAX_RETRIES + 1):
                asset = self._create_media_asset(task_id, "video", scene_num, prompt)
                try:
                    path = await asyncio.wait_for(
                        minimax_service.generate_video(task_id, scene_num, prompt, image_url=image_url, duration=scene_duration),
                        timeout=1800,
                    )
                    video_paths.append(path)
                    self._update_media_asset(asset.id, "success", file_path=path)
                    done_progress = 78 + int(((i + 1) / max(total, 1)) * 15)
                    self._update_status(task_id, "running", progress=done_progress)
                    logger.info(f"[{task_id}] 分镜{scene_num} 视频生成成功 (进度 {done_progress}%)")
                    break
                except asyncio.TimeoutError:
                    logger.warning(f"[{task_id}] 分镜{scene_num} 视频生成超时"
                        + (f" (重试 {retry+1}/{MAX_RETRIES})" if retry < MAX_RETRIES else ""))
                    self._update_media_asset(asset.id, "failed", error="生成超时")
                except Exception as e:
                    logger.warning(f"[{task_id}] 分镜{scene_num} 视频生成失败: {e}"
                        + (f" (重试 {retry+1}/{MAX_RETRIES})" if retry < MAX_RETRIES else ""))
                    self._update_media_asset(asset.id, "failed", error=str(e)[:500])
                    if retry >= MAX_RETRIES:
                        break

            self._update_status(task_id, "running", progress=base_progress)

        self._update_status(task_id, "running", progress=95)
        logger.info(f"[{task_id}] 视频生成完成: {len(video_paths)}/{total} 成功")
        return video_paths

    async def _run_composite(
        self,
        task_id: str,
        video_paths: list[str],
        audio_paths: list[str] | None,
        scene_list: list[dict],
        character_list: list[dict],
    ) -> None:
        """
        阶段5：视频拼接（带转场）。
        MiniMax-H3 已含音频，无需额外配音轨。转场来自 scene_list.transition。
        注：字幕烧录仍由 video_composer.composite 提供，带转场路径暂不烧字幕。
        """
        if not video_paths:
            logger.warning(f"[{task_id}] 无可用视频，跳过拼接")
            return

        logger.info(f"[{task_id}] 阶段5: FFmpeg 视频拼接")
        self._update_status(task_id, "running", progress=97)

        asset = self._create_media_asset(
            task_id, "composite", None, "final composite (MiniMax-H3)"
        )

        try:
            # 从 scene_list 提取转场列表（按 scene_number 顺序，与 video_paths 对齐）
            transitions = [s.get("transition") or "硬切" for s in scene_list]
            output_path = await video_composer.composite_with_transitions(
                task_id, video_paths, transitions
            )
            self._update_media_asset(asset.id, "success", file_path=output_path)
        except Exception as e:
            logger.warning(f"[{task_id}] 视频拼接失败: {e}")
            self._update_media_asset(asset.id, "failed", error=str(e)[:500])

        self._update_status(task_id, "running", progress=99)

    # ------------------------------------------------------------------
    # 按分镜独立触发方法
    # ------------------------------------------------------------------

    async def generate_scene_video(self, task_id: str, scene: dict) -> dict:
        """为单个分镜生成视频（MiniMax-H3）"""
        scene_num = scene["scene_number"]
        visual = scene.get("visual_description", "") or scene.get("description", "")
        camera = scene.get("camera_movement", "")
        action = scene.get("action_instruction", "")
        dialogue = scene.get("dialogue", "")
        # 台词对白：优先新字段 dialogue_text，去掉 @无 前缀但保留其后的画外音
        dialogue_text = (scene.get("dialogue_text", "") or dialogue or "").strip()
        if dialogue_text.startswith("@无"):
            dialogue_text = dialogue_text[2:].strip()
        location = scene.get("location", "")
        scene_duration = int(scene.get("duration_seconds", 6) or 6)
        scene_duration = max(SCENE_DURATION_MIN, min(scene_duration, SCENE_DURATION_MAX))

        self._cleanup_asset(task_id, scene_num, "video")

        # 从资产拆解中查找匹配的角色/场景/道具图片
        asset_ref = TaskManager._get_asset_reference_for_shot(task_id, scene)
        image_url = asset_ref["image_url"]

        # 构造 prompt（全局前缀 + 角色核心提示词 + 模板 image_prompt + 资产参考，强约束）
        global_prefix = TaskManager._get_global_prefix(task_id)
        image_prompt = scene.get("image_prompt", "")
        char_core = (scene.get("character_core_prompt") or "").strip()

        if image_prompt and image_prompt.strip():
            parts = []
            if global_prefix:
                parts.append(global_prefix[:800])
            elif settings.image_style:
                parts.append(f"Style: {settings.image_style}")
            if char_core:
                parts.append(f"人物角色核心提示词：{char_core[:500]}")
            parts.append(image_prompt[:1500])
            if dialogue_text:
                parts.append(f"台词/画外音：{dialogue_text[:800]}")
            if asset_ref["ref_text"]:
                parts.append(f"Design reference: {asset_ref['ref_text']}")
            postfix = TaskManager._get_post_constraint(task_id)
            if postfix:
                parts.append(postfix[:500])
            prompt = "，".join(parts)[:3000]
        else:
            # 旧格式兜底
            sound = ""
            if dialogue_text:
                sound = f"台词/画外音：{dialogue_text[:800]}"
            parts = []
            if global_prefix:
                parts.append(global_prefix[:800])
            elif settings.image_style:
                parts.append(f"Style: {settings.image_style}")
            if char_core:
                parts.append(f"人物角色核心提示词：{char_core[:500]}")
            if camera:
                parts.append(f"Camera: {camera}")
            if action:
                parts.append(f"Motion: {action}")
            if visual:
                parts.append(visual[:1200])
            if sound:
                parts.append(sound)
            postfix = TaskManager._get_post_constraint(task_id)
            if postfix:
                parts.append(postfix[:500])
            prompt = ". ".join(parts)[:3000]

        asset = self._create_media_asset(task_id, "video", scene_num, prompt)
        try:
            if settings.video_provider == "comfyui":
                from app.services.comfyui_service import comfyui_service
                image_paths = self._get_reference_image_paths(task_id, scene)
                path = await asyncio.wait_for(
                    comfyui_service.generate_video(
                        task_id, scene_num, prompt,
                        image_paths=image_paths, duration=scene_duration,
                    ),
                    timeout=settings.comfyui_poll_max_retries * settings.comfyui_poll_interval + 600,
                )
            else:
                path = await asyncio.wait_for(
                    minimax_service.generate_video(task_id, scene_num, prompt, image_url=image_url, duration=scene_duration),
                    timeout=300,
                )
            oss_key = await self._upload_to_oss(path, task_id, current_round(task_id))
            self._update_media_asset(asset.id, "success", file_path=path, oss_key=oss_key)
            return {"status": "success", "file_path": path, "asset_id": asset.id}
        except asyncio.TimeoutError:
            self._update_media_asset(asset.id, "failed", error="视频生成超时（300s）")
            return {"status": "failed", "error": "视频生成超时，请重试"}
        except Exception as e:
            err = str(e)[:500]
            self._update_media_asset(asset.id, "failed", error=err)
            return {"status": "failed", "error": err}

    async def generate_director_image(
        self, task_id: str, scene: dict, style: str = "guoman3d"
    ) -> dict:
        """为单个分镜生成 6 宫格分镜导演图（GPT-Image-2）"""
        from app.services.gpt_image_service import gpt_image_service
        from app.services.prompt_builder import prompt_builder

        scene_num = scene["scene_number"]
        if style not in DIRECTOR_STYLE_EN:
            style = "guoman3d"
        style_en = DIRECTOR_STYLE_EN[style]

        self._cleanup_asset(task_id, scene_num, "director_image")

        # 先落 running 记录再翻译：翻译是 LLM 调用（实测 40-90s），放在后面会让这段窗口里
        # 刷新页面看不到「生成中」、也能重复点出两次并发生成
        asset = self._create_media_asset(task_id, "director_image", scene_num, None)

        # 中文输入：全局前缀 + 角色核心提示词 + 模板 image_prompt
        # （不拼台词——导演图是画面拆解，台词对拆分镜头没有帮助）
        parts = []
        global_prefix = TaskManager._get_global_prefix(task_id)
        if global_prefix:
            parts.append(global_prefix[:400])
        elif settings.image_style:
            parts.append(f"Style: {settings.image_style}")
        char_core = (scene.get("character_core_prompt") or "").strip()
        if char_core:
            parts.append(f"人物角色核心提示词：{char_core[:300]}")
        image_prompt = (scene.get("image_prompt") or "").strip()
        if image_prompt:
            parts.append(image_prompt[:800])
        else:
            fallback = (
                scene.get("visual_description") or scene.get("description") or ""
            ).strip()
            if fallback:
                parts.append(fallback[:800])
        if len(parts) <= 1:
            # 兜底：至少给出场景/时间，避免把空串丢给翻译
            title = (scene.get("scene_title") or "").strip()
            location = (scene.get("location") or "").strip()
            time_of_day = (scene.get("time_of_day") or "").strip()
            parts.append("".join([title, location, time_of_day]) or "影视分镜场景")

        try:
            translated = await prompt_builder.build_image_prompt("。".join(parts))

            # 版式约束与风格锁定语都拼在翻译之后：进翻译会被 prompt_builder 改写成场景描述
            prompt = (
                f"{style_en}. {translated}. "
                f"{DIRECTOR_BOARD_CONSTRAINT}. {DIRECTOR_STYLE_LOCK[style]}"
            )[:3000]
            self._update_media_asset(asset.id, "running", prompt=prompt)

            path, image_url = await asyncio.wait_for(
                gpt_image_service.generate_asset_image(
                    task_id, f"scene_{scene_num:02d}_director", prompt
                ),
                timeout=660,
            )
            # 不带轮次前缀：文件名确定性且不入归档轮，重新生成时同名覆盖即可
            oss_key = await self._upload_to_oss(path)
            self._update_media_asset(
                asset.id,
                "success",
                file_path=path,
                file_url=image_url,
                oss_key=oss_key,
            )
            return {"status": "success", "file_path": path, "asset_id": asset.id}
        except asyncio.TimeoutError:
            self._update_media_asset(asset.id, "failed", error="导演图生成超时（660s）")
            return {"status": "failed", "error": "导演图生成超时，请重试"}
        except Exception as e:
            err = str(e)[:500]
            self._update_media_asset(asset.id, "failed", error=err)
            return {"status": "failed", "error": err}

    # ------------------------------------------------------------------
    # 媒体链路辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_asset(task_id: str, scene_number: int | None, asset_type: str) -> int:
        """删除同分镜同类型的旧记录，防止标签堆积"""
        db = SessionLocal()
        try:
            filters = [
                MediaAsset.task_id == task_id,
                MediaAsset.asset_type == asset_type,
            ]
            if scene_number is not None:
                filters.append(MediaAsset.scene_number == scene_number)
            stale = db.query(MediaAsset).filter(*filters).all()
            count = len(stale)
            for a in stale:
                db.delete(a)
            db.commit()
            return count
        finally:
            db.close()

    @staticmethod
    def _count_assets(task_id: str) -> int:
        """任务已有资产数量（> 0 时链路跳过资产拆解，避免冲掉已生成的资产图）"""
        from app.models.asset import AssetItem

        db = SessionLocal()
        try:
            return db.query(AssetItem).filter(AssetItem.task_id == task_id).count()
        finally:
            db.close()

    @staticmethod
    def _load_character_prompts(task_id: str) -> str:
        """
        读取任务的角色资产并拼装「角色设定」文本（注入分镜 LLM）。

        失败不阻断链路：返回空串，模板规则退化为「从原文提炼」——
        角色设定是质量增强项，不该让分镜阶段整体失败。
        """
        from app.models.asset import AssetItem
        from app.services.consistency import build_character_core_prompts

        db = SessionLocal()
        try:
            # 拼装在 session 内完成：AssetItem 一旦 detach，未加载属性会炸
            assets = (
                db.query(AssetItem)
                .filter(
                    AssetItem.task_id == task_id,
                    AssetItem.category == "character",
                )
                .order_by(AssetItem.created_at)
                .all()
            )
            prompts = build_character_core_prompts(assets)
        except Exception as e:
            logger.warning(f"[{task_id}] ⚠️ 角色设定不可用（分镜将从原文提炼）: {e}")
            return ""
        finally:
            db.close()

        if prompts:
            logger.info(
                f"[{task_id}] 角色设定注入: {len(assets)} 个角色 / {len(prompts)} 字符"
            )
        return prompts

    @staticmethod
    def _at_mentions(text: str, names: list[str]) -> list[str]:
        """
        找出 text 中以 `@资产名` 形式被显式引用的资产名（按在文本中出现的先后返回）。

        `@` 本身就是用户写下的显式引用信号，因此只做「名字精确匹配」，不猜字符边界：
        中文名后面跟的往往是动词（「@林夕看向」）而不是标点，要求断句符会把正常引用判掉。
        唯一需要裁决的是同一位置的重叠——资产同时有「韩」与「韩萧」时 `@韩萧（…）`
        同时含两个名字，取最长的，「韩」只是「韩萧」的前缀而非另一次引用。

        匹配在去掉空白的文本上做：`@韩 萧` 也算引用，且不会因换行/空格错过命中。
        返回的索引仅用于排序与去重，去空白后相对顺序不变。
        """
        if not text:
            return []

        normalized = re.sub(r"\s+", "", text)
        hits: dict[int, str] = {}

        for name in {n.strip() for n in names if n and n.strip()}:
            idx = normalized.find("@" + re.sub(r"\s+", "", name))
            if idx < 0:
                continue
            if idx in hits and len(hits[idx]) >= len(name):
                continue
            hits[idx] = name

        return [hits[p] for p in sorted(hits)]

    @staticmethod
    def _get_reference_image_paths(task_id: str, shot: dict) -> list[str]:
        """
        收集 ComfyUI 图生视频的多张参考图本地路径。
        顺序：角色图 → 场景图（只保留存在的文件，去重）。

        @ 引用优先：命中时按 @ 出现顺序收全部被引用资产（角色在前），
        不再做模糊子串猜测；无 @ 时保持原有「第一角色图 + 第一场景图」。
        """
        import os

        from app.models.asset import AssetItem

        def _name_matches(asset_name: str, text: str) -> bool:
            name = asset_name.strip()
            if not name:
                return False
            if name in text:
                return True
            for length in range(len(name), 1, -1):
                for i in range(len(name) - length + 1):
                    token = name[i:i + length]
                    if len(token) >= 2 and token in text:
                        return True
            return False

        paths: list[str] = []
        seen: set[str] = set()

        # 资产图（角色 + 场景）
        subject = shot.get("subject", "") or ""
        environment = shot.get("environment", "") or ""
        combined_text = f"{subject} {environment}"
        raw_text = shot.get("raw_script", "") or ""

        db = SessionLocal()
        try:
            assets = (
                db.query(AssetItem)
                .filter(
                    AssetItem.task_id == task_id,
                    AssetItem.image_status == "success",
                )
                .all()
            )

            mentions = TaskManager._at_mentions(raw_text, [a.name or "" for a in assets])
            if mentions:
                by_name = {(a.name or "").strip(): a for a in assets}
                picked = [by_name[n] for n in mentions if n in by_name]
                ordered = [a for a in picked if a.category == "character"] + [
                    a for a in picked if a.category != "character"
                ]
                for a in ordered:
                    if not a.image_path:
                        continue
                    ap = os.path.abspath(a.image_path)
                    if ap in seen or not os.path.isfile(a.image_path):
                        continue
                    paths.append(a.image_path)
                    seen.add(ap)
            else:
                char_path = None
                scene_path = None
                for a in assets:
                    if not a.image_path or not _name_matches(a.name or "", combined_text):
                        continue
                    ap = os.path.abspath(a.image_path)
                    if ap in seen:
                        continue
                    if a.category == "character" and char_path is None:
                        char_path = a.image_path
                    elif a.category == "scene" and scene_path is None:
                        scene_path = a.image_path

                for p in (char_path, scene_path):
                    if p and os.path.isfile(p):
                        paths.append(p)
                        seen.add(os.path.abspath(p))
        finally:
            db.close()

        # ComfyUI 参考图节点数有上限，超出的按顺序丢弃
        from app.services.comfyui_service import MAX_REF_IMAGES

        if len(paths) > MAX_REF_IMAGES:
            logger.warning(
                f"[{task_id}] 参考图 {len(paths)} 张超过上限 {MAX_REF_IMAGES}，已按顺序截断"
            )
            paths = paths[:MAX_REF_IMAGES]

        return paths

    @staticmethod
    def _get_asset_reference_for_shot(task_id: str, shot: dict) -> dict:
        """
        为单个分镜查找资产拆解中的参考图片和描述。
        使用分词匹配（2字及以上词组），优先角色图片。
        返回 {"image_url": str|None, "ref_text": str}

        @ 引用优先（@ 行见「人物角色核心提示词：」）：命中时只认被显式引用的资产，
        不做模糊猜测——猜错会挂上别的角色的脸，比不出图更糟；被引用资产尚未出图时
        该镜退化为文生视频，但仍把它的描述带进 ref_text。
        无 @ 时完全保持原分词匹配逻辑。
        """
        from app.models.asset import AssetItem

        subject = shot.get("subject", "") or ""
        environment = shot.get("environment", "") or ""
        combined_text = f"{subject} {environment}"
        raw_text = shot.get("raw_script", "") or ""

        def _name_matches(asset_name: str, text: str) -> bool:
            """分词匹配：提取资产名中2字及以上词组，任一词组在文本中出现则匹配"""
            name = asset_name.strip()
            if not name:
                return False
            # 精确匹配优先
            if name in text:
                return True
            # 分词：取所有2字及以上连续子串
            for length in range(len(name), 1, -1):
                for i in range(len(name) - length + 1):
                    token = name[i:i+length]
                    if len(token) >= 2 and token in text:
                        return True
            return False

        db = SessionLocal()
        try:
            assets = db.query(AssetItem).filter(
                AssetItem.task_id == task_id,
                AssetItem.image_status == "success",
            ).all()

            mentions = TaskManager._at_mentions(raw_text, [a.name or "" for a in assets])
            if mentions:
                by_name = {(a.name or "").strip(): a for a in assets}
                picked = [by_name[n] for n in mentions if n in by_name]
                logger.info(f"[{task_id}] 镜头 {shot.get('scene_number')} @引用: {'、'.join(mentions)}")
                char_url = next(
                    (a.image_url for a in picked
                     if a.category == "character" and a.image_url), None
                )
                any_url = next((a.image_url for a in picked if a.image_url), None)
                ref_parts = [
                    f"{a.name}: {(a.image_prompt or a.description or '')[:100]}"
                    for a in picked if a.image_prompt or a.description
                ]
                return {
                    "image_url": char_url or any_url,
                    "ref_text": " | ".join(ref_parts[:8])[:1000],
                }

            image_url = None
            char_url = None  # 角色图片单独记录
            ref_parts = []

            for a in assets:
                name = a.name or ""
                if not name or not _name_matches(name, combined_text):
                    continue

                if a.image_url:
                    if not image_url:
                        image_url = a.image_url  # 第一个匹配图
                    if a.category == "character" and not char_url:
                        char_url = a.image_url   # 第一个角色图

                if a.image_prompt:
                    ref_parts.append(f"{a.name}: {a.image_prompt[:100]}")
                elif a.description:
                    ref_parts.append(f"{a.name}: {a.description[:100]}")

            # 优先用角色图，兜底用第一个匹配图
            image_url = char_url or image_url

            ref_text = " | ".join(ref_parts[:8])[:1000] if ref_parts else ""
            return {"image_url": image_url, "ref_text": ref_text}
        finally:
            db.close()

    @staticmethod
    def _create_media_asset(
        task_id: str,
        asset_type: str,
        scene_number: int | None,
        prompt: str | None,
        character_name: str | None = None,
    ) -> MediaAsset:
        """创建 media_asset 记录"""
        db = SessionLocal()
        try:
            asset = MediaAsset(
                task_id=task_id,
                asset_type=asset_type,
                scene_number=scene_number,
                prompt=prompt,
                character_name=character_name,
                status="running",
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)
            event_bus.publish(task_id, "media", {
                "asset_id": asset.id,
                "task_id": task_id,
                "asset_type": asset_type,
                "scene_number": scene_number,
                "status": "running",
                "error_message": None,
            })
            return asset
        finally:
            db.close()

    @staticmethod
    async def _upload_to_oss(
        local_path: str | None,
        task_id: str | None = None,
        round_no: int | None = None,
    ) -> str | None:
        """
        上传本地文件到 OSS，失败返回 None（降级为本地 /media）。

        传入 task_id / round_no 时使用带轮次前缀的 key，避免新产物覆盖归档轮的同名对象
        （视频文件名是确定性的 scene_NNN.mp4）。
        """
        if not local_path:
            return None
        try:
            from app.services.storage import storage
            key = round_oss_key(local_path, task_id, round_no) if task_id and round_no else None
            return await asyncio.to_thread(storage.upload, local_path, key)
        except Exception as e:
            logger.warning(f"OSS 上传失败（忽略）: {local_path} -> {e}")
            return None

    @staticmethod
    def _update_media_asset(
        asset_id: str,
        status: str,
        file_path: str | None = None,
        file_url: str | None = None,
        oss_key: str | None = None,
        error: str | None = None,
        prompt: str | None = None,
    ) -> None:
        """更新 media_asset 状态"""
        db = SessionLocal()
        try:
            asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
            if asset:
                asset.status = status
                if file_path:
                    asset.file_path = file_path
                if file_url:
                    asset.file_url = file_url
                if oss_key:
                    asset.oss_key = oss_key
                if error:
                    asset.error_message = error
                if prompt:
                    asset.prompt = prompt
                db.commit()

                url = None
                if asset.oss_key:
                    from app.services.storage import storage
                    url = storage.get_signed_url(asset.oss_key)
                event_bus.publish(asset.task_id, "media", {
                    "asset_id": asset.id,
                    "task_id": asset.task_id,
                    "asset_type": asset.asset_type,
                    "scene_number": asset.scene_number,
                    "status": asset.status,
                    "error_message": asset.error_message,
                    "file_path": asset.file_path,
                    "url": url,
                })
        finally:
            db.close()

    @staticmethod
    def _extract_dialogue_sample(
        character_name: str, description: str
    ) -> str:
        """从角色描述中提取示例台词（优先从结构化 dialogue 提取）"""
        # 先尝试匹配引号内的对话
        pattern = r'["""''「](.+?)["“”''」]'
        matches = re.findall(pattern, description)
        if matches:
            return matches[0][:200]

        # 兜底：生成简单自我介绍
        return f"我是{character_name}，这是我的故事。"

    @staticmethod
    def _build_subtitles(
        scene_list: list[dict],
        character_list: list[dict],
        video_count: int,
    ) -> list[dict]:
        """根据结构化分镜生成 SRT 字幕数据"""
        subtitles = []
        char_names = [c["name"] for c in character_list]

        cumulative_time = 0.0

        for i, scene in enumerate(scene_list[:video_count]):
            scene_num = scene["scene_number"]
            scene_title = scene.get("scene_title", f"第{scene_num}幕")
            dialogue_text = scene.get("dialogue", "")
            duration = float(scene.get("duration_seconds", 5.0))
            start_time = cumulative_time
            end_time = start_time + duration

            # 场景标题字幕
            subtitles.append({
                "scene": scene_num,
                "text": f"【{scene_title}】",
                "start": start_time,
                "end": start_time + 2,
            })

            # 结构化对话字幕：按换行拆分
            if dialogue_text:
                lines = dialogue_text.strip().split("\n")
                for j, line in enumerate(lines[:5]):
                    line = line.strip()
                    if not line:
                        continue
                    # 如果已有角色前缀则保留，否则尝试匹配角色
                    if "：" not in line and ":" not in line:
                        speaker = char_names[j % len(char_names)] if char_names else ""
                        line = f"{speaker}：{line}" if speaker else line
                    sub_start = start_time + 2 + j * 2
                    sub_end = min(sub_start + 2, end_time - 0.5)
                    if sub_start < end_time:
                        subtitles.append({
                            "scene": scene_num,
                            "text": line[:120],
                            "start": sub_start,
                            "end": sub_end,
                        })

            cumulative_time += duration

        return subtitles

    # ------------------------------------------------------------------
    # 数据库操作
    # ------------------------------------------------------------------

    @staticmethod
    def _update_status(
        task_id: str,
        status: str,
        progress: int | None = None,
        error: str | None = None,
    ) -> None:
        """更新任务状态、进度、错误信息"""
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = status
                if progress is not None:
                    task.progress = progress
                if error is not None:
                    task.error_message = error
                db.commit()
                event_bus.publish(task_id, "task", {
                    "task_id": task_id,
                    "status": status,
                    "progress": task.progress,
                    "error_message": task.error_message,
                })
        finally:
            db.close()

    @staticmethod
    def fail_stale_running_tasks() -> int:
        """
        把 DB 中残留的 pending / running 任务标记为 failed，返回处理条数。

        队列是内存队列（不持久化），服务重启时 task_queue.stop() 只等 worker 30 秒，
        超时直接 cancel()，在跑的任务会被强杀 —— 而没有任何代码把 DB 状态改回来。
        后果是这些任务永远停在原进度，前端看起来就是「卡在 40%」，
        而且因为 status=running 连删除接口都会拒绝（409）。

        在服务启动时统一对账：此刻还没有请求进来，凡是 pending/running 的都是孤儿。
        """
        db = SessionLocal()
        try:
            rows = db.query(Task).filter(Task.status.in_(("pending", "running"))).all()
            for task in rows:
                task.status = "failed"
                task.error_message = "服务重启导致任务中断，请重新生成"
            if rows:
                db.commit()
            return len(rows)
        finally:
            db.close()

    @staticmethod
    def _save_outline(task_id: str, content: str) -> None:
        db = SessionLocal()
        try:
            outline = Outline(task_id=task_id, content=content)
            db.add(outline)
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _save_characters(task_id: str, character_list: list[dict]) -> None:
        db = SessionLocal()
        try:
            for char_data in character_list:
                character = Character(
                    task_id=task_id,
                    name=char_data["name"],
                    description=char_data["description"],
                )
                db.add(character)
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _save_storyboards(task_id: str, scene_list: list[dict]) -> None:
        """逐条存入分镜，优先使用模板字段，兼容旧 JSON 字段"""
        db = SessionLocal()
        try:
            for scene_data in scene_list:
                scene_num = scene_data["scene_number"]

                # 模板字段（新）
                shot_size = scene_data.get("shot_size", "")
                camera_angle = scene_data.get("camera_angle", "")
                subject = scene_data.get("subject", "")
                environment = scene_data.get("environment", "")
                mood = scene_data.get("mood", "")
                composition = scene_data.get("composition", "")
                quality_notes = scene_data.get("quality_notes", "")

                # 旧字段兼容
                title = scene_data.get("scene_title", f"镜头{scene_num}")
                location = scene_data.get("location", "") or environment[:200]
                time_of_day = scene_data.get("time_of_day", "")
                chars = scene_data.get("characters_in_scene", "")
                camera_movement = scene_data.get("camera_movement", "")
                dialogue = scene_data.get("dialogue", "")
                visual = scene_data.get("visual_description", "")
                image_prompt = scene_data.get("image_prompt", "")
                duration = scene_data.get("duration_seconds", 6.0)

                # 人类可读描述
                if scene_data.get("description"):
                    desc = scene_data["description"]
                else:
                    desc = image_prompt or json.dumps(scene_data, ensure_ascii=False)

                storyboard = Storyboard(
                    task_id=task_id,
                    scene_number=scene_num,
                    scene_title=title,
                    location=location,
                    time_of_day=time_of_day,
                    characters_in_scene=chars,
                    camera_movement=camera_movement,
                    dialogue=dialogue,
                    visual_description=visual,
                    image_prompt=image_prompt,
                    duration_seconds=duration,
                    description=desc,
                    # 模板新字段
                    shot_size=shot_size,
                    camera_angle=camera_angle,
                    subject=subject,
                    environment=environment,
                    mood=mood,
                    composition=composition,
                    quality_notes=quality_notes,
                    transition=scene_data.get("transition", ""),
                    dialogue_text=scene_data.get("dialogue_text", ""),
                    raw_script=scene_data.get("raw_script", ""),
                    character_core_prompt=scene_data.get("character_core_prompt", ""),
                )
                db.add(storyboard)
            db.commit()
        finally:
            db.close()


# 全局单例
task_manager = TaskManager()
