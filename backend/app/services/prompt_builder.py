"""
Image Prompt 构建器 —— 用 Claude 将分镜描述翻译为英文 image prompt
"""
import logging
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

IMAGE_PROMPT_SYSTEM = """You are a professional AI image prompt engineer.
Given a storyboard scene description in Chinese, create a concise English image prompt
optimized for AI image generation models (like Wan-X-Turbo).

Rules:
1. Output ONLY the English prompt, no explanation, no markdown
2. Describe: scene setting, lighting, camera angle, key subjects, mood/atmosphere, color palette
3. Add quality keywords: "cinematic lighting, 4K, high quality, detailed"
4. Keep under 200 words
5. Use visual, descriptive language — avoid abstract concepts"""


class PromptBuilder:
    """分镜描述 → 英文 image prompt 翻译服务"""

    @staticmethod
    async def build_image_prompt(scene_description: str) -> str:
        """
        将分镜描述翻译为适合 AI 图片生成的英文 prompt。

        Args:
            scene_description: 分镜脚本描述（中文 Markdown）

        Returns:
            英文 image prompt 字符串
        """
        user_message = f"Create an English image generation prompt for this storyboard scene:\n\n{scene_description[:1500]}"

        try:
            result = await llm_service._call_llm(
                system_prompt=IMAGE_PROMPT_SYSTEM,
                user_message=user_message,
                max_tokens=300,
                temperature=0.5,
            )
            prompt = result.strip().strip('"').strip("'")
            logger.info(f"Image prompt built: {prompt[:80]}...")
            return prompt
        except Exception as e:
            logger.warning(f"Prompt build failed, using fallback: {e}")
            # 兜底：直接用中文描述
            return (
                f"Cinematic scene, {scene_description[:200]}, "
                f"4K, high quality, cinematic lighting, detailed"
            )


# 全局单例
prompt_builder = PromptBuilder()
