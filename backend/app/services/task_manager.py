"""
级联任务编排器
任务链路：原著文本 → 剧本大纲 → 人物角色设定 → 分镜脚本
每个阶段依赖前一阶段结果，失败则标记任务为 failed。

解析 LLM 返回的 Markdown 文本，拆分为独立角色和分镜记录存入 SQLite。

边界保护：
- 单任务总超时 (task_total_timeout)
- 每阶段 LLM 调用超时 (llm_call_timeout)
- 输入校验 & Token 预算检查
- 失败自动回滚状态 & 友好错误信息
"""
import asyncio
import logging
import re

from app.config import settings
from app.database import SessionLocal
from app.models.task import Task
from app.models.outline import Outline
from app.models.character import Character
from app.models.storyboard import Storyboard
from app.services.text_processor import TextProcessor
from app.services.llm_service import llm_service
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
            # 总超时
            error_msg = f"任务执行超时（{total_timeout} 秒），请尝试缩短输入文本"
            self._update_status(task_id, "failed", error=error_msg)
            logger.warning(f"[{task_id}] 任务总超时 ({total_timeout}s)")

        except (TokenLimitError, LLMAPIError, InputTooLargeError, EmptyChunksError) as e:
            # 已知业务异常 → 友好消息
            friendly = self._friendly_error(e)
            self._update_status(task_id, "failed", error=friendly)
            logger.warning(f"[{task_id}] {friendly}")

        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            self._update_status(task_id, "failed", error=error_msg)
            logger.exception(f"[{task_id}] 未知错误")

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

        # ── 阶段 2：生成剧本大纲 ──
        logger.info(f"[{task_id}] 阶段2: 生成大纲")
        self._update_status(task_id, "running", progress=25)

        stage_timeout = settings.llm_call_timeout + 10
        outline_content = await asyncio.wait_for(
            llm_service.generate_outline(chunks),
            timeout=stage_timeout,
        )

        if not outline_content or not outline_content.strip():
            raise LLMAPIError("大纲生成结果为空，请重试")

        self._save_outline(task_id, outline_content)
        self._update_status(task_id, "running", progress=45)
        logger.info(f"[{task_id}] 大纲生成完成 ({len(outline_content)} 字)")

        # ── 阶段 3：生成人物角色设定 ──
        logger.info(f"[{task_id}] 阶段3: 生成人物角色")
        self._update_status(task_id, "running", progress=50)

        characters_text = await asyncio.wait_for(
            llm_service.generate_characters(outline_content, source_text),
            timeout=stage_timeout,
        )

        if not characters_text or not characters_text.strip():
            raise LLMAPIError("人物角色生成结果为空，请重试")

        character_list = self._parse_characters(characters_text)
        if not character_list:
            raise LLMAPIError("人物角色解析失败，请重试")

        self._save_characters(task_id, character_list)
        self._update_status(task_id, "running", progress=70)
        logger.info(f"[{task_id}] 人物生成完成: {len(character_list)} 个角色")

        # ── 阶段 4：生成分镜脚本 ──
        logger.info(f"[{task_id}] 阶段4: 生成分镜")
        self._update_status(task_id, "running", progress=75)

        storyboard_text = await asyncio.wait_for(
            llm_service.generate_storyboard(outline_content, characters_text),
            timeout=stage_timeout,
        )

        if not storyboard_text or not storyboard_text.strip():
            raise LLMAPIError("分镜脚本生成结果为空，请重试")

        scene_list = self._parse_storyboards(storyboard_text)
        if not scene_list:
            raise LLMAPIError("分镜脚本解析失败，请重试")

        self._save_storyboards(task_id, scene_list)

        # ── 完成 ──
        self._update_status(task_id, "success", progress=100)
        logger.info(
            f"[{task_id}] ✅ 级联任务完成: "
            f"大纲1篇, 角色{len(character_list)}个, 分镜{len(scene_list)}个"
        )

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
    # Markdown 解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_characters(markdown_text: str) -> list[dict]:
        """
        解析 Claude 返回的人物角色 Markdown。

        期望格式：
            ## 角色名
            描述内容...

        返回: [{"name": "角色名", "description": "描述"}, ...]
        """
        results = []
        pattern = r"##\s+(.+?)\n(.*?)(?=##\s+|\Z)"
        matches = re.findall(pattern, markdown_text, re.DOTALL)

        for name, desc in matches:
            name = name.strip()
            desc = desc.strip()
            # 跳过分镜标题混入的情况
            if name and desc and "分镜" not in name:
                results.append({"name": name[:100], "description": desc[:3000]})

        if not results:
            results.append({
                "name": "未解析角色",
                "description": markdown_text.strip()[:2000],
            })

        return results

    @staticmethod
    def _parse_storyboards(markdown_text: str) -> list[dict]:
        """
        解析 Claude 返回的分镜 Markdown。

        期望格式：
            ## 分镜N：标题
            描述内容...

        返回: [{"scene_number": N, "description": "完整内容"}, ...]
        """
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
                "description": full_desc.strip()[:3000],
            })

        if not results:
            sections = re.split(r"\n(?=##\s)", markdown_text)
            for i, section in enumerate(sections, 1):
                section = section.strip()
                if section:
                    results.append({
                        "scene_number": i,
                        "description": section[:2000],
                    })

        return results

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
        db = SessionLocal()
        try:
            for scene_data in scene_list:
                storyboard = Storyboard(
                    task_id=task_id,
                    scene_number=scene_data["scene_number"],
                    description=scene_data["description"],
                )
                db.add(storyboard)
            db.commit()
        finally:
            db.close()


# 全局单例
task_manager = TaskManager()
