"""
进程内事件总线（pub/sub）—— 用于 SSE 状态推送。
单进程场景够用；多 worker / 多实例部署时需替换为 Redis pub/sub。
"""
import asyncio


class EventBus:
    def __init__(self):
        self._task_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._global_subscribers: list[asyncio.Queue] = []

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """订阅单个任务的事件，返回队列"""
        q = asyncio.Queue(maxsize=100)
        self._task_subscribers.setdefault(task_id, []).append(q)
        return q

    def subscribe_global(self) -> asyncio.Queue:
        """订阅全局事件（所有任务）"""
        q = asyncio.Queue(maxsize=500)
        self._global_subscribers.append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        subs = self._task_subscribers.get(task_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                self._task_subscribers.pop(task_id, None)

    def unsubscribe_global(self, q: asyncio.Queue) -> None:
        if q in self._global_subscribers:
            self._global_subscribers.remove(q)

    def publish(self, task_id: str, event_type: str, data: dict) -> None:
        """发布事件：推给该任务的订阅者 + 全局订阅者"""
        event = {"type": event_type, "task_id": task_id, "data": data}
        for q in list(self._task_subscribers.get(task_id, [])):
            self._put(q, event)
        for q in list(self._global_subscribers):
            self._put(q, event)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # 订阅者消费太慢，丢弃最旧事件防止积压
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass


# 全局单例
event_bus = EventBus()
