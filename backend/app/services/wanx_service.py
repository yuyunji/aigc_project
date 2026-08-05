"""
Wan-X-Turbo 分镜图片生成服务 (DashScope API)
异步提交 → 轮询 task_id → 下载图片到本地
"""
import asyncio
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


class WanXService:
    """Wan-X-Turbo 图片生成客户端"""

    def __init__(self):
        self.api_key = settings.dashscope_api_key
        self.model = settings.dashscope_image_model
        self.size = settings.dashscope_image_size
        self.poll_interval = settings.dashscope_poll_interval
        self.poll_max = settings.dashscope_poll_max_retries
        self.media_dir = settings.media_dir

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def generate_image(
        self, task_id: str, scene_number: int, prompt: str
    ) -> str:
        """
        为单个分镜生成图片并下载到本地。

        Args:
            task_id:     任务 ID（用于目录组织）
            scene_number: 分镜序号
            prompt:      英文 image prompt

        Returns:
            本地图片路径

        Raises:
            LLMAPIError: API 调用失败或图片生成失败
        """
        if not self.api_key:
            raise LLMAPIError("DashScope API Key 未配置，请在 .env 中设置 DASHSCOPE_API_KEY")

        # 1. 提交任务
        dashscope_task_id = await self._submit_task(prompt)
        logger.info(f"[{task_id}] DashScope 任务已提交: {dashscope_task_id} (分镜 {scene_number})")

        # 2. 轮询结果
        image_url = await self._poll_result(dashscope_task_id, task_id, scene_number)

        # 3. 下载到本地
        local_path = await self._download_image(task_id, scene_number, image_url)
        logger.info(f"[{task_id}] 图片已下载: {local_path}")

        return local_path

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    async def _submit_task(self, prompt: str) -> str:
        """提交文生图任务，返回 DashScope task_id"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": self.model,
            "input": {"prompt": prompt},
            "parameters": {"size": self.size, "n": 1, "prompt_extend": True},
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(DASHSCOPE_URL, json=payload, headers=headers)
            data = resp.json()

        if resp.status_code != 200 or "output" not in data:
            error_msg = data.get("message", data.get("code", str(resp.status_code)))
            raise LLMAPIError(f"DashScope 提交失败: {error_msg}")

        task_id = data["output"].get("task_id")
        if not task_id:
            raise LLMAPIError("DashScope 未返回 task_id")
        return task_id

    async def _poll_result(
        self, dashscope_task_id: str, task_id: str, scene_number: int
    ) -> str:
        """轮询任务结果，返回图片 URL"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = DASHSCOPE_TASK_URL.format(task_id=dashscope_task_id)

        for attempt in range(self.poll_max):
            await asyncio.sleep(self.poll_interval)

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                data = resp.json()

            status = data.get("output", {}).get("task_status", "")
            logger.debug(
                f"[{task_id}] DashScope 轮询 (分镜{scene_number}, "
                f"attempt {attempt + 1}/{self.poll_max}): {status}"
            )

            if status == "SUCCEEDED":
                results = data["output"].get("results", [])
                if results and "url" in results[0]:
                    return results[0]["url"]
                raise LLMAPIError("DashScope 返回成功但无图片 URL")

            if status == "FAILED":
                err = data.get("output", {}).get("message", "未知错误")
                raise LLMAPIError(f"DashScope 图片生成失败: {err}")

            # PENDING / RUNNING → 继续轮询

        raise LLMAPIError(
            f"DashScope 图片生成超时（分镜{scene_number}，"
            f"已等待 {self.poll_max * self.poll_interval}s）"
        )

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    async def _download_image(
        self, task_id: str, scene_number: int, image_url: str
    ) -> str:
        """下载图片到 media/{task_id}/images/"""
        output_dir = os.path.join(self.media_dir, task_id, "images")
        os.makedirs(output_dir, exist_ok=True)

        filename = f"scene_{scene_number:03d}.png"
        filepath = os.path.join(output_dir, filename)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(resp.content)

        return filepath


# 全局单例
wanx_service = WanXService()
