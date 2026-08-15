"""
SSE 状态推送端点
GET /api/tasks/{task_id}/events —— 单任务事件流（分镜图/视频/资产状态）
GET /api/events —— 全局事件流（任务列表状态）
"""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.events import event_bus

router = APIRouter(tags=["实时事件"])

_HEARTBEAT_INTERVAL = 30  # 秒，无事件时发心跳防中间层断连

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 防止反向代理缓冲
}


def _format_sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def event_generator():
        q = event_bus.subscribe(task_id)
        try:
            yield _format_sse("connected", {"task_id": task_id})
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                    yield _format_sse(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            event_bus.unsubscribe(task_id, q)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.get("/events")
async def global_events():
    async def event_generator():
        q = event_bus.subscribe_global()
        try:
            yield _format_sse("connected", {"scope": "global"})
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                    yield _format_sse(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            event_bus.unsubscribe_global(q)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS
    )
