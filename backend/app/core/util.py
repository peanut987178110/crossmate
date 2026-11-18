"""通用工具：ID、JSON 字段、事件埋点、统一时钟（支持测试时间加速）。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.core import db

_clock_offset: float = 0.0  # 测试用时间偏移（秒）


def now() -> float:
    return time.time() + _clock_offset


def advance(seconds: float) -> None:
    global _clock_offset
    _clock_offset += seconds


def reset_clock() -> None:
    global _clock_offset
    _clock_offset = 0.0


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def as_dict(model: Any) -> dict:
    """把 pydantic 模型转成 dict，同时兼容 v1 和 v2。

    v2: model.model_dump()  v1: model.dict()
    为什么要兼容两份：安卓壳用 Chaquopy 在手机上跑后端，而 pydantic v2 的
    核心（pydantic-core）是 Rust 扩展，没有 Android wheel，只能装纯 Python 的 v1。
    """
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    return model.dict()


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(s: str, default: Any = None) -> Any:
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


def track(user_id: str | None, name: str, **props: Any) -> None:
    """核心事件埋点，写入 events 表。事件名清单见 docs/04-指标与数据看板.md。"""
    with db.tx() as c:
        c.execute(
            "INSERT INTO events(user_id,name,props,created_at) VALUES(?,?,?,?)",
            (user_id, name, dumps(props), now()),
        )


def audit(actor: str | None, action: str, target: str | None = None, **meta: Any) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO audit_log(actor,action,target,meta,created_at) VALUES(?,?,?,?,?)",
            (actor, action, target, dumps(meta), now()),
        )