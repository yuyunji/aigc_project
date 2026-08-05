"""
FastAPI 应用入口
挂载路由、配置 CORS、注册异常处理、管理生命周期事件
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import upload, task, result, media
from app.services.task_queue import task_queue
from app.utils.exceptions import register_exception_handlers

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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


# 静态文件服务（前端构建产物 + 媒体文件）—— 放在最后，避免拦截 API 路由
media_dir = os.path.abspath(settings.media_dir)
os.makedirs("static", exist_ok=True)
os.makedirs(media_dir, exist_ok=True)
if os.path.isdir("static") and os.listdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
app.mount("/media", StaticFiles(directory=media_dir), name="media")
