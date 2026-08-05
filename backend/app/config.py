"""
应用配置 —— 从 .env 文件读取，通过 pydantic-settings 管理
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude API
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-20250915"
    llm_max_retries: int = 2            # API 调用失败重试次数
    llm_retry_base_delay: float = 2.0   # 重试指数退避基数（秒）
    llm_call_timeout: int = 120         # 单次 LLM 调用超时（秒）
    task_total_timeout: int = 600       # 单个任务总超时（秒）

    # 服务
    host: str = "127.0.0.1"
    port: int = 8000

    # 数据库
    database_url: str = "sqlite:///./aigc_workbench.db"

    # 文本处理
    max_chunk_size: int = 8000          # 分片大小（字符）
    chunk_overlap: int = 200            # 分片重叠量（字符）
    max_input_chars: int = 200_000      # 允许的最大输入字符数
    max_chunks_for_llm: int = 3         # 送入 LLM 的最大分片数

    # 上传
    upload_dir: str = "uploads"
    max_upload_size: int = 10 * 1024 * 1024  # 10MB

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局单例配置
settings = Settings()

# 确保上传目录存在
os.makedirs(settings.upload_dir, exist_ok=True)
