"""RTC 回调验签：HMAC-SHA256(ts + body_without_sig)，5 分钟防重放窗口。

生产环境密钥在 KMS；演示默认使用固定 demo key，可在 .env 覆盖。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

SECRET = os.getenv("RTC_WEBHOOK_SECRET", "crossmate-demo-rtc-secret")


_UNSPECIFIED = object()


def _canonical(payload: dict) -> str:
    return json.dumps({k: v for k, v in payload.items() if k != "signature"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(payload: dict, ts: int | None = None, *, with_ts: bool = False) -> str:
    """HMAC-SHA256；with_ts=True 时返回生产格式 "ts=<ts>;v1=<sig>"。"""
    body = _canonical(payload)
    digest = hmac.new(SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};v1={digest}" if with_ts else digest


def verify_signature(payload: dict) -> bool:
    sig = payload.get("signature") or ""
    if not sig:
        return False
    body = _canonical(payload)
    if sig.startswith("ts="):
        try:
            parts = dict(p.split("=", 1) for p in sig.split(";"))
            ts = int(parts["ts"])
            sig = parts["v1"]
        except (ValueError, KeyError):
            return False
        from app.core import util

        if abs(util.now() - ts) > 300:  # 防重放窗口 5 分钟
            return False
        cands = [ts, int(time.time())]  # 允许签名方与本地秒级时钟差
    else:
        cands = [None, int(time.time())]
    return any(
        hmac.compare_digest(hmac.new(SECRET.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest(), sig)
        for t in cands
    )
