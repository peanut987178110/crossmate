"""CrossMate API v0.2 —— 应用入口。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import API_PREFIX
from app.core.http import ApiError, handler, middleware
from app.core.http import request_id_ctx as _rid
from app.routers import ai, auth, discover, misc, rooms, users

app = FastAPI(title="CrossMate API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 演示用；生产按域名白名单
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(middleware)
app.add_exception_handler(ApiError, handler)  # type: ignore[arg-type]


@app.exception_handler(RequestValidationError)
async def _validation(request, exc):  # noqa: ANN001
    """Pydantic 校验失败 -> 统一 400 参数错误格式。"""
    return JSONResponse(status_code=400, content={
        "code": 40000, "message": "invalid parameters",
        "data": {"errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()][:5]},
        "request_id": _rid.get(),
    })

for r in (auth.router, users.router, discover.router, rooms.router, ai.router, misc.router):
    app.include_router(r, prefix=API_PREFIX)


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": "0.2.0"}


@app.get("/api/v0.2/meta")
def meta():
    from app.core.config import AI_MODE, PRIORITY_LIKE_DAILY_QUOTA, RECOMMEND_WEIGHTS, REWARD_RULES

    return {"code": 0, "message": "ok", "request_id": "-", "data": {
        "ai_mode": AI_MODE,
        "priority_like_daily_quota": PRIORITY_LIKE_DAILY_QUOTA,
        "recommend_weights": RECOMMEND_WEIGHTS,
        "reward_rules": {k: {"points": v[0], "desc": v[1]} for k, v in REWARD_RULES.items()},
        "notice": "Prototype numbers are placeholders. All recommendations are explainable.",
    }}


@app.on_event("startup")
def _startup() -> None:
    """首次启动写入演示数据（可用 CROSSMATE_SEED=0 关闭）。"""
    import os as _os

    if _os.getenv("CROSSMATE_SEED", "1") == "1":
        from app.seed import seed

        seed()


# 安卓壳里源码在只读的 APK assets 中，前端文件由原生侧解压到可写目录后通过环境变量指定。
WEB_DIR = Path(os.getenv("CROSSMATE_WEB_DIR") or (Path(__file__).resolve().parents[2] / "web"))
if WEB_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/app/index.html")


# 便于 `python -m app.main` 启动；也支持 uvicorn app.main:app
if __name__ == "__main__":
    import uvicorn

    # 默认只听本机；手机 / 模拟器联调用 HOST=0.0.0.0
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")), reload=False)