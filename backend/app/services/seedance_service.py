"""
Seedance 1.5 Pro 图生视频服务 (fal.ai)
上传图片 → 提交视频生成 → 轮询 → 下载
"""
import asyncio
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

FAL_SUBMIT_URL = "https://fal.run/{model}"
FAL_RESULT_URL = "https://fal.run/{model}/requests/{request_id}"
FAL_STATUS_URL = "https://fal.run/{model}/requests/{request_id}/status"


class SeedanceService:
    """Seedance 1.5 Pro 图生视频客户端 (via fal.ai REST API)"""

    def __init__(self):
        self.api_key = settings.fal_key
        self.model = settings.fal_video_model
        self.duration = settings.fal_video_duration
        self.resolution = settings.fal_video_resolution
        self.poll_interval = settings.fal_poll_interval
        self.poll_max = settings.fal_poll_max_retries
        self.media_dir = settings.media_dir

    async def generate_video(
        self, task_id: str, scene_number: int, image_path: str, prompt: str
    ) -> str:
        """
        为单个分镜图片生成视频片段。

        Args:
            task_id:      任务 ID
            scene_number: 分镜序号
            image_path:   本地图片路径
            prompt:       动作描述 prompt

        Returns:
            本地视频文件路径
        """
        if not self.api_key:
            raise LLMAPIError("fal.ai API Key 未配置，请在 .env 中设置 FAL_KEY")

        # 1. 上传图片获取 URL（使用临时方式：直接作为 data URI 或上传）
        image_url = await self._upload_image(image_path, task_id, scene_number)

        # 2. 提交视频生成
        request_id = await self._submit_task(image_url, prompt)
        logger.info(
            f"[{task_id}] Seedance 任务已提交: {request_id} (分镜 {scene_number})"
        )

        # 3. 轮询结果
        video_url = await self._poll_result(request_id, task_id, scene_number)

        # 4. 下载到本地
        local_path = await self._download_video(task_id, scene_number, video_url)
        logger.info(f"[{task_id}] 视频已下载: {local_path}")

        return local_path

    async def _upload_image(
        self, image_path: str, task_id: str, scene_number: int
    ) -> str:
        """返回本地图片的 file:// 路径，由 fal.ai 自动处理"""
        # fal.ai API 支持 file:// URL 或可访问的 HTTP URL
        abs_path = os.path.abspath(image_path).replace("\\", "/")
        return f"file:///{abs_path}"

    async def _submit_task(self, image_url: str, prompt: str) -> str:
        """提交图生视频任务"""
        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "image_url": image_url,
            "prompt": prompt,
            "duration": self.duration,
            "resolution": self.resolution,
            "generate_audio": False,  # 我们单独做 TTS
        }

        url = FAL_SUBMIT_URL.format(model=self.model)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            data = resp.json()

        if resp.status_code not in (200, 201):
            error_msg = data.get("detail", str(resp.status_code))
            raise LLMAPIError(f"Seedance 提交失败: {error_msg}")

        request_id = data.get("request_id", "")
        if not request_id:
            raise LLMAPIError("Seedance 未返回 request_id")
        return request_id

    async def _poll_result(
        self, request_id: str, task_id: str, scene_number: int
    ) -> str:
        """轮询 video 生成结果"""
        status_url = FAL_STATUS_URL.format(
            model=self.model, request_id=request_id
        )
        headers = {"Authorization": f"Key {self.api_key}"}

        for attempt in range(self.poll_max):
            await asyncio.sleep(self.poll_interval)

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(status_url, headers=headers)
                data = resp.json()

            status = data.get("status", "")
            logger.debug(
                f"[{task_id}] Seedance 轮询 (分镜{scene_number}, "
                f"attempt {attempt + 1}/{self.poll_max}): {status}"
            )

            if status == "COMPLETED":
                video_data = data.get("output", data.get("result", {}))
                video_url = video_data.get("video", {}).get("url", "")
                if not video_url:
                    video_url = video_data.get("url", "")
                if not video_url:
                    raise LLMAPIError("Seedance 完成但无视频 URL")
                return video_url

            if status in ("FAILED", "CANCELLED"):
                err = data.get("error", data.get("status", "未知错误"))
                raise LLMAPIError(f"Seedance 视频生成失败: {err}")

        raise LLMAPIError(
            f"Seedance 视频生成超时（分镜{scene_number}，"
            f"已等待 {self.poll_max * self.poll_interval}s）"
        )

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
seedance_service = SeedanceService()
