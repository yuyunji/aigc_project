"""
内存任务队列 —— 使用 asyncio.Queue 模拟消息队列
不持久化，程序重启后任务丢失（Demo 允许）
"""
import asyncio
import logging
from dataclasses import dataclass

from app.services.task_manager import task_manager

logger = logging.getLogger(__name__)


@dataclass
class QueueTask:
    """队列中的任务"""
    task_id: str
    source_text: str


class InMemoryTaskQueue:
    """
    基于 asyncio.Queue 的内存任务队列。
    单 worker 逐条消费，按 FIFO 顺序执行级联生成任务。

    生命周期：
    - start()   → 启动后台 worker
    - enqueue() → 任务入队
    - stop()    → 优雅关闭
    """

    def __init__(self):
        self._queue: asyncio.Queue[QueueTask] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._running = False

    async def enqueue(self, task_id: str, source_text: str) -> None:
        """
        将任务放入队列。非阻塞——调用方无需等待任务完成。

        Args:
            task_id:     数据库中的任务唯一 ID
            source_text: 用户提交的原始文本
        """
        qt = QueueTask(task_id=task_id, source_text=source_text)
        await self._queue.put(qt)
        logger.info(f"任务入队: {task_id}, 队列深度: {self._queue.qsize()}")

    async def start(self) -> None:
        """
        启动队列 worker 协程。
        在 FastAPI 启动事件中调用，开始消费队列。
        """
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("任务队列 worker 已启动")

    async def stop(self) -> None:
        """
        停止队列 worker。
        在 FastAPI 关闭事件中调用，等待当前任务完成后退出。
        """
        if not self._running:
            return
        self._running = False

        # 放入哨兵值唤醒 worker 让它退出
        await self._queue.put(None)  # type: ignore

        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=30)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                logger.warning("队列 worker 超时，已强制取消")
        logger.info("任务队列 worker 已停止")

    async def _worker_loop(self) -> None:
        """
        Worker 主循环：不断从队列取任务，调用 TaskManager 执行。
        哨兵值为 None 时退出。
        """
        while self._running:
            try:
                # 使用超时避免阻塞在 get() 上无法退出
                queue_task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if queue_task is None:
                # 哨兵信号，退出
                break

            logger.info(f"开始处理任务: {queue_task.task_id}")
            try:
                await task_manager.process_task(
                    task_id=queue_task.task_id,
                    source_text=queue_task.source_text
                )
            except Exception as e:
                # task_manager 内部已捕获并更新 DB 状态为 failed，
                # 此处兜底防止 worker 崩溃
                logger.exception(f"任务处理异常 (未在 manager 层捕获): {queue_task.task_id}: {e}")
            finally:
                self._queue.task_done()


# 全局单例队列
task_queue = InMemoryTaskQueue()
