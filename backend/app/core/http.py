"""HTTP 层：统一错误、请求上下文、响应包装。

统一返回体：{"code":0,"message":"ok","data":...,"request_id":"..."}
错误：code 非 0，HTTP 状态语义化（400/401/403/404/409/422/429/503）。
"""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

log = logging.getLogger("crossmate")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


class ApiError(Exception):
    def __init__(self, status: int, code: int, message: str, data: Any = None):
        self.status = status
        self.code = code
        self.message = message
        self.data = data


def err(status: int, code: int, message: str, data: Any = None) -> ApiError:
    return ApiError(status, code, message, data)


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data, "request_id": request_id_ctx.get()}


async def handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
            "request_id": request_id_ctx.get(),
        },
    )


ERROR_CODES = {
    400: 40000,  # 参数错误
    401: 40100,  # 未认证 / Token 失效
    403: 40300,  # 无权限（对象级）
    404: 40400,  # 不存在
    409: 40900,  # 冲突（额度、重复、状态机不允许）
    422: 42200,  # 风险拦截
    429: 42900,  # 限流
    503: 50300,  # 依赖不可用
}


async def middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    request_id_ctx.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except ApiError as exc:  # 兼容在中间件外抛出的情况
        response = await handler(request, exc)
    except Exception:  # noqa: BLE001
        log.exception("unhandled error path=%s", request.url.path)
        response = JSONResponse(
            status_code=500,
            content={"code": 50000, "message": "internal error", "data": None, "request_id": rid},
        )
    response.headers["X-Request-Id"] = rid
    # 访问日志：不含 Token / 邮箱 / 聊天正文 / Prompt（TC36）
    log.info(
        "%s %s %s rid=%s %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        rid,
        (time.perf_counter() - started) * 1000,
    )
    return response