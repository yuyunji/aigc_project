"""
级联任务编排器
完整 8 阶段链路：
  原著文本 → 分片预处理 → 剧本大纲 → 人物角色设定 → 分镜脚本
  → 分镜图片生成 → 图生视频 → 角色配音 → 字幕合成

阶段 1-4 为文本链路，阶段 5-8 为媒体链路。
媒体链路在文本链完成后自动触发（可在配置中关闭）。
"""
import asyncio
import json
import logging
import re

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
from app.services.gpt_image_service import gpt_image_service
from app.services.video_composer import video_composer
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

# 是否自动执行媒体链路（由 settings.auto_media_pipeline 控制）


class TaskManager:
    """
    级联任务调度器。

    链路四阶段：
    1. 文本预处理（分片）
    2. 生成大纲 → 存入 outlines 表
    3. 生成人物 → 解析后逐条存入 characters 表
    4. 生成分镜 → 解析后逐条存入 storyboards 表

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

        # ── 阶段 2：AI 分镜师分镜拆解（单次 LLM 调用，镜数由 AI 判断）──
        logger.info(f"[{task_id}] 阶段2: AI分镜师分镜拆解")
        self._update_status(task_id, "running", progress=25)

        # 阶段超时 = 单次 LLM 超时 × (1 + 重试次数) + 缓冲，保证内部重试有机会跑完
        stage_timeout = settings.llm_call_timeout * (settings.llm_max_retries + 1) + 60

        storyboard_text = await asyncio.wait_for(
            llm_service.generate_storyboard_single(chunks),
            timeout=stage_timeout,
        )

        if not storyboard_text or not storyboard_text.strip():
            raise LLMAPIError("分镜提示词生成结果为空，请重试")

        logger.info(
            f"[{task_id}] LLM 返回 {len(storyboard_text)} 字符: "
            f"{storyboard_text[:200].replace(chr(10), '↵')}..."
        )

        # 提取 TOTAL_SHOTS
        declared_total = self._extract_total_shots(storyboard_text)
        if declared_total:
            logger.info(f"[{task_id}] LLM 声明总镜数: {declared_total}")
        else:
            logger.warning(f"[{task_id}] 未提取到 TOTAL_SHOTS 声明")

        # 提取 GLOBAL_PREFIX
        global_prefix = self._extract_global_prefix(storyboard_text)
        if global_prefix:
            self._save_global_prefix(task_id, global_prefix)
            logger.info(f"[{task_id}] 全局前缀: {global_prefix[:80]}...")
        else:
            logger.warning(f"[{task_id}] 未提取到全局前缀")

        # 提取 POST_CONSTRAINT
        post_constraint = self._extract_post_constraint(storyboard_text)
        if post_constraint:
            self._save_post_constraint(task_id, post_constraint)
            logger.info(f"[{task_id}] 后置约束: {post_constraint[:80]}...")
        else:
            logger.warning(f"[{task_id}] 未提取到后置约束")

        # 解析分镜
        scene_list = self._parse_template_storyboard(storyboard_text)
        if not scene_list:
            raise LLMAPIError("分镜提示词解析失败")

        logger.info(f"[{task_id}] 分镜解析完成: {len(scene_list)} 个镜头")

        # TOTAL_SHOTS 与实际解析数一致性检查
        if declared_total and declared_total != len(scene_list):
            logger.warning(
                f"[{task_id}] ⚠️ LLM 声明 {declared_total} 镜，实际解析 {len(scene_list)} 镜，"
                "不一致！可能 LLM 输出格式有误"
            )

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

        # ── 阶段 3-4：媒体链路（文生视频/图生视频 + FFmpeg 拼接）──
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
    # TOTAL_SHOTS / 全局前缀 / 后置约束 管理
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_total_shots(raw_text: str) -> int | None:
        """从 LLM 输出中提取 TOTAL_SHOTS 声明值"""
        match = re.search(r"TOTAL_SHOTS[：:]\s*(\d+)", raw_text)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _extract_global_prefix(raw_text: str) -> str:
        """从 LLM 输出中提取 GLOBAL_PREFIX 行"""
        match = re.search(r"GLOBAL_PREFIX[：:]\s*(.+)", raw_text)
        if match:
            return match.group(1).strip()
        # 兜底：尝试查找第一行是否为风格描述
        first_line = raw_text.strip().split("\n")[0].strip()
        if first_line.startswith("日式") or "动漫" in first_line:
            return first_line
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
    def _extract_post_constraint(raw_text: str) -> str:
        """从 LLM 输出中提取 POST_CONSTRAINT 行"""
        match = re.search(r"POST_CONSTRAINT[：:]\s*(.+)", raw_text)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _save_post_constraint(task_id: str, constraint: str) -> None:
        """将后置约束保存到 tasks 表"""
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.post_constraint = constraint
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
        """从 tasks 表读取后置约束"""
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            return (task.post_constraint or "") if task else ""
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 模板格式解析（字段名分隔方式，兼容任意镜数）
    # ------------------------------------------------------------------

    # 画质补充默认值（LLM 未输出时使用）
    DEFAULT_QUALITY_NOTES = "金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染"

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
                    scene_duration = max(4.0, min(float(dur_match.group(1)), 15.0))

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

    # ------------------------------------------------------------------
    # 阶段 5：分镜→视频（MiniMax-H3 文生视频）
    # ------------------------------------------------------------------

    async def _run_storyboard_to_video(
        self, task_id: str, scene_list: list[dict]
    ) -> list[str]:
        """阶段5：MiniMax-H3 文生视频，每分镜一键生成，含内置音频。"""
        total = len(scene_list)
        logger.info(f"[{task_id}] 阶段5: 分镜→视频 MiniMax-H3 ({total} 个分镜)")
        self._update_status(task_id, "running", progress=78)

        video_paths = []
        MAX_RETRIES = 1

        for i, scene in enumerate(scene_list):
            scene_num = scene["scene_number"]
            base_progress = 78 + int((i / max(total, 1)) * 15)
            self._update_status(task_id, "running", progress=base_progress)

            # 查找已有分镜图的远程 URL，传给 MiniMax
            image_url = self._get_latest_image_url(task_id, scene_num)

            # 从资产拆解中查找匹配的角色/场景/道具图片
            asset_ref = TaskManager._get_asset_reference_for_shot(task_id, scene)
            if not image_url and asset_ref["image_url"]:
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
            scene_duration = max(4, min(scene_duration, 15))

            global_prefix = TaskManager._get_global_prefix(task_id)

            if image_prompt and image_prompt.strip():
                prompt_parts = []
                if global_prefix:
                    prompt_parts.append(global_prefix[:800])
                elif settings.image_style:
                    prompt_parts.append(f"Style: {settings.image_style}")
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
        阶段6：视频拼接 + SRT 字幕 (FFmpeg)。
        MiniMax-H3 已含音频，无需额外配音轨。
        """
        if not video_paths:
            logger.warning(f"[{task_id}] 无可用视频，跳过拼接")
            return

        logger.info(f"[{task_id}] 阶段6: FFmpeg 视频拼接")
        self._update_status(task_id, "running", progress=97)

        subtitle_data = self._build_subtitles(
            scene_list, character_list, len(video_paths)
        )

        asset = self._create_media_asset(
            task_id, "composite", None, "final composite (MiniMax-H3)"
        )

        try:
            output_path = await video_composer.composite(
                task_id, video_paths, audio_paths or [], subtitle_data
            )
            self._update_media_asset(asset.id, "success", file_path=output_path)
        except Exception as e:
            logger.warning(f"[{task_id}] 视频拼接失败: {e}")
            self._update_media_asset(asset.id, "failed", error=str(e)[:500])

        self._update_status(task_id, "running", progress=99)

    # ------------------------------------------------------------------
    # 按分镜独立触发方法
    # ------------------------------------------------------------------

    async def generate_scene_image(self, task_id: str, scene: dict) -> dict:
        """为单个分镜生成图片，支持 MiniMax image-01 / GPT-Image-2 切换"""
        scene_num = scene["scene_number"]
        visual = scene.get("visual_description", "") or scene.get("description", "")
        prebuilt = scene.get("image_prompt", "")

        if prebuilt and prebuilt.strip():
            prompt = prebuilt
        elif visual and visual.strip():
            prompt = visual[:500]
        else:
            prompt = "cinematic scene, dramatic lighting, 4K, high quality"

        # ── 提取出场角色的外貌特征（GPT-Image-2 / MiniMax 共用）──
        char_appearance = self._get_characters_appearance(
            task_id, scene.get("characters_in_scene", "")
        )
        char_names = self._parse_char_names(scene.get("characters_in_scene", ""))

        self._cleanup_asset(task_id, scene_num, "image")
        asset = self._create_media_asset(task_id, "image", scene_num, prompt)

        provider = settings.image_provider

        # ── 强制注入全局前缀 ──
        global_prefix = TaskManager._get_global_prefix(task_id)

        try:
            if provider == "gpt-image-2":
                # GPT-Image-2: 一致性上下文（角色圣经 + 场景锚定，英文直拼不压缩）+ 中文主体翻译
                char_bible = self._get_character_bible(task_id, scene.get("subject", ""))
                scene_ref = self._get_scene_reference(
                    task_id, f"{scene.get('location', '')} {scene.get('environment', '')}"
                )
                prev_shot = self._get_prev_shot_context(task_id, scene_num)

                prompt_parts = []
                if global_prefix:
                    prompt_parts.append(
                        f"REQUIRED STYLE: 2D Japanese anime, cel-shaded, hand-drawn look. "
                        f"Style guide: {global_prefix[:500]}"
                    )
                else:
                    prompt_parts.append(
                        "REQUIRED STYLE: 2D anime, Japanese animation, cel-shaded, "
                        "flat colors, hand-drawn look, NO 3D rendering, NO photorealism."
                    )
                if settings.image_style:
                    prompt_parts.append(f"Style detail: {settings.image_style}")
                if prev_shot:
                    prompt_parts.append(
                        f"PREVIOUS SHOT (continue seamlessly from its ending): {prev_shot}"
                    )
                # 兜底：无资产三视图时用文字外貌描述
                if char_appearance and not char_bible:
                    prompt_parts.append(
                        f"Character appearances (MUST keep consistent across all scenes):\n{char_appearance}"
                    )
                prompt_parts.append(f"Scene description: {prompt}")
                full_prompt = "\n\n".join(prompt_parts)

                from app.services.prompt_builder import prompt_builder
                try:
                    english_prompt = await prompt_builder.build_image_prompt(full_prompt)
                except Exception:
                    english_prompt = full_prompt

                # 一致性上下文（英文，绕过 200 词压缩直接拼在前面）
                consistency = []
                if char_bible:
                    consistency.append(
                        f"CHARACTER BIBLE (identical in every shot, never alter appearance):\n{char_bible}"
                    )
                if scene_ref:
                    consistency.append(
                        f"SCENE REFERENCE (keep environment/lighting consistent): {scene_ref}"
                    )
                if consistency:
                    english_prompt = "\n\n".join(consistency) + "\n\n" + english_prompt

                path, remote_url = await asyncio.wait_for(
                    gpt_image_service.generate_scene_image(task_id, scene_num, english_prompt),
                    timeout=600,
                )
            else:
                remote_url = None
                # MiniMax image-01（默认）: 全局前缀 + subject_reference 图像锚定方案
                if global_prefix:
                    prompt = f"{global_prefix}. {prompt}"
                elif settings.image_style:
                    prompt = f"{settings.image_style}. {prompt}"
                ref_image_url = None
                if char_names and char_appearance:
                    ref_image_url = await self._ensure_character_ref(
                        task_id, char_names[0], char_appearance
                    )
                if ref_image_url:
                    prompt = (
                        f"{prompt}. "
                        f"Keep the character ({char_names[0]}) appearance consistent with the reference image"
                    )
                from app.services.minimax_image_service import minimax_image_service
                path = await asyncio.wait_for(
                    minimax_image_service.generate_image(task_id, scene_num, prompt, ref_image_url),
                    timeout=180,
                )

            oss_key = await self._upload_to_oss(path)
            self._update_media_asset(asset.id, "success", file_path=path, file_url=remote_url, oss_key=oss_key)
            return {"status": "success", "file_path": path, "asset_id": asset.id}
        except asyncio.TimeoutError:
            self._update_media_asset(asset.id, "failed", error="图片生成超时（600s），请稍后重试")
            return {"status": "failed", "error": "图片生成超时，请稍后重试"}
        except Exception as e:
            err = str(e)[:500]
            self._update_media_asset(asset.id, "failed", error=err)
            return {"status": "failed", "error": err}

    async def generate_scene_video(self, task_id: str, scene: dict) -> dict:
        """为单个分镜生成视频（MiniMax-H3，优先使用已生成的分镜图作参考）"""
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
        scene_duration = max(4, min(scene_duration, 15))

        self._cleanup_asset(task_id, scene_num, "video")

        # 查找已有分镜图的远程 URL，同时查资产拆解图片
        image_url = self._get_latest_image_url(task_id, scene_num)
        asset_ref = TaskManager._get_asset_reference_for_shot(task_id, scene)
        if not image_url and asset_ref["image_url"]:
            image_url = asset_ref["image_url"]

        # 构造 prompt（全局前缀 + 模板 image_prompt + 资产参考，强约束）
        global_prefix = TaskManager._get_global_prefix(task_id)
        image_prompt = scene.get("image_prompt", "")

        if image_prompt and image_prompt.strip():
            parts = []
            if global_prefix:
                parts.append(global_prefix[:800])
            elif settings.image_style:
                parts.append(f"Style: {settings.image_style}")
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
                image_paths = self._get_reference_image_paths(task_id, scene, scene_num)
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
            oss_key = await self._upload_to_oss(path)
            self._update_media_asset(asset.id, "success", file_path=path, oss_key=oss_key)
            return {"status": "success", "file_path": path, "asset_id": asset.id}
        except asyncio.TimeoutError:
            self._update_media_asset(asset.id, "failed", error="视频生成超时（300s）")
            return {"status": "failed", "error": "视频生成超时，请重试"}
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
    def _get_characters_appearance(task_id: str, characters_in_scene: str) -> str:
        """从角色库中提取出场角色的外貌描述，用于注入图片 prompt"""
        if not characters_in_scene or not characters_in_scene.strip():
            return ""

        # 解析角色名（中/英逗号、顿号分隔）
        import re
        names = re.split(r"[,，、]+", characters_in_scene)
        names = [n.strip() for n in names if n.strip()]
        if not names:
            return ""

        db = SessionLocal()
        try:
            chars = (
                db.query(Character)
                .filter(Character.task_id == task_id, Character.name.in_(names))
                .all()
            )
            if not chars:
                return ""

            lines = []
            for c in chars:
                desc = c.description or ""
                # 提取外貌相关字段：外貌、特征、衣着、发型、身材
                appearance_parts = []
                for m in re.finditer(
                    r"[-*]\s*\*\*(.+?)\*\*[：:]\s*(.+)", desc
                ):
                    key = m.group(1).strip()
                    value = m.group(2).strip()
                    if any(
                        kw in key
                        for kw in ["外貌", "特征", "衣着", "发型", "身材", "标志", "细节", "服饰", "体型", "面容"]
                    ):
                        appearance_parts.append(value)

                if appearance_parts:
                    lines.append(f"{c.name}: {'; '.join(appearance_parts)}")

            return "\n".join(lines)
        finally:
            db.close()

    @staticmethod
    def _asset_name_matches(asset_name: str, text: str) -> bool:
        """分词匹配：资产名中 2 字及以上词组在文本中出现则匹配"""
        name = (asset_name or "").strip()
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

    @staticmethod
    def _get_character_bible(task_id: str, subject_text: str) -> str:
        """从资产拆解提取出场角色的三视图英文 prompt，构建角色外貌圣经"""
        from app.models.asset import AssetItem
        if not subject_text or not subject_text.strip():
            return ""
        db = SessionLocal()
        try:
            assets = (
                db.query(AssetItem)
                .filter(
                    AssetItem.task_id == task_id,
                    AssetItem.category == "character",
                    AssetItem.image_status == "success",
                )
                .all()
            )
            lines = []
            for a in assets:
                if not TaskManager._asset_name_matches(a.name or "", subject_text):
                    continue
                ref = (a.image_prompt or a.description or a.name or "").strip()
                if ref:
                    lines.append(f"- {a.name}: {ref[:400]}")
            return "\n".join(lines)
        finally:
            db.close()

    @staticmethod
    def _get_scene_reference(task_id: str, scene_text: str) -> str:
        """从资产拆解提取场景参考描述（英文 image_prompt）。正向匹配：场景资产名出现在分镜 location/environment 里"""
        from app.models.asset import AssetItem
        if not scene_text or not scene_text.strip():
            return ""
        db = SessionLocal()
        try:
            assets = (
                db.query(AssetItem)
                .filter(
                    AssetItem.task_id == task_id,
                    AssetItem.category == "scene",
                    AssetItem.image_status == "success",
                )
                .all()
            )
            for a in assets:
                if TaskManager._asset_name_matches(a.name or "", scene_text):
                    return (a.image_prompt or a.description or a.name or "").strip()[:400]
            return ""
        finally:
            db.close()

    @staticmethod
    def _get_prev_shot_context(task_id: str, scene_num: int) -> str:
        """拿前一镜的画面描述，作为当前镜头的叙事衔接（中文，交给翻译）"""
        db = SessionLocal()
        try:
            prev = (
                db.query(Storyboard)
                .filter(
                    Storyboard.task_id == task_id,
                    Storyboard.scene_number == scene_num - 1,
                )
                .first()
            )
            if not prev:
                return ""
            return (prev.image_prompt or prev.visual_description or "")[:300]
        finally:
            db.close()

    @staticmethod
    def _get_latest_image_url(task_id: str, scene_number: int) -> str | None:
        """获取分镜最新图片的远程 URL，用于视频生成参考"""
        db = SessionLocal()
        try:
            asset = (
                db.query(MediaAsset)
                .filter(
                    MediaAsset.task_id == task_id,
                    MediaAsset.scene_number == scene_number,
                    MediaAsset.asset_type == "image",
                    MediaAsset.status == "success",
                )
                .order_by(MediaAsset.created_at.desc())
                .first()
            )
            return asset.file_url if asset else None
        finally:
            db.close()

    @staticmethod
    def _get_latest_image_path(task_id: str, scene_number: int) -> str | None:
        """获取分镜最新图片的本地路径，用于 ComfyUI 图生视频参考"""
        db = SessionLocal()
        try:
            asset = (
                db.query(MediaAsset)
                .filter(
                    MediaAsset.task_id == task_id,
                    MediaAsset.scene_number == scene_number,
                    MediaAsset.asset_type == "image",
                    MediaAsset.status == "success",
                )
                .order_by(MediaAsset.created_at.desc())
                .first()
            )
            return asset.file_path if asset else None
        finally:
            db.close()

    @staticmethod
    def _get_reference_image_paths(task_id: str, shot: dict, scene_number: int) -> list[str]:
        """
        收集 ComfyUI 图生视频的多张参考图本地路径。
        顺序：分镜图 → 角色图 → 场景图（只保留存在的文件，去重）。
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

        # 1. 分镜图（画面构图/叙事参考）
        shot_path = TaskManager._get_latest_image_path(task_id, scene_number)
        if shot_path and os.path.isfile(shot_path):
            paths.append(shot_path)
            seen.add(os.path.abspath(shot_path))

        # 2. 资产图（角色 + 场景）
        subject = shot.get("subject", "") or ""
        environment = shot.get("environment", "") or ""
        combined_text = f"{subject} {environment}"

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

        return paths

    @staticmethod
    def _parse_char_names(characters_in_scene: str) -> list[str]:
        """从 characters_in_scene 字段解析角色名列表"""
        if not characters_in_scene or not characters_in_scene.strip():
            return []
        import re
        names = re.split(r"[,，、]+", characters_in_scene)
        return [n.strip() for n in names if n.strip()]

    @staticmethod
    def _get_asset_reference_for_shot(task_id: str, shot: dict) -> dict:
        """
        为单个分镜查找资产拆解中的参考图片和描述。
        使用分词匹配（2字及以上词组），优先角色图片。
        返回 {"image_url": str|None, "ref_text": str}
        """
        from app.models.asset import AssetItem

        subject = shot.get("subject", "") or ""
        environment = shot.get("environment", "") or ""
        combined_text = f"{subject} {environment}"

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
    async def _ensure_character_ref(
        task_id: str, char_name: str, char_appearance: str
    ) -> str | None:
        """
        小云雀服化道Agent：确保角色有定妆参考图（正面+侧面 2 张）。
        已存在则返回缓存 URL，否则调用 MiniMax image-01 生成。
        返回正面 HTTPS URL（用于 API subject_reference）或 None。
        """
        from app.services.minimax_image_service import minimax_image_service
        import os

        ref_dir = os.path.join(settings.media_dir, task_id, "characters")
        os.makedirs(ref_dir, exist_ok=True)
        safe_name = char_name.replace("/", "_").replace("\\", "_")[:50]
        ref_path = os.path.join(ref_dir, f"{safe_name}.png")
        url_path = os.path.join(ref_dir, f"{safe_name}.url")
        side_ref_path = os.path.join(ref_dir, f"{safe_name}_side.png")
        side_url_path = os.path.join(ref_dir, f"{safe_name}_side.url")

        # 已存在正面 + 侧面缓存 → 直接返回正面 URL
        if os.path.isfile(ref_path) and os.path.isfile(url_path):
            with open(url_path, "r") as uf:
                cached_url = uf.read().strip()
            if cached_url:
                return cached_url
        elif os.path.isfile(url_path):
            with open(url_path, "r") as uf:
                cached_url = uf.read().strip()
            if cached_url:
                return cached_url

        # 提取外貌 prompt
        lines = char_appearance.split("\n")
        appearance_text = ""
        for line in lines:
            if line.startswith(f"{char_name}:"):
                appearance_text = line[len(char_name) + 1:].strip()
                break
        if not appearance_text:
            appearance_text = char_appearance.split("\n")[0] if char_appearance else ""
        if not appearance_text:
            return None

        try:
            # 生成正面定妆照
            local_path, https_url = await minimax_image_service.generate_character_portrait(
                task_id, char_name, appearance_text
            )
            with open(url_path, "w") as uf:
                uf.write(https_url)
            logger.info(f"[{task_id}] 角色正面定妆照已生成: {char_name}")

            # 生成侧面定妆照（仅当不存在时）
            if not os.path.isfile(side_ref_path) or not os.path.isfile(side_url_path):
                try:
                    _, side_url = await minimax_image_service.generate_character_portrait_side(
                        task_id, char_name, appearance_text
                    )
                    with open(side_url_path, "w") as uf:
                        uf.write(side_url)
                    logger.info(f"[{task_id}] 角色侧面定妆照已生成: {char_name}")
                except Exception as e:
                    logger.warning(f"[{task_id}] 角色侧面照生成失败（非致命）: {char_name}: {e}")

            return https_url
        except Exception as e:
            logger.warning(f"[{task_id}] 角色参考图生成失败: {char_name}: {e}")
            return None

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
    async def _upload_to_oss(local_path: str | None) -> str | None:
        """上传本地文件到 OSS，失败返回 None（降级为本地 /media）"""
        if not local_path:
            return None
        try:
            from app.services.storage import storage
            return await asyncio.to_thread(storage.upload, local_path)
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
                )
                db.add(storyboard)
            db.commit()
        finally:
            db.close()


# 全局单例
task_manager = TaskManager()
