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

        # ── 阶段 5-6：媒体链路（MiniMax-H3 文生视频 + FFmpeg 拼接） ──
        video_paths = []
        if settings.auto_media_pipeline:
            video_paths = await self._run_storyboard_to_video(task_id, scene_list)
            await self._run_composite(task_id, video_paths, None, scene_list, character_list)

        # ── 完成 ──
        self._update_status(task_id, "success", progress=100)
        summary = f"大纲1篇, 角色{len(character_list)}个, 分镜{len(scene_list)}个"
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
    # Markdown 解析
    # ------------------------------------------------------------------

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
    # 阶段 5-6：媒体链路 (MiniMax-H3)
    # ------------------------------------------------------------------

    async def _run_storyboard_to_video(
        self, task_id: str, scene_list: list[dict]
    ) -> list[str]:
        """
        阶段5：分镜→视频 (MiniMax-H3 文生视频，含内置音频)。
        参考小云雀：每分镜一键生成视频，Prompt 含视觉+音频描述。
        """
        total = len(scene_list)
        logger.info(f"[{task_id}] 阶段5: 分镜→视频 MiniMax-H3 ({total} 个分镜)")
        self._update_status(task_id, "running", progress=78)

        video_paths = []
        MAX_RETRIES = 1

        for i, scene in enumerate(scene_list):
            scene_num = scene["scene_number"]
            progress = 78 + int((i / max(total, 1)) * 17)

            # 构建 MiniMax prompt：视觉描述 + Sound: 音频描述
            visual = scene.get("visual_description", "") or scene.get("description", "")
            camera = scene.get("camera_movement", "")
            dialogue = scene.get("dialogue", "")
            location = scene.get("location", "")
            time_of_day = scene.get("time_of_day", "")

            sound_clause = ""
            if dialogue:
                # 取前两行对话作为 Sound 提示
                lines = dialogue.strip().split("\n")[:2]
                sound_clause = f"Sound: characters speaking naturally, ambient {location or 'scene'} atmosphere"

            prompt_parts = []
            if camera:
                prompt_parts.append(f"Camera: {camera}")
            if visual:
                prompt_parts.append(visual[:1500])
            if sound_clause:
                prompt_parts.append(sound_clause)
            prompt = ". ".join(prompt_parts)[:2000]

            # 重试循环
            for retry in range(MAX_RETRIES + 1):
                asset = self._create_media_asset(
                    task_id, "video", scene_num, prompt
                )
                try:
                    path = await asyncio.wait_for(
                        minimax_service.generate_video(task_id, scene_num, prompt),
                        timeout=300,
                    )
                    video_paths.append(path)
                    self._update_media_asset(asset.id, "success", file_path=path)
                    logger.info(f"[{task_id}] 分镜{scene_num} 视频生成成功")
                    break
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[{task_id}] 分镜{scene_num} 视频生成超时"
                        + (f" (重试 {retry+1}/{MAX_RETRIES})" if retry < MAX_RETRIES else "")
                    )
                    self._update_media_asset(asset.id, "failed", error="生成超时")
                except Exception as e:
                    logger.warning(
                        f"[{task_id}] 分镜{scene_num} 视频生成失败: {e}"
                        + (f" (重试 {retry+1}/{MAX_RETRIES})" if retry < MAX_RETRIES else "")
                    )
                    self._update_media_asset(asset.id, "failed", error=str(e)[:500])
                    if retry >= MAX_RETRIES:
                        break

            self._update_status(task_id, "running", progress=progress)

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
        """为单个分镜生成图片，返回 {status, file_path, error}"""
        scene_num = scene["scene_number"]
        visual = scene.get("visual_description", "") or scene.get("description", "")
        prebuilt = scene.get("image_prompt", "")

        if prebuilt and prebuilt.strip():
            prompt = prebuilt
        elif visual and visual.strip():
            prompt = visual[:500]
        else:
            prompt = "cinematic scene, dramatic lighting, 4K, high quality"

        # 全局风格前缀
        if settings.ark_image_style:
            prompt = f"{settings.ark_image_style}. {prompt}"

        # 注入出场角色外貌特征，确保跨分镜角色一致性
        char_appearance = self._get_characters_appearance(
            task_id, scene.get("characters_in_scene", "")
        )

        # 为第一个出场角色生成定妆参考图，作为图片生成的 identity anchor
        ref_image_path = None
        char_names = self._parse_char_names(scene.get("characters_in_scene", ""))
        if char_names and char_appearance:
            ref_image_path = await self._ensure_character_ref(
                task_id, char_names[0], char_appearance
            )
        if ref_image_path:
            prompt = (
                f"{prompt}. "
                f"Keep the character appearance exactly as in the reference image, "
                f"same face, same outfit, same hair style"
            )

        self._cleanup_asset(task_id, scene_num, "image")
        asset = self._create_media_asset(task_id, "image", scene_num, prompt)
        try:
            from app.services.seedream_service import seedream_service
            path = await asyncio.wait_for(
                seedream_service.generate_image(task_id, scene_num, prompt, ref_image_path),
                timeout=180,
            )
            self._update_media_asset(asset.id, "success", file_path=path)
            return {"status": "success", "file_path": path, "asset_id": asset.id}
        except asyncio.TimeoutError:
            self._update_media_asset(asset.id, "failed", error="图片生成超时（180s）")
            return {"status": "failed", "error": "图片生成超时，请重试"}
        except Exception as e:
            err = str(e)[:500]
            self._update_media_asset(asset.id, "failed", error=err)
            return {"status": "failed", "error": err}

    async def generate_scene_video(self, task_id: str, scene: dict) -> dict:
        """为单个分镜生成视频 (MiniMax-H3)，返回 {status, file_path, error}"""
        scene_num = scene["scene_number"]
        visual = scene.get("visual_description", "") or scene.get("description", "")
        camera = scene.get("camera_movement", "")
        dialogue = scene.get("dialogue", "")
        location = scene.get("location", "")

        sound = ""
        if dialogue:
            sound = f"Sound: characters speaking naturally, ambient {location or 'scene'} atmosphere"

        parts = []
        if camera:
            parts.append(f"Camera: {camera}")
        if visual:
            parts.append(visual[:1500])
        if sound:
            parts.append(sound)
        prompt = ". ".join(parts)[:2000]

        self._cleanup_asset(task_id, scene_num, "video")
        asset = self._create_media_asset(task_id, "video", scene_num, prompt)
        try:
            path = await asyncio.wait_for(
                minimax_service.generate_video(task_id, scene_num, prompt),
                timeout=300,
            )
            self._update_media_asset(asset.id, "success", file_path=path)
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
    def _cleanup_asset(task_id: str, scene_number: int, asset_type: str) -> int:
        """删除同分镜同类型的旧记录，防止标签堆积"""
        db = SessionLocal()
        try:
            stale = (
                db.query(MediaAsset)
                .filter(
                    MediaAsset.task_id == task_id,
                    MediaAsset.scene_number == scene_number,
                    MediaAsset.asset_type == asset_type,
                )
                .all()
            )
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
    def _parse_char_names(characters_in_scene: str) -> list[str]:
        """从 characters_in_scene 字段解析角色名列表"""
        if not characters_in_scene or not characters_in_scene.strip():
            return []
        import re
        names = re.split(r"[,，、]+", characters_in_scene)
        return [n.strip() for n in names if n.strip()]

    @staticmethod
    async def _ensure_character_ref(
        task_id: str, char_name: str, char_appearance: str
    ) -> str | None:
        """
        确保角色有定妆参考图。已存在则直接返回路径，否则调用 Seedream 生成。
        参考图保存在 media/{task_id}/characters/ 下。
        """
        from app.services.seedream_service import seedream_service
        import os

        ref_dir = os.path.join(settings.media_dir, task_id, "characters")
        os.makedirs(ref_dir, exist_ok=True)
        # 安全文件名
        safe_name = char_name.replace("/", "_").replace("\\", "_")[:50]
        ref_path = os.path.join(ref_dir, f"{safe_name}.png")

        # 已存在则直接返回
        if os.path.isfile(ref_path):
            return ref_path

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
            ref_url = await seedream_service._generate(
                task_id,
                f"{settings.ark_image_style or ''}. "
                f"Character reference portrait of {char_name}: {appearance_text}. "
                "Front view, half-body, clean background, full outfit, clear face",
            )
            # 下载参考图
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(ref_url)
                resp.raise_for_status()
            with open(ref_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"[{task_id}] 角色定妆参考图已生成: {char_name} → {ref_path}")
            return ref_path
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
        """逐条存入结构化分镜，description 存储人类可读短文"""
        db = SessionLocal()
        try:
            for scene_data in scene_list:
                # 构建人类可读的 description 文本（不再存 JSON 代码）
                desc_parts = []
                title = scene_data.get("scene_title", "")
                location = scene_data.get("location", "")
                time_of_day = scene_data.get("time_of_day", "")
                camera = scene_data.get("camera_movement", "")
                chars = scene_data.get("characters_in_scene", "")
                visual = scene_data.get("visual_description", "")
                dialogue = scene_data.get("dialogue", "")

                # 标题 + 场景信息
                header = f"## {title}" if title else f"## 分镜{scene_data['scene_number']}"
                if location or time_of_day:
                    loc_info = f"{location} · {time_of_day}" if location and time_of_day else (location or time_of_day)
                    header += f"\n*{loc_info}*"
                desc_parts.append(header)

                # 出场角色
                if chars:
                    desc_parts.append(f"\n**出场角色**：{chars}")

                # 画面描述（核心内容）
                if visual:
                    desc_parts.append(f"\n{visual}")

                # 台词
                if dialogue:
                    desc_parts.append(f"\n> {dialogue.replace(chr(10), chr(10) + '> ')}")

                # 运镜
                if camera:
                    desc_parts.append(f"\n🎥 运镜：{camera}")

                readable_desc = "\n".join(desc_parts)

                storyboard = Storyboard(
                    task_id=task_id,
                    scene_number=scene_data["scene_number"],
                    scene_title=title,
                    location=location,
                    time_of_day=time_of_day,
                    characters_in_scene=chars,
                    camera_movement=camera,
                    dialogue=dialogue,
                    visual_description=visual,
                    image_prompt=scene_data.get("image_prompt", ""),
                    duration_seconds=scene_data.get("duration_seconds", 5.0),
                    description=readable_desc,
                )
                db.add(storyboard)
            db.commit()
        finally:
            db.close()


# 全局单例
task_manager = TaskManager()
