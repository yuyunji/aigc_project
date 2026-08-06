"""
GLM-Image 分镜图片生成服务 (智谱 AI / BigModel API)
同步请求 → 返回图片 URL → 下载到本地
"""
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

GLM_IMAGE_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"


class GLMImageService:
    """GLM-Image 图片生成客户端"""

    def __init__(self):
        self.api_key = settings.glm_api_key
        self.model = settings.image_model
        self.size = settings.image_size
        self.quality = settings.image_quality
        self.media_dir = settings.media_dir

    async def generate_image(
        self,
        task_id: str,
        scene_number: int,
        prompt: str,
        ref_image_path: str | None = None,
    ) -> str:
        """为单个分镜生成图片并下载到本地。ref_image_path 忽略（GLM-Image 不支持参考图）"""
        if not self.api_key:
            raise LLMAPIError("GLM API Key 未配置，请在 .env 中设置 GLM_API_KEY")

        image_url = await self._generate(prompt)
        logger.info(f"[{task_id}] GLM-Image 已生成 (分镜 {scene_number})")

        local_path = await self._download(task_id, scene_number, image_url)
        logger.info(f"[{task_id}] 图片已下载: {local_path}")
        return local_path

    async def _generate(self, prompt: str, ref_image_path: str | None = None) -> str:
        """调用 GLM-Image API，返回图片 URL"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt[:2000],
            "size": self.size,
            "quality": self.quality,
            "watermark_enabled": True,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(GLM_IMAGE_URL, json=payload, headers=headers)
            data = resp.json()

        if resp.status_code != 200:
            error_msg = data.get("error", {}).get("message", str(resp.status_code))
            raise LLMAPIError(f"GLM-Image 生成失败: {error_msg}")

        results = data.get("data", [])
        if not results or "url" not in results[0]:
            raise LLMAPIError("GLM-Image 未返回图片 URL")
        return results[0]["url"]

    async def _download(
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
glm_image_service = GLMImageService()
