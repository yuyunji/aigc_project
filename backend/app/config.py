"""
应用配置 —— 从 .env 文件读取，通过 pydantic-settings 管理
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM 通用配置 ──
    llm_provider: str = "deepseek"              # anthropic | deepseek
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = "claude-sonnet-5-20250915"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
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

    # ── MiniMax-H3 (文生视频，含内置音频) ──
    minimax_api_key: str = ""
    minimax_model: str = "MiniMax-H3"
    minimax_video_duration: int = 6       # 每分镜视频时长（秒，4-15）
    minimax_video_ratio: str = "16:9"
    minimax_video_resolution: str = "2K"
    minimax_poll_interval: int = 5        # 轮询间隔（秒）
    minimax_poll_max_retries: int = 60    # 约 5 分钟

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
