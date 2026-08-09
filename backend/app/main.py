"""
FastAPI 应用入口
挂载路由、配置 CORS、注册异常处理、管理生命周期事件
"""
import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import upload, task, result, media
from app.services.task_queue import task_queue
from app.utils.exceptions import register_exception_handlers

# ── 日志配置 ──
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# 根 logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# 格式
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 控制台 handler（INFO 以上，UTF-8 编码）
sys.stdout.reconfigure(encoding="utf-8")
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.INFO)
console.setFormatter(formatter)
root_logger.addHandler(console)

# 全量日志文件（DEBUG 以上，轮转 5MB × 5 个）
all_log = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "app.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
all_log.setLevel(logging.DEBUG)
all_log.setFormatter(formatter)
root_logger.addHandler(all_log)

# 错误日志文件（WARNING 以上，单独记录）
err_log = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "error.log"),
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
err_log.setLevel(logging.WARNING)
err_log.setFormatter(formatter)
root_logger.addHandler(err_log)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 生命周期管理
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期上下文管理器：
    - 启动时：初始化数据库、启动任务队列 worker
    - 关闭时：优雅停止任务队列 worker
    """
    # ── 启动 ──
    logger.info("正在初始化数据库...")
    init_db()
    logger.info("数据库初始化完成")

    logger.info("正在启动任务队列 worker...")
    await task_queue.start()
    logger.info("服务启动完毕，等待请求")

    yield

    # ── 关闭 ──
    logger.info("正在停止任务队列 worker...")
    await task_queue.stop()
    logger.info("服务已关闭")


# ------------------------------------------------------------------
# 应用实例
# ------------------------------------------------------------------

app = FastAPI(
    title="AIGC短剧工作台",
    description="个人求职Demo项目 - AI辅助短剧剧本生成",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置 —— 开发阶段允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(upload.router, prefix="/api")
app.include_router(task.router, prefix="/api")
app.include_router(result.router, prefix="/api")
app.include_router(media.router, prefix="/api")

# 注册全局异常 handler
register_exception_handlers(app)


@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "service": "AIGC短剧工作台"}


# 静态文件服务 + SPA fallback（前端构建产物 + 媒体文件）
media_dir = os.path.abspath(settings.media_dir)
os.makedirs("static", exist_ok=True)
os.makedirs(media_dir, exist_ok=True)

STATIC_DIR = os.path.abspath("static")
SPA_INDEX = os.path.join(STATIC_DIR, "index.html")

if os.path.isfile(SPA_INDEX):
    # 先挂载 /media（避免被 SPA fallback 拦截）
    app.mount("/media", StaticFiles(directory=media_dir), name="media")

    # 静态资源走文件系统
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    # 其他所有路径 → index.html（SPA router 处理）
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        if os.path.isfile(SPA_INDEX):
            return FileResponse(SPA_INDEX)
        raise HTTPException(status_code=404, detail="Not Found")
else:
    app.mount("/media", StaticFiles(directory=media_dir), name="media")
