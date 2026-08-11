"""
GPT-Image-2 分镜图片生成服务 (OpenAI Images API)
支持两种模式：
  1. 单分镜图片生成（替代 MiniMax image-01）
  2. 导演流程图生成（所有分镜合成一张视觉规划图）
"""
import logging
import os
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"


class GptImageService:
    """GPT-Image-2 图片生成客户端"""

    def __init__(self):
        self.api_key = settings.openai_api_key
        self.base_url = settings.openai_base_url
        self.model = settings.openai_image_model
        self.size = settings.openai_image_size
        self.quality = settings.openai_image_quality
        self.media_dir = settings.media_dir

    # ------------------------------------------------------------------
    # 单分镜图片生成
    # ------------------------------------------------------------------

    async def generate_scene_image(
        self, task_id: str, scene_number: int, prompt: str
    ) -> tuple[str, str]:
        """
        为单个分镜生成图片。

        Returns:
            (本地图片文件路径, 远程HTTPS URL)
        """
        if not self.api_key:
            raise LLMAPIError("OpenAI API Key 未配置，请在 .env 中设置 OPENAI_API_KEY")

        image_url = await self._generate(prompt)
        logger.info(f"[{task_id}] GPT-Image-2 图片已生成 (分镜 {scene_number})")

        local_path = await self._download(task_id, scene_number, image_url)
        logger.info(f"[{task_id}] 图片已下载: {local_path}")
        return local_path, image_url

    # ------------------------------------------------------------------
    # 导演流程图生成（核心新功能）
    # ------------------------------------------------------------------

    async def generate_storyboard_flowchart(
        self, task_id: str, scene_list: list[dict],
        asset_refs: str = "", global_prefix: str = "",
    ) -> str:
        """
        生成导演流程图 —— 25 镜 5×5 宫格，强制日漫风格，参考资产拆解。

        Args:
            task_id:       任务 ID
            scene_list:    分镜列表
            asset_refs:    资产拆解参考（角色/场景/道具描述）
            global_prefix: 全局日漫风格前缀

        Returns:
            本地图片文件路径
        """
        if not self.api_key:
            raise LLMAPIError("OpenAI API Key 未配置，请在 .env 中设置 OPENAI_API_KEY")

        prompt = self._build_flowchart_prompt(scene_list, asset_refs, global_prefix)
        logger.info(
            f"[{task_id}] 导演流程图 prompt 已构建 ({len(prompt)} 字符, {len(scene_list)} 个分镜)"
        )

        # 流程图使用更宽的画幅
        image_url = await self._generate(prompt, size="1792x1024")
        logger.info(f"[{task_id}] GPT-Image-2 导演流程图已生成")

        local_path = await self._download_flowchart(task_id, image_url)
        logger.info(f"[{task_id}] 导演流程图已下载: {local_path}")
        return local_path

    def _build_flowchart_prompt(
        self, scene_list: list[dict], asset_refs: str = "", global_prefix: str = ""
    ) -> str:
        """
        将分镜列表编织为导演流程图 prompt。

        要求 GPT-Image-2 生成一张专业电影故事板，
        按时间线排列所有场景，标注场景编号、运镜方式和角色。
        """
        scene_descriptions = []
        for scene in scene_list:
            num = scene.get("scene_number", "?")
            title = scene.get("scene_title", "")
            camera = scene.get("camera_movement", "")
            chars = scene.get("characters_in_scene", "")
            visual = scene.get("visual_description", "") or scene.get("description", "")

            # 每个分镜压缩为极简关键词（25 镜宫格图）
            short_desc = ""
            if visual:
                short_desc = visual[:40]
            elif scene.get("subject"):
                short_desc = scene.get("subject", "")[:40]
            scene_descriptions.append(f"#{num}: {short_desc}")

        # 25 镜 5x5 宫格布局
        total = len(scene_list)
        joined = ", ".join(scene_descriptions)

        # 强制日漫风格前缀
        style = global_prefix[:300] if global_prefix else (
            "2D Japanese anime, cel-shaded, hand-drawn look, "
            "vibrant colors, clean linework, cinematic lighting"
        )

        # 资产参考
        asset_section = ""
        if asset_refs and asset_refs.strip():
            asset_section = f"\n\nDesign references (use these character/scene/prop designs):\n{asset_refs[:1200]}"

        return (
            f"REQUIRED STYLE: {style}. "
            f"Professional anime storyboard grid, 5x5 layout with {total} numbered panels. "
            f"Each panel shows a key visual moment with its scene number. "
            f"Consistent character designs across all panels, same art style throughout. "
            f"No watermarks, no text except scene numbers in corner.\n\n"
            f"Scenes: {joined[:800]}"
            f"{asset_section}"
        )[:3000]

    # ------------------------------------------------------------------
    # 底层 API 调用
    # ------------------------------------------------------------------

    async def _generate(self, prompt: str, size: str | None = None) -> str:
        """调用 OpenAI Images API，返回图片 URL"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": size or self.size,
            "quality": self.quality,
            "response_format": "url",
        }

        url = f"{self.base_url}/images/generations"
        logger.debug(f"GPT-Image-2 请求: {url} model={self.model} size={payload['size']} prompt_len={len(prompt)}")

        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(url, json=payload, headers=headers)
                data = resp.json()
        except Exception as e:
            logger.error(f"GPT-Image-2 网络错误: {e}")
            raise LLMAPIError(f"GPT-Image-2 请求失败: {e}")

        logger.debug(f"GPT-Image-2 响应: status={resp.status_code}")

        if resp.status_code not in (200, 201):
            error_msg = data.get("error", {}).get("message", str(resp.status_code))
            logger.error(f"GPT-Image-2 API 错误 (status={resp.status_code}): {error_msg}")
            logger.error(f"GPT-Image-2 完整响应: {str(data)[:500]}")
            raise LLMAPIError(f"GPT-Image-2 生成失败: {error_msg}")

        urls = data.get("data", [])
        if not urls:
            raise LLMAPIError("GPT-Image-2 未返回图片 URL")
        return urls[0].get("url", "")

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    async def _download(
        self, task_id: str, scene_number: int, image_url: str
    ) -> str:
        """下载图片到 media/{task_id}/images/"""
        output_dir = os.path.join(self.media_dir, task_id, "images")
        os.makedirs(output_dir, exist_ok=True)
        filename = f"scene_{scene_number:03d}_gpt.png"
        filepath = os.path.join(output_dir, filename)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath

    async def _download_flowchart(self, task_id: str, image_url: str) -> str:
        """下载导演流程图到 media/{task_id}/flowchart/"""
        output_dir = os.path.join(self.media_dir, task_id, "flowchart")
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, "storyboard_flowchart.png")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath


    async def _download_asset(
        self, task_id: str, asset_name: str, image_url: str
    ) -> str:
        """下载资产图片到 media/{task_id}/assets/"""
        import re
        safe_name = re.sub(r"[^\w一-鿿_-]", "_", asset_name)[:50]
        output_dir = os.path.join(self.media_dir, task_id, "assets")
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, f"{safe_name}.png")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath

    async def generate_asset_image(
        self, task_id: str, asset_name: str, prompt: str
    ) -> tuple[str, str]:
        """为资产生成参考图，返回 (local_path, remote_url)"""
        image_url = await self._generate(prompt, settings.openai_image_size)
        local_path = await self._download_asset(task_id, asset_name, image_url)
        logger.info(f"[{task_id}] 资产图片已生成: {asset_name}")
        return local_path, image_url


# 全局单例
gpt_image_service = GptImageService()
