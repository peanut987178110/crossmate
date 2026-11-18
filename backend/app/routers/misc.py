"""通知、积分、举报 API。"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.config import API_PREFIX, REDEMPTION_ITEMS, REWARD_RULES
from app.core.http import err, ok
from app.routers.auth import current_user
from app.services import rewards

router = APIRouter()
REPORT_CATEGORIES = {
    "harassment", "fraud", "hate", "minor_risk", "privacy_request", "other",
}


def me(authorization: str = Header(default="", alias="Authorization")) -> dict:
    return current_user(authorization)


# ---------------- notifications ----------------
@router.get("/notifications")
def list_notifications(unread_only: bool = False, user: dict = Depends(me)):
    sql = "SELECT * FROM notifications WHERE user_id=?"
    args: list = [user["id"]]
    if unread_only:
        sql += " AND read=0"
    rows = db.q(sql + " ORDER BY created_at DESC LIMIT 100", args)
    unread = db.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", (user["id"],))["n"]
    return ok({"items": [dict(r) for r in rows], "unread": unread})


class ReadIn(BaseModel):
    ids: list[str] | None = None  # None => 全部已读


@router.post("/notifications/read")
def mark_read(body: ReadIn, user: dict = Depends(me)):
    with db.tx() as c:
        if body.ids:
            marks = ",".join("?" * len(body.ids))
            c.execute(f"UPDATE notifications SET read=1 WHERE user_id=? AND id IN ({marks})",
                      (user["id"], *body.ids))
        else:
            c.execute("UPDATE notifications SET read=1 WHERE user_id=? AND read=0", (user["id"],))
    for _ in (body.ids or [None]):  # 埋点一次即可
        pass
    util.track(user["id"], "notification_read", scope="partial" if body.ids else "all")
    return ok({"ok": True})  # TC37：幂等


# ---------------- rewards ----------------
@router.get("/rewards/ledger")
def ledger(user: dict = Depends(me)):
    rows = db.q("SELECT * FROM rewards_ledger WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    reds = db.q("SELECT * FROM redemptions WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    return ok({
        "balance": rewards.balance(user["id"]),
        "weekly_granted": rewards.granted_this_week(user["id"]),
        "items": [dict(r) for r in rows],
        "redemptions": [dict(r) for r in reds],
        "catalog": REDEMPTION_ITEMS,
        "rules": {k: {"points": v[0], "desc": v[1], "lifetime": v[2], "observe_hours": v[3]} for k, v in REWARD_RULES.items()},
    })


class RedeemIn(BaseModel):
    item_id: str


@router.post("/rewards/redemptions")
def redeem(body: RedeemIn, user: dict = Depends(me)):
    return ok(rewards.redeem(user["id"], body.item_id))


# ---------------- reports ----------------
class ReportIn(BaseModel):
    target_user_id: str
    target_type: str = "user"  # user | match | message | room | call
    target_id: str | None = None
    category: str
    detail: str = Field(default="", max_length=1000)
    also_block: bool = True


RISK_MAP = {
    "minor_risk": ("high", "safety_queue"),
    "fraud": ("high", "trust_safety_queue"),
    "harassment": ("medium", "content_queue"),
    "hate": ("high", "content_queue"),
    "privacy_request": ("medium", "trust_safety_queue"),
    "other": ("low", "general_queue"),
}


@router.post("/reports")
def create_report(body: ReportIn, user: dict = Depends(me)):
    """FR12 + SAFETY_TRIAGE：先落证据 -> 可选拉黑 -> 分流人工。举报永不发积分（TC31）。"""
    if body.category not in REPORT_CATEGORIES:
        raise err(400, 40040, "invalid category")
    if body.target_user_id == user["id"]:
        raise err(400, 40041, "cannot report yourself")
    risk, queue = RISK_MAP[body.category]
    rid = util.new_id("rp")
    evidence = util.new_id("ev")
    redacted = {
        "category": body.category,
        # 脱敏：仅保留类别与长度，不保存聊天原文（TC36）
        "detail_len": len(body.detail),
        "target_type": body.target_type,
    }
    t = util.now()
    with db.tx() as c:
        c.execute(
            """INSERT INTO reports(id,reporter_id,target_user_id,target_type,target_id,category,detail,also_block,
               status,risk_level,queue,evidence_id,created_at) VALUES(?,?,?,?,?,?,?,?, 'submitted',?,?,?,?)""",
            (rid, user["id"], body.target_user_id, body.target_type, body.target_id, body.category,
             body.detail, 1 if body.also_block else 0, risk, queue, evidence, t),
        )
        if body.also_block:
            a, b = sorted([user["id"], body.target_user_id])
            c.execute("INSERT OR IGNORE INTO blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)",
                      (user["id"], body.target_user_id, t))
            c.execute("UPDATE matches SET status='blocked', closed_at=?, close_reason='blocked' "
                      "WHERE user_a=? AND user_b=? AND status='active'", (t, a, b))
    util.audit(user["id"], "report.submitted", rid, category=body.category, evidence=evidence, redacted=redacted)
    util.track(user["id"], "report_submitted", category=body.category, also_block=body.also_block)
    return ok({"report_id": rid, "status": "submitted", "risk_level": risk, "queue": queue,
               "message": f"Report received: {rid}."})


@router.get("/reports/{report_id}")
def get_report(report_id: str, user: dict = Depends(me)):
    r = db.q1("SELECT * FROM reports WHERE id=? AND reporter_id=?", (report_id, user["id"]))
    if not r:
        raise err(404, 40405, "report not found")
    d = dict(r)
    d.pop("detail", None)
    return ok(d)


# ---------------- 便于演示：运营视图（真实环境需管理员权限） ----------------
@router.get("/admin/stats")
def stats(authorization: str = Header(default="", alias="Authorization")):
    current_user(authorization)
    names = [r["name"] for r in db.q("SELECT DISTINCT name FROM events")]
    counts = {n: db.q1("SELECT COUNT(*) AS n FROM events WHERE name=?", (n,))["n"] for n in names}
    today = time.strftime("%Y-%m-%d", time.gmtime(util.now()))
    dau = db.q1(
        "SELECT COUNT(DISTINCT user_id) AS n FROM events WHERE created_at>=?",
        (util.now() - (util.now() % 86400),),
    )["n"]
    return ok({"date": today, "dau": dau, "event_counts": dict(sorted(counts.items()))})

# ---------------- 演示台：重置数据（仅在演示模式可用） ----------------
@router.post("/admin/reset")
def reset_demo():
    """重置演示数据。生产环境必须删除或加管理员鉴权 + 二次确认。"""
    from app.core.config import AI_MODE
    from app.seed import seed

    if AI_MODE != "mock" and os.getenv("CROSSMATE_ALLOW_RESET") != "1":
        raise err(403, 40320, "reset disabled in production")
    seed(reset=True)
    return ok({"ok": True})


# ---------------- E2E 脚本垫片：让 Playwright 在同一页面里切换账号 / 等待双击 ----------------
# 仅演示模式可用，用于自动化测试；生产构建应移除。
class E2EIn(BaseModel):
    action: str
    user: str | None = None
    token: str | None = None
    path: str | None = None
    method: str = "GET"
    body: dict | None = None
    ms: int = 0


def _e2e_token_for(key: str) -> str:
    from app.routers.auth import issue_session
    from app.seed import seed

    ids = seed()  # 幂等：确保演示账号存在
    if key not in ids:
        raise err(404, 40401, "unknown demo user")
    return issue_session(ids[key])["access_token"]


@router.post("/admin/e2e")
def e2e(action_body: E2EIn):
    from app.core.config import AI_MODE
    import asyncio

    if AI_MODE != "mock" and os.getenv("CROSSMATE_ALLOW_E2E") != "1":
        raise err(403, 40320, "e2e disabled")
    a = action_body
    if a.action == "token":
        return ok({"token": _e2e_token_for(a.user or ""), "expires_in": 7 * 86400})
    if a.action == "wait":
        import time as _t
        _t.sleep(max(0, min(a.ms, 15000)) / 1000)
        return ok({"waited": a.ms})
    if a.action == "call":
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app

        headers = {"Authorization": f"Bearer {a.token or ''}"} if a.token else {}
        tc = TestClient(fastapi_app)
        r = tc.request(a.method or "GET", API_PREFIX + (a.path or "/"), headers=headers, json=a.body if a.body is not None else None)
        return ok({"status": r.status_code, "json": r.json()})
    raise err(400, 40090, "unknown action")
