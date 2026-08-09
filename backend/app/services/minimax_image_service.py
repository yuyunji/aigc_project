"""
MiniMax image-01 分镜图片生成服务
支持 subject_reference 角色参考图确保跨分镜角色一致性
参考小云雀流程：角色定妆图 → 分镜图片（嵌入定妆图作为 visual anchor）
"""
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

MINIMAX_IMAGE_URL = "https://api.minimaxi.com/v1/image_generation"


class MiniMaxImageService:
    """MiniMax image-01 图片生成客户端"""

    def __init__(self):
        self.api_key = settings.minimax_api_key
        self.model = settings.image_model
        self.aspect_ratio = settings.image_aspect_ratio
        self.media_dir = settings.media_dir

    async def generate_character_portrait(
        self, task_id: str, character_name: str, appearance_prompt: str
    ) -> tuple[str, str]:
        """
        生成角色定妆照（正面半身、清晰五官、完整服装）。
        返回 (本地文件路径, MiniMax原始URL)。
        原始URL用于后续分镜图生成时作为 subject_reference。
        """
        prompt = (
            f"{settings.image_style or ''}. "
            f"Character reference portrait of {character_name}: {appearance_prompt}. "
            "Front view, half-body, clean simple background, full outfit visible, "
            "clear facial features, neutral pose, character design sheet, high quality"
        )
        image_url = await self._generate(prompt)
        logger.info(f"[{task_id}] 角色定妆照已生成: {character_name}")

        local_path = await self._download_ref(task_id, character_name, image_url)
        return local_path, image_url

    async def generate_character_portrait_side(
        self, task_id: str, character_name: str, appearance_prompt: str
    ) -> tuple[str, str]:
        """
        生成角色侧面定妆照（侧面半身，保持与正面一致的服装和五官特征）。
        返回 (本地文件路径, MiniMax原始URL)。
        """
        prompt = (
            f"{settings.image_style or ''}. "
            f"Side profile portrait of {character_name}: {appearance_prompt}. "
            "Side view, half-body, same outfit and hairstyle as front view, "
            "clean simple background, clear facial profile, character design sheet, high quality"
        )
        image_url = await self._generate(prompt)
        logger.info(f"[{task_id}] 角色侧面定妆照已生成: {character_name}")

        # 保存为 {name}_side.png，缓存 URL 为 {name}_side.url
        local_path = await self._download_ref_side(task_id, character_name, image_url)
        return local_path, image_url

    async def generate_image(
        self,
        task_id: str,
        scene_number: int,
        prompt: str,
        ref_image_url: str | None = None,
    ) -> str:
        """
        为单个分镜生成图片。ref_image_url 为角色定妆照的 HTTPS URL，
        作为 subject_reference 传入确保角色一致性。
        """
        if not self.api_key:
            raise LLMAPIError("MiniMax API Key 未配置，请在 .env 中设置 MINIMAX_API_KEY")

        image_url = await self._generate(prompt, ref_image_url)
        logger.info(f"[{task_id}] MiniMax image-01 已生成 (分镜 {scene_number})")

        local_path = await self._download(task_id, scene_number, image_url)
        logger.info(f"[{task_id}] 图片已下载: {local_path}")
        return local_path

    async def _generate(self, prompt: str, ref_image_url: str | None = None) -> str:
        """调用 MiniMax image-01 API，返回图片 URL"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt[:1500],
            "aspect_ratio": self.aspect_ratio,
            "n": 1,
            "response_format": "url",
            "prompt_optimizer": True,
        }

        # 小云雀方案: subject_reference 角色定妆图作为 visual anchor
        if ref_image_url:
            payload["subject_reference"] = [{
                "type": "character",
                "image_file": ref_image_url,
            }]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(MINIMAX_IMAGE_URL, json=payload, headers=headers)
            data = resp.json()

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code") != 0:
            msg = base_resp.get("status_msg", str(resp.status_code))
            raise LLMAPIError(f"MiniMax image-01 生成失败: {msg}")

        urls = data.get("data", {}).get("image_urls", [])
        if not urls:
            raise LLMAPIError("MiniMax image-01 未返回图片 URL")
        return urls[0]

    async def _download_ref(
        self, task_id: str, character_name: str, image_url: str
    ) -> str:
        """下载角色正面定妆照到 media/{task_id}/characters/{name}.png"""
        output_dir = os.path.join(self.media_dir, task_id, "characters")
        os.makedirs(output_dir, exist_ok=True)
        safe_name = character_name.replace("/", "_").replace("\\", "_")[:50]
        filepath = os.path.join(output_dir, f"{safe_name}.png")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath

    async def _download_ref_side(
        self, task_id: str, character_name: str, image_url: str
    ) -> str:
        """下载角色侧面定妆照到 media/{task_id}/characters/{name}_side.png"""
        output_dir = os.path.join(self.media_dir, task_id, "characters")
        os.makedirs(output_dir, exist_ok=True)
        safe_name = character_name.replace("/", "_").replace("\\", "_")[:50]
        filepath = os.path.join(output_dir, f"{safe_name}_side.png")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath

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
minimax_image_service = MiniMaxImageService()
