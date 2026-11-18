"""积分流水与兑换（FR11）。

铁律：
- 积分不影响推荐排序，不兑换现金；兑换物只有主题/头像框；
- 举报、登录、停留房间、刷消息、重点喜欢、邀请 一律不发积分；
- 流水幂等键 (user_id, reward_code, source_id)；
- 观察期：grant 先写 pending，到期且无拉黑/违规再 granted，否则 rejected/reversed；
- UTC 周上限 100 分。
"""
from __future__ import annotations

import time

from app.core import db, util
from app.core.config import (
    QUALITY_FEEDBACK_WEEKLY_LIMIT,
    REDEMPTION_ITEMS,
    REWARD_RULES,
    WEEKLY_POINTS_CAP,
)
from app.core.http import err

# 明确“不奖励”的动作在业务入口直接不调用 grant，此处仅做文档式白名单。
NEVER_REWARD = {"report", "login", "room_stay", "message_spam", "priority_like", "invite"}


def _week_key(ts: float) -> str:
    y, w, _ = time.gmtime(ts)[:3]
    return f"{y}-W{time.strftime('%U', time.gmtime(ts))}"


def granted_this_week(user_id: str) -> int:
    start = util.now() - 7 * 86400
    rows = db.q(
        "SELECT COALESCE(SUM(points),0) AS s FROM rewards_ledger WHERE user_id=? AND status IN ('granted','pending') AND created_at>=?",
        (user_id, start),
    )
    return int(rows[0]["s"])


def _notify(user_id: str, ntype: str, title: str, body: str, ref_id: str | None) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO notifications(id,user_id,type,title,body,ref_id,read,created_at) VALUES(?,?,?,?,?,?,0,?)",
            (util.new_id("n"), user_id, ntype, title, body, ref_id, util.now()),
        )


def grant(user_id: str, reward_code: str, source_id: str, reason: str | None = None) -> dict:
    """幂等地写入一条奖励流水。观察期>0 时落 pending。"""
    if reward_code not in REWARD_RULES:
        raise ValueError(f"unknown reward {reward_code}")
    points, desc, lifetime, observe_h = REWARD_RULES[reward_code]
    existing = db.q1(
        "SELECT * FROM rewards_ledger WHERE user_id=? AND reward_code=? AND source_id=?",
        (user_id, reward_code, source_id),
    )
    if existing:
        return {"created": False, "ledger": dict(existing)}
    if lifetime is not None:
        count = db.q1(
            "SELECT COUNT(*) AS n FROM rewards_ledger WHERE user_id=? AND reward_code=?",
            (user_id, reward_code),
        )["n"]
        if count >= lifetime:
            return {"created": False, "reason": "lifetime_limit"}
    if reward_code == "QUALITY_FEEDBACK":
        n = db.q1(
            "SELECT COUNT(*) AS n FROM rewards_ledger WHERE user_id=? AND reward_code=? AND created_at>=?",
            (user_id, reward_code, util.now() - 7 * 86400),
        )["n"]
        if n >= QUALITY_FEEDBACK_WEEKLY_LIMIT:
            return {"created": False, "reason": "weekly_feedback_limit"}
    if granted_this_week(user_id) + points > WEEKLY_POINTS_CAP:
        return {"created": False, "reason": "weekly_cap"}
    status = "pending" if observe_h > 0 else "granted"
    lid = util.new_id("rw")
    t = util.now()
    with db.tx() as c:
        c.execute(
            """INSERT INTO rewards_ledger(id,user_id,reward_code,source_id,points,status,reason,created_at,settled_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (lid, user_id, reward_code, source_id, points, status, reason or desc, t,
             t if status == "granted" else None),
        )
    if status == "granted":
        _notify(user_id, "reward", f"You earned {points} points", f"You earned {points} points: {reason or desc}.", lid)
        util.track(user_id, "reward_granted", reward_code=reward_code, points=points)
    else:
        _notify(user_id, "reward", f"{points} points pending review", f"Your {reason or desc} reward is under review ({observe_h}h).", lid)
    return {"created": True, "ledger_id": lid, "status": status, "points": points}


def settle_due() -> int:
    """结算到期 pending：无拉黑/违规 -> granted，否则 rejected。"""
    pending = db.q(
        "SELECT * FROM rewards_ledger WHERE status='pending' AND created_at <= ?",
        (util.now() - 86400,),
    )
    n = 0
    for r in pending:
        if r["reward_code"] == "PROFILE_COMPLETE":
            new = "granted"
        else:
            bad = db.q1(
                "SELECT 1 FROM reports WHERE (reporter_id=? OR target_user_id=?) AND created_at>=? AND risk_level IN ('high','medium')",
                (r["user_id"], r["user_id"], r["created_at"]),
            )
            new = "rejected" if bad else "granted"
        with db.tx() as c:
            c.execute("UPDATE rewards_ledger SET status=?, settled_at=? WHERE id=?", (new, util.now(), r["id"]))
        if new == "granted":
            _notify(r["user_id"], "reward", f"You earned {r['points']} points",
                    f"You earned {r['points']} points: {r['reason']}.", r["id"])
            util.track(r["user_id"], "reward_granted", reward_code=r["reward_code"], points=r["points"])
        n += 1
    return n


def reverse_for_user(user_id: str, why: str) -> None:
    """处罚回滚：把该用户 granted/pending 的流水标 reversed。"""
    with db.tx() as c:
        c.execute("UPDATE rewards_ledger SET status='reversed', settled_at=? WHERE user_id=? AND status IN ('granted','pending')",
                  (util.now(), user_id))
    util.audit(None, "reward.reversed", user_id, why=why)


def balance(user_id: str) -> int:
    earned = db.q1("SELECT COALESCE(SUM(points),0) AS s FROM rewards_ledger WHERE user_id=? AND status='granted'", (user_id,))["s"]
    spent = db.q1("SELECT COALESCE(SUM(cost),0) AS s FROM redemptions WHERE user_id=?", (user_id,))["s"]
    return int(earned) - int(spent)


def maybe_first_valid_interaction(match_id: str) -> dict | None:
    """24h 内双方各 ≥2 条成功送达，且无拉黑 / 无中高风险举报 -> 双方各 +30（观察 24h）。

    TC29：只数“成功送达”的消息，刷消息（同一条重复 client_message_id）不会被计数，
    单向刷屏也不会满足“双方各 ≥2 条”。
    """
    m = db.q1("SELECT * FROM matches WHERE id=?", (match_id,))
    if not m or m["status"] != "active":
        return None
    now = util.now()
    since = now - 86400
    counts = {u: db.q1(
        "SELECT COUNT(*) AS n FROM messages WHERE match_id=? AND sender_id=? AND status='delivered' AND created_at>=?",
        (match_id, u, since))["n"] for u in (m["user_a"], m["user_b"])}
    if min(counts.values()) < 2:
        return None
    if db.q1("SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
             (m["user_a"], m["user_b"], m["user_b"], m["user_a"])):
        return {"granted": False, "reason": "blocked"}
    bad = db.q1(
        "SELECT 1 FROM reports WHERE created_at>=? AND risk_level IN ('high','medium') AND target_user_id IN (?,?)",
        (since, m["user_a"], m["user_b"]),
    )
    if bad:
        return {"granted": False, "reason": "violation"}
    out = []
    for u in (m["user_a"], m["user_b"]):
        out.append(grant(u, "FIRST_VALID_INTERACTION", source_id=match_id, reason="First valid interaction"))
    return {"granted": True, "detail": out}


def redeem(user_id: str, item_id: str) -> dict:
    item = REDEMPTION_ITEMS.get(item_id)
    if not item:
        raise err(404, 40402, "item not found")
    bal = balance(user_id)
    if bal < item["cost"]:
        raise err(409, 40920, "insufficient points", {"balance": bal, "cost": item["cost"]})
    rid = util.new_id("rd")
    with db.tx() as c:
        c.execute("INSERT INTO redemptions(id,user_id,item_id,cost,created_at) VALUES(?,?,?,?,?)",
                  (rid, user_id, item_id, item["cost"], util.now()))
    util.track(user_id, "redemption_created", item_id=item_id, cost=item["cost"])
    return {"redemption_id": rid, "item": item, "balance": balance(user_id)}