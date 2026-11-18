"""用户、资料、隐私、屏蔽、删除账号。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.config import (
    BIO_MAX,
    INTERESTS_MAX,
    MIN_INTERESTS,
    NICKNAME_MAX,
    SOCIAL_GOALS,
    VISIBILITY_CHOICES,
)
from app.core.http import err, ok
from app.routers.auth import current_user

router = APIRouter()


def me(authorization: str = Header(default="", alias="Authorization")) -> dict:
    return current_user(authorization)


class ProfileIn(BaseModel):
    nickname: str | None = Field(default=None, max_length=NICKNAME_MAX)
    country: str | None = None
    timezone: str | None = None
    languages: list[str] | None = None
    social_goals: list[str] | None = None
    interests: list[str] | None = None
    bio: str | None = Field(default=None, max_length=BIO_MAX)


class PrivacyIn(BaseModel):
    country_visibility: str | None = None
    interests_visibility: str | None = None
    timezone_visibility: str | None = None


class BlockIn(BaseModel):
    user_id: str


class DeletionIn(BaseModel):
    confirm: bool = False


def profile_strength(u: dict) -> int:
    """资料强度 0-100。字段权重写在这里，前端与 PRD 引用同一口径。"""
    parts = [
        (25, bool(u.get("nickname"))),
        (15, bool(u.get("country"))),
        (10, bool(u.get("timezone"))),
        (20, len(u.get("languages") or []) >= 1),
        (10, bool(u.get("social_goals"))),
        (15, len(u.get("interests") or []) >= MIN_INTERESTS),
        (5, bool(u.get("bio"))),
    ]
    return sum(w for w, okk in parts if okk)


def public_view(u: dict, viewer_id: str | None, privacy: dict | None = None) -> dict:
    """按可见性裁剪后的对外资料。TC05：private 字段不进入 Discover。"""
    p = privacy or {}
    cv = p.get("country_visibility", "public")
    iv = p.get("interests_visibility", "public")
    tv = p.get("timezone_visibility", "matches_only")
    matched = _is_matched(viewer_id, u["id"]) if viewer_id else False

    def visible(level: str) -> bool:
        if level == "public":
            return True
        if level == "matches_only":
            return matched
        return False

    out = {"user_id": u["id"], "nickname": u["nickname"], "bio": u["bio"],
           "languages": util.loads(u["languages"], []) if isinstance(u["languages"], str) else u["languages"],
           "social_goals": util.loads(u["social_goals"], []) if isinstance(u["social_goals"], str) else u["social_goals"],
           "avatar_seed": u["avatar_seed"]}
    if visible(cv):
        out["country"] = u["country"]
    if visible(iv):
        out["interests"] = util.loads(u["interests"], []) if isinstance(u["interests"], str) else u["interests"]
    if visible(tv):
        out["timezone"] = u["timezone"]
    return out


def _is_matched(a: str | None, b: str) -> bool:
    if not a:
        return False
    pair = tuple(sorted([a, b]))
    row = db.q1("SELECT 1 FROM matches WHERE user_a=? AND user_b=? AND status='active'", pair)
    return bool(row)


def _privacy(uid: str) -> dict:
    row = db.q1("SELECT * FROM privacy WHERE user_id=?", (uid,))
    return dict(row) if row else {}


@router.get("/users/me/profile")
def get_profile(user: dict = Depends(me)):
    u = db.q1("SELECT * FROM users WHERE id=?", (user["id"],))
    d = dict(u)
    d.pop("email", None)  # Token/邮箱不返回给普通接口
    for k in ("interests", "languages", "social_goals"):
        d[k] = util.loads(d[k], [])
    d["profile_strength"] = profile_strength(d)
    return ok(d)


@router.patch("/users/me/profile")
def patch_profile(body: ProfileIn, user: dict = Depends(me)):
    cur = db.q1("SELECT * FROM users WHERE id=?", (user["id"],))
    if cur["status"] != "active":
        raise err(403, 40312, "account not active")
    sets: dict = {}
    if body.nickname is not None:
        nick = body.nickname.strip()
        if not nick:
            raise err(400, 40010, "nickname required")
        sets["nickname"] = nick
    if body.country is not None:
        sets["country"] = body.country.strip()[:40] or None  # 用户确认，不取 IP
    if body.timezone is not None:
        sets["timezone"] = body.timezone.strip()[:64] or None
    if body.bio is not None:
        sets["bio"] = body.bio.strip()
    if body.languages is not None:
        sets["languages"] = util.dumps([x.strip() for x in body.languages if x.strip()][:5])
    if body.social_goals is not None:
        bad = set(body.social_goals) - SOCIAL_GOALS
        if bad:
            raise err(400, 40011, f"unknown social goal: {sorted(bad)}")
        sets["social_goals"] = util.dumps(body.social_goals)
    if body.interests is not None:
        items = list(dict.fromkeys(x.strip() for x in body.interests if x.strip()))
        if len(items) > INTERESTS_MAX:
            raise err(400, 40012, f"at most {INTERESTS_MAX} interests")
        sets["interests"] = util.dumps(items)
    merged = {**dict(cur), **sets}
    needed = merged["nickname"] and merged["country"] and merged["timezone"] and \
        util.loads(merged["languages"], []) and util.loads(merged["social_goals"], []) and \
        len(util.loads(merged["interests"], [])) >= MIN_INTERESTS
    sets["profile_completed"] = 1 if needed else 0
    cols = ",".join(f"{k}=?" for k in sets)
    with db.tx() as c:
        c.execute(f"UPDATE users SET {cols} WHERE id=?", (*sets.values(), user["id"]))
        if sets["profile_completed"] and not cur["profile_completed"]:
            c.execute("UPDATE users SET last_active_at=? WHERE id=?", (util.now(), user["id"]))
    util.track(user["id"], "profile_updated", completed=bool(sets["profile_completed"]))
    return get_profile(user)


@router.get("/users/me/privacy")
def get_privacy(user: dict = Depends(me)):
    return ok(_privacy(user["id"]))


@router.patch("/users/me/privacy")
def patch_privacy(body: PrivacyIn, user: dict = Depends(me)):
    p = _privacy(user["id"])
    sets = {}
    for f in ("country_visibility", "interests_visibility", "timezone_visibility"):
        v = getattr(body, f)
        if v is None:
            continue
        if v not in VISIBILITY_CHOICES:
            raise err(400, 40010, f"invalid visibility for {f}")
        if v != p.get(f):
            sets[f] = v
    if sets and any(f.startswith("interests") for f in sets) and p.get("interests_visibility") == "public":
        # TC32：关闭兴趣公开后，其他用户缓存失效。这里用 cache_epoch 通知下游。
        util.track(user["id"], "privacy_cache_invalidated", field="interests")
    if sets:
        cols = ",".join(f"{k}=?" for k in sets)
        with db.tx() as c:
            c.execute(f"UPDATE privacy SET {cols} WHERE user_id=?", (*sets.values(), user["id"]))
    util.audit(user["id"], "privacy.updated", user["id"], fields=list(sets))
    return ok(_privacy(user["id"]))


@router.get("/users/me/privacy/export")
def export_privacy(user: dict = Depends(me)):
    uid = user["id"]
    profile = dict(db.q1("SELECT * FROM users WHERE id=?", (uid,)))
    profile.pop("email")
    for k in ("interests", "languages", "social_goals"):
        profile[k] = util.loads(profile[k], [])
    return ok({
        "exported_at": util.now(),
        "profile": profile,
        "privacy": _privacy(uid),
        "matches": [dict(r) for r in db.q("SELECT id,user_a,user_b,status,created_at FROM matches WHERE user_a=? OR user_b=?", (uid, uid))],
        "messages": [dict(r) for r in db.q("SELECT id,match_id,sender_id,text,created_at FROM messages WHERE sender_id=?", (uid,))],
        "reports": [dict(r) for r in db.q("SELECT id,category,status,created_at FROM reports WHERE reporter_id=?", (uid,))],
        "rewards": [dict(r) for r in db.q("SELECT id,reward_code,points,status,created_at FROM rewards_ledger WHERE user_id=?", (uid,))],
    })


@router.post("/users/me/deletion")
def request_deletion(body: DeletionIn, user: dict = Depends(me)):
    if not body.confirm:
        raise err(400, 40014, "confirm required")
    uid = user["id"]
    with db.tx() as c:
        c.execute("UPDATE users SET status='deleting', deleted_at=? WHERE id=?", (util.now(), uid))
        c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (uid,))
        c.execute("UPDATE matches SET status='closed', closed_at=?, close_reason='account_deleted' "
                  "WHERE (user_a=? OR user_b=?) AND status='active'", (util.now(), uid, uid))
    util.track(uid, "account_deletion_requested")
    return ok({"status": "deleting", "grace_days": 7})


@router.post("/users/me/deletion/cancel")
def cancel_deletion(user: dict = Depends(me)):
    with db.tx() as c:
        c.execute("UPDATE users SET status='active', deleted_at=NULL WHERE id=?", (user["id"],))
    return ok({"status": "active"})


@router.post("/blocks")
def block(body: BlockIn, user: dict = Depends(me)):
    uid = user["id"]
    if body.user_id == uid:
        raise err(400, 40015, "cannot block yourself")
    if not db.q1("SELECT 1 FROM users WHERE id=? AND status='active'", (body.user_id,)):
        raise err(404, 40401, "user not found")
    a, b = sorted([uid, body.user_id])
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)",
                  (uid, body.user_id, util.now()))
        # FR05：拉黑立即解除消息、通话和房间私聊权限
        c.execute("UPDATE matches SET status='blocked', closed_at=?, close_reason='blocked' "
                  "WHERE user_a=? AND user_b=? AND status='active'", (util.now(), a, b))
        c.execute("UPDATE calls SET status='ended', ended_at=?, end_reason='blocked' WHERE status IN ('requested','ringing','connected','reconnecting') AND ((caller_id=? AND callee_id=?) OR (caller_id=? AND callee_id=?))",
                  (util.now(), uid, body.user_id, body.user_id, uid))
    util.track(uid, "user_blocked", target=body.user_id)
    return ok({"blocked": True})


@router.delete("/blocks/{user_id}")
def unblock(user_id: str, user: dict = Depends(me)):
    with db.tx() as c:
        cur = c.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (user["id"], user_id))
    return ok({"unblocked": cur.rowcount > 0})


@router.get("/blocks")
def list_blocks(user: dict = Depends(me)):
    rows = db.q("SELECT blocked_id, created_at FROM blocks WHERE blocker_id=? ORDER BY created_at DESC", (user["id"],))
    return ok([dict(r) for r in rows])


def blocked_between(a: str, b: str) -> bool:
    return bool(db.q1("SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                      (a, b, b, a)))


def settle_pending_rewards_due() -> None:
    """后台结算观察期到期的奖励（由 rewards 模块实现）。"""
    from app.services import rewards

    rewards.settle_due()


def now_utc_date(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts or util.now()))