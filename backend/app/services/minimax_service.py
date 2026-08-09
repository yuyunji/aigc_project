"""
MiniMax-H3 文生视频服务 (MiniMax API)
参考小云雀剧本→视频工作流：每分镜一键文生视频（含音频同步）
"""
import asyncio
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

MINIMAX_SUBMIT_URL = "https://api.minimaxi.com/v2/video_generation"
MINIMAX_QUERY_URL = "https://api.minimaxi.com/v2/query/video_generation"


class MiniMaxService:
    """MiniMax-H3 text-to-video client"""

    def __init__(self):
        self.api_key = settings.minimax_api_key
        self.model = settings.minimax_model
        self.duration = settings.minimax_video_duration
        self.ratio = settings.minimax_video_ratio
        self.resolution = settings.minimax_video_resolution
        self.motion_strength = settings.minimax_motion_strength
        self.poll_interval = settings.minimax_poll_interval
        self.poll_max = settings.minimax_poll_max_retries
        self.media_dir = settings.media_dir

    async def generate_video(
        self, task_id: str, scene_number: int, prompt: str
    ) -> str:
        """
        分镜→视频：提交→轮询→下载，返回本地 .mp4 路径。
        MiniMax-H3 内置音频同步，无需单独配音。
        """
        if not self.api_key:
            raise LLMAPIError(
                "MiniMax API Key 未配置，请在 .env 中设置 MINIMAX_API_KEY"
            )

        # 1. 提交
        mm_task_id = await self._submit_task(prompt)
        logger.info(
            f"[{task_id}] MiniMax 已提交: {mm_task_id} (分镜 {scene_number})"
        )

        # 2. 轮询
        video_url = await self._poll_result(mm_task_id, task_id, scene_number)

        # 3. 下载
        local_path = await self._download_video(task_id, scene_number, video_url)
        logger.info(f"[{task_id}] 视频已下载: {local_path}")

        return local_path

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    async def _submit_task(self, prompt: str) -> str:
        """提交文生视频任务，返回 MiniMax task_id"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "content": [{"type": "text", "text": prompt[:2000]}],
            "duration": self.duration,
            "ratio": self.ratio,
            "resolution": self.resolution,
            "motion_strength": self.motion_strength,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(MINIMAX_SUBMIT_URL, json=payload, headers=headers)
            data = resp.json()

        if resp.status_code not in (200, 201):
            error_msg = data.get("error", {}).get("message", str(resp.status_code))
            raise LLMAPIError(f"MiniMax 提交失败: {error_msg}")

        task_id = data.get("task_id", "")
        if not task_id:
            raise LLMAPIError("MiniMax 未返回 task_id")
        return task_id

    async def _poll_result(
        self, mm_task_id: str, task_id: str, scene_number: int
    ) -> str:
        """轮询任务结果，返回视频下载 URL"""
        headers = {"Authorization": f"Bearer {self.api_key}"}

        for attempt in range(self.poll_max):
            await asyncio.sleep(self.poll_interval)

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    MINIMAX_QUERY_URL,
                    params={"task_id": mm_task_id},
                    headers=headers,
                )
                data = resp.json()

            # v2 API 返回 items 数组，找到匹配 task_id 的条目
            items = data.get("items", [])
            matched = next((it for it in items if it.get("id") == mm_task_id), None)
            if not matched:
                matched = items[0] if items else {}

            status = matched.get("status", "")
            logger.debug(
                f"[{task_id}] MiniMax 轮询 (分镜{scene_number}, "
                f"attempt {attempt + 1}/{self.poll_max}): {status}"
            )

            if status == "succeeded":
                video_url = matched.get("content", {}).get("url", "")
                if not video_url:
                    raise LLMAPIError("MiniMax 完成但无 video URL")
                return video_url

            if status == "failed":
                err = matched.get("error", {}).get("message", "未知错误")
                raise LLMAPIError(f"MiniMax 视频生成失败: {err}")

            # queued / running → 继续轮询

        raise LLMAPIError(
            f"MiniMax 视频生成超时（分镜{scene_number}，"
            f"已等待 {self.poll_max * self.poll_interval}s）"
        )

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    async def _download_video(
        self, task_id: str, scene_number: int, video_url: str
    ) -> str:
        """下载视频到 media/{task_id}/videos/"""
        output_dir = os.path.join(self.media_dir, task_id, "videos")
        os.makedirs(output_dir, exist_ok=True)

        filename = f"scene_{scene_number:03d}.mp4"
        filepath = os.path.join(output_dir, filename)

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(resp.content)

        return filepath


# 全局单例
minimax_service = MiniMaxService()
