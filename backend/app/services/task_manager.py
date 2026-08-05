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

from app.config import settings
from app.database import SessionLocal
from app.models.task import Task
from app.models.outline import Outline
from app.models.character import Character
from app.models.storyboard import Storyboard
from app.models.media import MediaAsset
from app.services.text_processor import TextProcessor
from app.services.llm_service import llm_service
from app.services.prompt_builder import prompt_builder
from app.services.wanx_service import wanx_service
from app.services.seedance_service import seedance_service
from app.services.tts_service import tts_service
from app.services.video_composer import video_composer
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

        # ── 阶段 5-8：媒体链路（可选，自动执行） ──
        image_paths = []
        video_paths = []
        audio_paths = []
        if settings.auto_media_pipeline:
            # 5. 分镜图片生成
            image_paths = await self._run_image_generation(
                task_id, scene_list
            )
            # 6. 图生视频
            video_paths = await self._run_video_generation(
                task_id, scene_list, image_paths
            )
            # 7. 角色配音
            audio_paths = await self._run_tts_generation(
                task_id, character_list
            )
            # 8. 字幕合成
            await self._run_composite(
                task_id, video_paths, audio_paths, scene_list, character_list
            )

        # ── 完成 ──
        self._update_status(task_id, "success", progress=100)
        summary = f"大纲1篇, 角色{len(character_list)}个, 分镜{len(scene_list)}个"
        if settings.auto_media_pipeline:
            summary += f", 图片{len(image_paths)}张, 视频{len(video_paths)}段, 配音{len(audio_paths)}段"
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
                    "camera_movement": str(scene.get("camera_movement", "")),
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
    # 阶段 5-8：媒体链路
    # ------------------------------------------------------------------

    async def _run_image_generation(
        self, task_id: str, scene_list: list[dict]
    ) -> list[str]:
        """
        阶段5：为每个分镜生成图片（Wan-X-Turbo）。
        返回: 本地图片路径列表
        """
        logger.info(f"[{task_id}] 阶段5: 分镜图片生成 ({len(scene_list)} 个分镜)")
        self._update_status(task_id, "running", progress=78)

        image_paths = []
        total = len(scene_list)

        for i, scene in enumerate(scene_list):
            scene_num = scene["scene_number"]
            # 优先使用 visual_description 或 image_prompt 生成图片
            visual = scene.get("visual_description", "") or scene.get("description", "")
            prebuilt = scene.get("image_prompt", "")
            progress = 78 + int((i / max(total, 1)) * 10)

            # 构建 image prompt：优先用 LLM 生成的英文 prompt
            if prebuilt:
                prompt = prebuilt
            else:
                prompt = await prompt_builder.build_image_prompt(visual)

            # 保存 media asset
            asset = self._create_media_asset(
                task_id, "image", scene_num, prompt
            )

            try:
                path = await wanx_service.generate_image(
                    task_id, scene_num, prompt
                )
                image_paths.append(path)
                self._update_media_asset(asset.id, "success", file_path=path)
            except Exception as e:
                logger.warning(f"[{task_id}] 分镜{scene_num} 图片生成失败: {e}")
                self._update_media_asset(
                    asset.id, "failed", error=str(e)[:500]
                )
                # 图片生成失败不阻断后续阶段，用占位继续

            self._update_status(task_id, "running", progress=progress)

        self._update_status(task_id, "running", progress=88)
        logger.info(
            f"[{task_id}] 图片生成完成: {len(image_paths)}/{total} 成功"
        )
        return image_paths

    async def _run_video_generation(
        self, task_id: str, scene_list: list[dict], image_paths: list[str]
    ) -> list[str]:
        """
        阶段6：图生视频（Seedance 1.5 Pro）。
        返回: 本地视频路径列表
        """
        if not image_paths:
            logger.warning(f"[{task_id}] 无可用图片，跳过视频生成")
            return []

        logger.info(f"[{task_id}] 阶段6: 图生视频 ({len(image_paths)} 段)")
        self._update_status(task_id, "running", progress=89)

        video_paths = []
        total = len(image_paths)

        for i, (img_path, scene) in enumerate(
            zip(image_paths, scene_list[: len(image_paths)])
        ):
            scene_num = scene["scene_number"]
            progress = 89 + int((i / max(total, 1)) * 5)

            # 使用场景的运镜和画面描述生成针对性视频 prompt
            camera = scene.get("camera_movement", "")
            visual = scene.get("visual_description", "")
            action_prompt = (
                f"{camera + '. ' if camera else ''}"
                f"{visual[:200] + '. ' if visual else ''}"
                f"cinematic motion, smooth movement, professional lighting"
            )

            asset = self._create_media_asset(
                task_id, "video", scene_num, action_prompt
            )

            try:
                path = await seedance_service.generate_video(
                    task_id, scene_num, img_path, action_prompt
                )
                video_paths.append(path)
                self._update_media_asset(asset.id, "success", file_path=path)
            except Exception as e:
                logger.warning(f"[{task_id}] 分镜{scene_num} 视频生成失败: {e}")
                self._update_media_asset(
                    asset.id, "failed", error=str(e)[:500]
                )

            self._update_status(task_id, "running", progress=progress)

        self._update_status(task_id, "running", progress=94)
        logger.info(
            f"[{task_id}] 视频生成完成: {len(video_paths)}/{total} 成功"
        )
        return video_paths

    async def _run_tts_generation(
        self, task_id: str, character_list: list[dict]
    ) -> list[str]:
        """
        阶段7：角色配音（Volcengine TTS）。
        为每个角色合成一段示例语音。
        """
        logger.info(f"[{task_id}] 阶段7: 角色配音 ({len(character_list)} 个角色)")
        self._update_status(task_id, "running", progress=95)

        audio_paths = []
        total = len(character_list)

        for i, char in enumerate(character_list):
            char_name = char["name"]
            # 从角色描述中提取可能的台词片段
            desc = char.get("description", "")
            sample_text = self._extract_dialogue_sample(char_name, desc)

            if not sample_text:
                continue

            progress = 95 + int((i / max(total, 1)) * 2)

            asset = self._create_media_asset(
                task_id, "audio", None, sample_text, char_name
            )

            try:
                path = await tts_service.synthesize(
                    task_id, char_name, sample_text
                )
                audio_paths.append(path)
                self._update_media_asset(asset.id, "success", file_path=path)
            except Exception as e:
                logger.warning(f"[{task_id}] 角色 {char_name} TTS 失败: {e}")
                self._update_media_asset(
                    asset.id, "failed", error=str(e)[:500]
                )

            self._update_status(task_id, "running", progress=progress)

        self._update_status(task_id, "running", progress=97)
        logger.info(
            f"[{task_id}] 配音完成: {len(audio_paths)}/{total} 成功"
        )
        return audio_paths

    async def _run_composite(
        self,
        task_id: str,
        video_paths: list[str],
        audio_paths: list[str],
        scene_list: list[dict],
        character_list: list[dict],
    ) -> None:
        """
        阶段8：字幕合成（FFmpeg）。
        拼接视频 + 音频 + 生成 SRT 字幕 → 最终 MP4。
        """
        if not video_paths:
            logger.warning(f"[{task_id}] 无可用视频，跳过合成")
            return

        logger.info(f"[{task_id}] 阶段8: 字幕合成")
        self._update_status(task_id, "running", progress=98)

        # 生成字幕数据
        subtitle_data = self._build_subtitles(
            scene_list, character_list, len(video_paths)
        )

        asset = self._create_media_asset(
            task_id, "composite", None, "final composite"
        )

        try:
            output_path = await video_composer.composite(
                task_id, video_paths, audio_paths, subtitle_data
            )
            self._update_media_asset(asset.id, "success", file_path=output_path)
        except Exception as e:
            logger.warning(f"[{task_id}] 视频合成失败: {e}")
            self._update_media_asset(asset.id, "failed", error=str(e)[:500])

        self._update_status(task_id, "running", progress=99)

    # ------------------------------------------------------------------
    # 媒体链路辅助方法
    # ------------------------------------------------------------------

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
            return asset
        finally:
            db.close()

    @staticmethod
    def _update_media_asset(
        asset_id: str,
        status: str,
        file_path: str | None = None,
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
                if error:
                    asset.error_message = error
                db.commit()
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
        """逐条存入结构化分镜"""
        db = SessionLocal()
        try:
            for scene_data in scene_list:
                storyboard = Storyboard(
                    task_id=task_id,
                    scene_number=scene_data["scene_number"],
                    scene_title=scene_data.get("scene_title", ""),
                    location=scene_data.get("location", ""),
                    time_of_day=scene_data.get("time_of_day", ""),
                    characters_in_scene=scene_data.get("characters_in_scene", ""),
                    camera_movement=scene_data.get("camera_movement", ""),
                    dialogue=scene_data.get("dialogue", ""),
                    visual_description=scene_data.get("visual_description", ""),
                    image_prompt=scene_data.get("image_prompt", ""),
                    duration_seconds=scene_data.get("duration_seconds", 5.0),
                    description=scene_data.get("description", ""),
                )
                db.add(storyboard)
            db.commit()
        finally:
            db.close()


# 全局单例
task_manager = TaskManager()
