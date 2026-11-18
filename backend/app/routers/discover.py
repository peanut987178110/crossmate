"""Discover / Likes / Passes / Matches / Messages（FR04–FR06）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.config import DISCOVER_PAGE_SIZE, PASS_COOLDOWN_HOURS, PRIORITY_LIKE_DAILY_QUOTA, TEXT_MAX, TEXT_MIN
from app.core.http import err, ok
from app.routers.auth import current_user
from app.routers.users import blocked_between
from app.services import recommend, rewards

router = APIRouter()


def me(authorization: str = Header(default="", alias="Authorization")) -> dict:
    return current_user(authorization)


class LikeIn(BaseModel):
    candidate_id: str
    type: str = "like"  # like | priority_like
    idempotency_key: str | None = None


class PassIn(BaseModel):
    candidate_id: str


class MessageIn(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=TEXT_MIN, max_length=TEXT_MAX)
    source_generation_id: str | None = None


def _require_active_profile(user: dict) -> None:
    if not user.get("profile_completed"):
        raise err(403, 40320, "complete your profile first")


def _pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted([a, b]))  # type: ignore[return-value]


def _create_match_if_reciprocal(a: str, b: str) -> dict | None:
    """TC08：并发双向喜欢只建一个 match —— 用 UNIQUE(user_a,user_b) + INSERT OR IGNORE。"""
    other = db.q1("SELECT * FROM likes WHERE from_id=? AND to_id=?", (b, a))
    if not other:
        return None
    if blocked_between(a, b):
        raise err(422, 42210, "cannot match with this user")
    ua, ub = _pair(a, b)
    mid = util.new_id("m")
    with db.tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO matches(id,user_a,user_b,status,created_at) VALUES(?,?,?, 'active', ?)",
            (mid, ua, ub, util.now()),
        )
        created = cur.rowcount == 1
    row = db.q1("SELECT * FROM matches WHERE user_a=? AND user_b=?", (ua, ub))
    if created:
        util.track(a, "match_created", match_id=row["id"], other=b)
        util.track(b, "match_created", match_id=row["id"], other=a)
        for u in (ua, ub):
            _notify(u, "match", "It's a match!", "You both chose to connect. Say hello!", row["id"])
            rewards.grant(u, "FIRST_MATCH", source_id=row["id"], reason="First real match")
    return dict(row)


def _notify(user_id: str, ntype: str, title: str, body: str, ref_id: str | None = None) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO notifications(id,user_id,type,title,body,ref_id,read,created_at) VALUES(?,?,?,?,?,?,0,?)",
            (util.new_id("n"), user_id, ntype, title, body, ref_id, util.now()),
        )


@router.get("/discover")
def get_discover(tab: str = "for_you", cursor: int = 0, user: dict = Depends(me)):
    _require_active_profile(user)
    if tab not in {"for_you", "language", "online"}:
        raise err(400, 40020, "invalid tab")
    items = recommend.discover(user, tab, DISCOVER_PAGE_SIZE, cursor)
    remaining = _priority_quota_left(user["id"])
    return ok({"items": items, "next_cursor": cursor + len(items),
               "priority_like_quota": {"limit": PRIORITY_LIKE_DAILY_QUOTA, "remaining": remaining}})


def _priority_quota_left(uid: str) -> int:
    today = util.now() - (util.now() % 86400)
    n = db.q1("SELECT COUNT(*) AS n FROM likes WHERE from_id=? AND type='priority_like' AND created_at>=?",
              (uid, today))["n"]
    return max(0, PRIORITY_LIKE_DAILY_QUOTA - n)


@router.post("/likes")
def create_like(body: LikeIn, user: dict = Depends(me)):
    _require_active_profile(user)
    uid = user["id"]
    if body.candidate_id == uid:
        raise err(400, 40021, "cannot like yourself")
    if body.type not in {"like", "priority_like"}:
        raise err(400, 40022, "invalid like type")
    if not db.q1("SELECT 1 FROM users WHERE id=? AND status='active'", (body.candidate_id,)):
        raise err(404, 40403, "candidate not found")
    if blocked_between(uid, body.candidate_id):
        raise err(403, 40321, "blocked")
    # 幂等：同一 (from,to) 只写一条；priority_like 不降级
    existing = db.q1("SELECT * FROM likes WHERE from_id=? AND to_id=?", (uid, body.candidate_id))
    if existing:
        type_ = "priority_like" if "priority_like" in (existing["type"], body.type) else "like"
        if type_ != existing["type"]:
            with db.tx() as c:
                c.execute("UPDATE likes SET type=? WHERE from_id=? AND to_id=?", (type_, uid, body.candidate_id))
    else:
        if body.type == "priority_like":
            if _priority_quota_left(uid) <= 0:
                # TC07：额度为 0 不能操作；也不能用积分购买
                raise err(409, 40930, "priority like quota exhausted", {"remaining": 0})
        with db.tx() as c:
            c.execute("INSERT INTO likes(from_id,to_id,type,created_at) VALUES(?,?,?,?)",
                      (uid, body.candidate_id, body.type, util.now()))
    util.track(uid, "like_created", candidate_id=body.candidate_id, type=body.type)
    if body.type == "priority_like":
        util.track(uid, "priority_like_created", candidate_id=body.candidate_id)
    match = _create_match_if_reciprocal(uid, body.candidate_id)
    return ok({"liked": True, "type": body.type, "match": match,
               "priority_like_quota_left": _priority_quota_left(uid)})


@router.post("/passes")
def create_pass(body: PassIn, user: dict = Depends(me)):
    uid = user["id"]
    if body.candidate_id == uid:
        raise err(400, 40021, "cannot pass yourself")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO passes(from_id,to_id,created_at) VALUES(?,?,?)",
                  (uid, body.candidate_id, util.now()))
    return ok({"passed": True, "cooldown_hours": PASS_COOLDOWN_HOURS})


# ---------------- matches & messages ----------------

def _match_or_403(match_id: str, uid: str) -> dict:
    m = db.q1("SELECT * FROM matches WHERE id=?", (match_id,))
    # TC40：不存在与越权都返回 404，避免枚举
    if not m:
        raise err(404, 40404, "match not found")
    if uid not in (m["user_a"], m["user_b"]):
        raise err(404, 40404, "match not found")
    return dict(m)


def _match_public(m: dict, uid: str) -> dict:
    other = m["user_b"] if m["user_a"] == uid else m["user_a"]
    o = db.q1("SELECT * FROM users WHERE id=?", (other,))
    return {"match_id": m["id"], "status": m["status"], "created_at": m["created_at"],
            "can_chat": m["status"] == "active",  # TC09：单向喜欢/非 active 不能聊天
            "peer": {"user_id": o["id"], "nickname": o["nickname"], "avatar_seed": o["avatar_seed"],
                     "languages": util.loads(o["languages"], []), "online": util.now() - o["last_active_at"] < 900}}


@router.get("/matches")
def list_matches(user: dict = Depends(me)):
    rows = db.q("SELECT * FROM matches WHERE user_a=? OR user_b=? ORDER BY created_at DESC", (user["id"], user["id"]))
    out = []
    for r in rows:
        last = db.q1("SELECT text, created_at FROM messages WHERE match_id=? ORDER BY created_at DESC LIMIT 1", (r["id"],))
        item = _match_public(dict(r), user["id"])
        item["last_message"] = last["text"] if last else None
        item["last_at"] = last["created_at"] if last else r["created_at"]
        out.append(item)
    return ok(out)


@router.get("/chats")
def list_chats(user: dict = Depends(me)):
    return list_matches(user)


@router.get("/matches/{match_id}/messages")
def list_messages(match_id: str, after: float = 0, user: dict = Depends(me)):
    m = _match_or_403(match_id, user["id"])
    rows = db.q("SELECT * FROM messages WHERE match_id=? AND created_at>? ORDER BY created_at ASC",
                (m["id"], after))
    return ok([dict(r) for r in rows])


@router.post("/matches/{match_id}/messages")
def send_message(match_id: str, body: MessageIn, user: dict = Depends(me)):
    m = _match_or_403(match_id, user["id"])
    if m["status"] != "active":
        raise err(403, 40322, "match is not active", {"status": m["status"]})
    uid = user["id"]
    text = body.text.strip()
    if not (TEXT_MIN <= len(text) <= TEXT_MAX):
        raise err(400, 40030, f"text must be {TEXT_MIN}-{TEXT_MAX} chars")
    existing = db.q1("SELECT * FROM messages WHERE match_id=? AND client_message_id=?",
                     (m["id"], body.client_message_id))
    if existing:  # TC10：幂等，返回同一条
        return ok({"message": dict(existing), "deduplicated": True})
    if body.source_generation_id:
        gen = db.q1("SELECT * FROM ai_generations WHERE id=? AND user_id=?", (body.source_generation_id, uid))
        if gen:
            with db.tx() as c:
                c.execute("INSERT INTO ai_adoptions(id,generation_id,user_id,suggestion_index,created_at) VALUES(?,?,?,0,?)",
                          (util.new_id("ad"), gen["id"], uid, util.now()))
            util.track(uid, "icebreaker_adopted", generation_id=gen["id"], kind=gen["kind"])
    mid = util.new_id("msg")
    with db.tx() as c:
        c.execute(
            """INSERT INTO messages(id,match_id,sender_id,client_message_id,text,source_generation_id,status,created_at)
               VALUES(?,?,?,?,?,?,'delivered',?)""",
            (mid, m["id"], uid, body.client_message_id, text, body.source_generation_id, util.now()),
        )
    util.track(uid, "message_delivered", match_id=m["id"], chars=len(text))
    rewards.maybe_first_valid_interaction(m["id"])
    return ok({"message": dict(db.q1("SELECT * FROM messages WHERE id=?", (mid,))), "deduplicated": False})


@router.post("/matches/{match_id}/read")
def mark_read(match_id: str, user: dict = Depends(me)):
    _match_or_403(match_id, user["id"])
    return ok({"ok": True})