"""
Doubao-Seedream 分镜图片生成服务 (火山引擎 ARK API)
支持角色参考图 (image-to-image) 确保跨分镜角色一致性
"""
import base64
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

ARK_IMAGE_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"


class SeedreamService:
    """Doubao-Seedream 图片生成客户端"""

    def __init__(self):
        self.api_key = settings.ark_api_key
        self.model = settings.ark_image_model
        self.size = settings.ark_image_size
        self.media_dir = settings.media_dir

    async def generate_character_ref(
        self, task_id: str, character_name: str, appearance_prompt: str
    ) -> str:
        """
        生成角色定妆参考图（正面半身、清晰五官、完整服装）。
        """
        prompt = (
            f"{appearance_prompt}. "
            "Character reference sheet, front view, half-body portrait, "
            "clean simple background, full outfit visible, clear facial features, "
            "neutral pose, character design sheet style"
        )
        return await self._generate(task_id, prompt)

    async def generate_image(
        self,
        task_id: str,
        scene_number: int,
        prompt: str,
        ref_image_path: str | None = None,
    ) -> str:
        """
        为单个分镜生成图片并下载到本地。

        Args:
            ref_image_path: 角色定妆参考图路径，用于保持角色一致性
        """
        if not self.api_key:
            raise LLMAPIError("ARK API Key 未配置，请在 .env 中设置 ARK_API_KEY")

        image_url = await self._generate(task_id, prompt, ref_image_path)
        logger.info(f"[{task_id}] Seedream 图片已生成 (分镜 {scene_number})")

        local_path = await self._download(task_id, scene_number, image_url)
        logger.info(f"[{task_id}] 图片已下载: {local_path}")
        return local_path

    async def _generate(
        self, task_id: str, prompt: str, ref_image_path: str | None = None
    ) -> str:
        """调用 ARK API 生成图片，返回图片 URL"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt[:2000],
            "response_format": "url",
            "size": self.size,
            "stream": False,
            "watermark": True,
        }

        # 角色参考图 (image-to-image)
        if ref_image_path:
            b64 = self._encode_image(ref_image_path)
            if b64:
                payload["image"] = f"data:image/png;base64,{b64}"
                logger.debug(f"[{task_id}] 参考图已注入 ({os.path.basename(ref_image_path)})")

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(ARK_IMAGE_URL, json=payload, headers=headers)
            data = resp.json()

        if resp.status_code != 200:
            error_msg = data.get("error", {}).get("message", str(resp.status_code))
            raise LLMAPIError(f"Seedream 生成失败: {error_msg}")

        results = data.get("data", [])
        if not results or "url" not in results[0]:
            raise LLMAPIError("Seedream 未返回图片 URL")
        return results[0]["url"]

    @staticmethod
    def _encode_image(filepath: str) -> str:
        """读取本地图片并转为 base64"""
        try:
            if not os.path.isfile(filepath):
                return ""
            with open(filepath, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""

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
seedream_service = SeedreamService()
