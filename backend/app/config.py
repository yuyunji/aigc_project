"""
应用配置 —— 从 .env 文件读取，通过 pydantic-settings 管理
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM 通用配置 ──
    llm_provider: str = "deepseek"              # anthropic | deepseek | doubao
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = "claude-sonnet-5-20250915"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = "doubao-seed-2-1-turbo-260628"
    llm_max_retries: int = 1          # 重试 1 次（共 2 次调用），动态镜数输出长，减少无效重试
    llm_retry_base_delay: float = 2.0
    llm_call_timeout: int = 600       # 单次 LLM 调用超时 10 分钟，动态镜数输出更长需要更多时间
    task_total_timeout: int = 1800    # 任务总超时 30 分钟

    # 服务
    host: str = "127.0.0.1"
    port: int = 8000

    # 数据库（MySQL）
    database_url: str = "mysql+pymysql://aigc:aigc_pass@127.0.0.1:3306/aigc_workbench?charset=utf8mb4"

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
    minimax_video_ratio: str = "16:9"     # 16:9 横屏 / 9:16 竖屏
    minimax_video_resolution: str = "2K"
    minimax_motion_strength: float = 0.3  # 运动强度 0.25-0.4，漫剧轻动态推荐值
    minimax_poll_interval: int = 10       # 轮询间隔（秒）
    minimax_poll_max_retries: int = 180   # 约 30 分钟

    # ── MiniMax image-01 (分镜图片生成，支持 subject_reference 角色一致性) ──
    image_model: str = "image-01"
    image_aspect_ratio: str = "16:9"
    image_style: str = ""  # 全局风格 prompt 前缀，如 "Japanese anime style, manga art"

    # ── 图片生成 Provider 选择 ──
    image_provider: str = "minimax"         # minimax | gpt-image-2

    # ── OpenAI GPT-Image-2 ──
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_image_model: str = "gpt-image-2"
    openai_image_size: str = "1792x1024"    # 16:9 宽幅
    openai_image_quality: str = "high"       # standard | high

    # ── 视频生成 Provider 选择 ──
    video_provider: str = "minimax-h3"      # minimax-h3 | comfyui（本地 MiniMax H3）

    # ── ComfyUI 本地 MiniMax H3 视频生成 ──
    comfyui_url: str = "http://127.0.0.1:8198"
    comfyui_poll_interval: int = 5        # 轮询间隔（秒）
    comfyui_poll_max_retries: int = 360   # 约 30 分钟

    # ── FFmpeg 字幕拼接 ──
    ffmpeg_path: str = "ffmpeg"
    media_dir: str = "media"
    auto_media_pipeline: bool = False  # 默认关闭自动媒体管线，改为手动逐分镜触发

    # ── 阿里云 OSS（私有 Bucket + 签名 URL）──
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_endpoint: str = ""      # 如 oss-cn-shanghai.aliyuncs.com
    oss_bucket: str = ""
    oss_url_expire: int = 3600  # 签名 URL 有效期（秒）

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略 .env 中未定义的多余字段


# 全局单例配置
settings = Settings()

# 确保运行时目录存在
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.media_dir, exist_ok=True)
