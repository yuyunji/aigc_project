"""
自定义异常 & 全局异常处理注册
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class TaskNotFoundException(Exception):
    """任务不存在"""
    def __init__(self, task_id: str):
        self.task_id = task_id


class LLMAPIError(Exception):
    """大模型 API 调用失败"""
    def __init__(self, message: str):
        self.message = message


class TokenLimitError(LLMAPIError):
    """Token 超限"""
    pass


class TaskTimeoutError(Exception):
    """任务执行超时"""
    def __init__(self, task_id: str, stage: str = ""):
        self.task_id = task_id
        self.stage = stage


class InputTooLargeError(Exception):
    """输入文本超过限制"""
    def __init__(self, actual: int, limit: int):
        self.actual = actual
        self.limit = limit


class EmptyChunksError(Exception):
    """文本分片后无有效内容"""
    pass


def register_exception_handlers(app: FastAPI) -> None:
    """
    注册全局异常处理 handler
    统一返回 JSON 格式错误信息
    """
    @app.exception_handler(TaskNotFoundException)
    async def handle_task_not_found(request: Request, exc: TaskNotFoundException):
        return JSONResponse(
            status_code=404,
            content={"detail": f"任务 {exc.task_id} 不存在"}
        )

    @app.exception_handler(LLMAPIError)
    async def handle_llm_error(request: Request, exc: LLMAPIError):
        return JSONResponse(
            status_code=502,
            content={"detail": f"AI 服务调用失败: {exc.message}"}
        )

    @app.exception_handler(TokenLimitError)
    async def handle_token_limit(request: Request, exc: TokenLimitError):
        return JSONResponse(
            status_code=400,
            content={"detail": f"输入文本过长，超出Token限制: {exc.message}"}
        )

    @app.exception_handler(TaskTimeoutError)
    async def handle_task_timeout(request: Request, exc: TaskTimeoutError):
        return JSONResponse(
            status_code=504,
            content={"detail": f"任务处理超时: 任务={exc.task_id}, 阶段={exc.stage}"}
        )

    @app.exception_handler(InputTooLargeError)
    async def handle_input_too_large(request: Request, exc: InputTooLargeError):
        return JSONResponse(
            status_code=413,
            content={
                "detail": f"输入文本过长: {exc.actual} 字符（上限 {exc.limit} 字符）"
            }
        )
