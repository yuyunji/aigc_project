"""
应用配置 —— 从 .env 文件读取，通过 pydantic-settings 管理
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude API
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-20250915"
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 2.0
    llm_call_timeout: int = 120
    task_total_timeout: int = 600

    # 服务
    host: str = "127.0.0.1"
    port: int = 8000

    # 数据库
    database_url: str = "sqlite:///./aigc_workbench.db"

    # 文本处理
    max_chunk_size: int = 8000
    chunk_overlap: int = 200
    max_input_chars: int = 200_000
    max_chunks_for_llm: int = 3

    # 上传
    upload_dir: str = "uploads"
    max_upload_size: int = 10 * 1024 * 1024

    # ── DashScope (Wan-X-Turbo 分镜图片生成) ──
    dashscope_api_key: str = ""
    dashscope_image_model: str = "wanx2.1-t2i-turbo"
    dashscope_image_size: str = "1280*720"
    dashscope_poll_interval: int = 3      # 轮询间隔（秒）
    dashscope_poll_max_retries: int = 40  # 最大轮询次数（约 2 分钟）

    # ── fal.ai (Seedance 1.5 Pro 图生视频) ──
    fal_key: str = ""
    fal_video_model: str = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"
    fal_video_duration: int = 5           # 视频时长（秒）
    fal_video_resolution: str = "720p"
    fal_poll_interval: int = 5
    fal_poll_max_retries: int = 60        # 约 5 分钟

    # ── Volcengine TTS (火山引擎角色配音) ──
    volc_access_key: str = ""
    volc_secret_key: str = ""
    volc_tts_voice_type: str = "BV700_streaming"  # 默认音色
    volc_tts_encoding: str = "mp3"

    # ── FFmpeg 字幕拼接 ──
    ffmpeg_path: str = "ffmpeg"
    media_dir: str = "media"
    auto_media_pipeline: bool = True  # 是否自动执行媒体链路（可关闭）

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局单例配置
settings = Settings()

# 确保运行时目录存在
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.media_dir, exist_ok=True)
