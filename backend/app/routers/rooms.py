"""房间与一对一语音通话（FR08 / FR09）。

状态机：
  room: scheduled -> live -> ended / frozen（ended 不可再进；frozen 只读）
  member: listener -举手-> speaker_requested -主持人同意-> speaker -禁言-> muted(仍可听)
          主持人 remove -> left（前端返回 Rooms 并展示原因类别）
  call: requested -> ringing -> connected -> ended
        弱网: connected <-> reconnecting
        只有双方 media_ready 才写 connected_at（TC24/TC25），且只写一次。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.config import ROOM_MESSAGE_MAX, ROOM_TABS
from app.core.http import err, ok
from app.routers.auth import current_user

router = APIRouter()


def me(authorization: str = Header(default="", alias="Authorization")) -> dict:
    return current_user(authorization)


def _notify(user_id: str, ntype: str, title: str, body: str, ref_id: str | None = None) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO notifications(id,user_id,type,title,body,ref_id,read,created_at) VALUES(?,?,?,?,?,?,0,?)",
                  (util.new_id("n"), user_id, ntype, title, body, ref_id, util.now()))


# =================== rooms ===================
def _room_public(r: dict) -> dict:
    members = db.q("SELECT user_id,role,hand_raised,muted_until FROM room_members WHERE room_id=? AND left_at IS NULL", (r["id"],))
    # 仅暴露昵称，不暴露邮箱等隐私字段（TC36：最小必要）
    names = {u["id"]: u["nickname"] for u in db.q("SELECT id,nickname FROM users")}
    return {**r, "listeners": sum(1 for m in members if m["role"] == "listener"),
            "speakers": [m["user_id"] for m in members if m["role"] == "speaker"],
            "members": [{"user_id": m["user_id"], "nickname": names.get(m["user_id"], ""), "role": m["role"],
                         "hand_raised": m["hand_raised"], "muted_until": m["muted_until"]} for m in members],
            "member_count": len(members)}


@router.get("/rooms")
def list_rooms(tab: str = "popular", user: dict = Depends(me)):
    if tab not in ROOM_TABS:
        raise err(400, 40050, "invalid tab")
    rows = db.q("SELECT * FROM rooms WHERE status IN ('live','scheduled') ORDER BY created_at DESC")
    items = [_room_public(dict(r)) for r in rows]
    if not items:
        return ok({"items": [], "empty": True, "empty_text": "No rooms yet. Start one or check back soon."})
    return ok({"items": items, "empty": False})


class CreateRoomIn(BaseModel):
    title: str = Field(min_length=2, max_length=60)
    topic: str = Field(min_length=2, max_length=200)
    language: str = "en"


@router.post("/rooms")
def create_room(body: CreateRoomIn, user: dict = Depends(me)):
    uid = util.new_id("r")
    t = util.now()
    with db.tx() as c:
        c.execute("INSERT INTO rooms(id,title,topic,language,host_id,status,created_at) VALUES(?,?,?,?,?,'live',?)",
                  (uid, body.title, body.topic, body.language, user["id"], t))
        c.execute("INSERT INTO room_members(room_id,user_id,role,joined_at) VALUES(?,?, 'speaker', ?)", (uid, user["id"], t))
    return ok(_room_public(dict(db.q1("SELECT * FROM rooms WHERE id=?", (uid,)))))


@router.get("/rooms/{room_id}")
def get_room(room_id: str, user: dict = Depends(me)):
    r = db.q1("SELECT * FROM rooms WHERE id=?", (room_id,))
    if not r:
        raise err(404, 40410, "room not found")
    d = _room_public(dict(r))
    mine = db.q1("SELECT * FROM room_members WHERE room_id=? AND user_id=? AND left_at IS NULL", (room_id, user["id"]))
    d["me"] = dict(mine) if mine else None
    host = db.q1("SELECT nickname FROM users WHERE id=?", (r["host_id"],))
    d["host_name"] = host["nickname"] if host else "Host"
    d["is_host"] = r["host_id"] == user["id"]
    return ok(d)


@router.post("/rooms/{room_id}/join")
def join_room(room_id: str, user: dict = Depends(me)):
    r = db.q1("SELECT * FROM rooms WHERE id=?", (room_id,))
    if not r:
        raise err(404, 40410, "room not found")
    if r["status"] != "live":
        raise err(409, 40940, "room is not live", {"status": r["status"]})
    # TC16：Join 后默认 listener（主持人自己除外）
    role = "speaker" if r["host_id"] == user["id"] else "listener"
    with db.tx() as c:
        c.execute("""INSERT INTO room_members(room_id,user_id,role,hand_raised,muted_until,joined_at,left_at)
                     VALUES(?,?,?,0,0,?,NULL)
                     ON CONFLICT(room_id,user_id) DO UPDATE SET left_at=NULL, joined_at=excluded.joined_at""",
                  (room_id, user["id"], role, util.now()))
        got = c.execute("SELECT role FROM room_members WHERE room_id=? AND user_id=?", (room_id, user["id"])).fetchone()
    util.track(user["id"], "room_joined", room_id=room_id, role=got["role"])
    return ok({"room_id": room_id, "role": got["role"]})


@router.post("/rooms/{room_id}/raise-hand")
def raise_hand(room_id: str, user: dict = Depends(me)):
    m = _member(room_id, user["id"])
    if m["role"] in ("speaker",):
        return ok({"hand_raised": False, "role": "speaker", "muted": m["muted_until"] > util.now()})
    if m["muted_until"] > util.now():
        raise err(403, 40330, "You are temporarily muted.")
    with db.tx() as c:
        c.execute("UPDATE room_members SET hand_raised=1, role='speaker_requested' WHERE room_id=? AND user_id=?",
                  (room_id, user["id"]))
        host = db.q1("SELECT host_id FROM rooms WHERE id=?", (room_id,))
        c.execute("INSERT INTO notifications(id,user_id,type,title,body,ref_id,read,created_at) VALUES(?,?,?,?,?,?,0,?)",
                  (util.new_id("n"), host["host_id"], "room", "Hand raised",
                   "Someone raised their hand. Hand raised. Waiting for the host.", room_id, util.now()))
    util.track(user["id"], "hand_raised", room_id=room_id)
    return ok({"hand_raised": True, "role": "speaker_requested"})


@router.delete("/rooms/{room_id}/raise-hand")
def lower_hand(room_id: str, user: dict = Depends(me)):
    with db.tx() as c:
        c.execute("UPDATE room_members SET hand_raised=0, role='listener' WHERE room_id=? AND user_id=? AND role='speaker_requested'",
                  (room_id, user["id"]))
    return ok({"hand_raised": False, "role": "listener"})


def _member(room_id: str, uid: str) -> dict:
    m = db.q1("SELECT * FROM room_members WHERE room_id=? AND user_id=? AND left_at IS NULL", (room_id, uid))
    if not m:
        raise err(403, 40331, "join the room first")
    return dict(m)


def _room(room_id: str) -> dict:
    r = db.q1("SELECT * FROM rooms WHERE id=?", (room_id,))
    if not r:
        raise err(404, 40410, "room not found")
    return dict(r)


def _require_host(room_id: str, uid: str) -> dict:
    r = _room(room_id)
    if r["host_id"] != uid:
        raise err(403, 40332, "host only")
    return r


class ModerationIn(BaseModel):
    user_id: str
    minutes: int = 10
    reason: str = "host_action"


@router.post("/rooms/{room_id}/moderation/approve")
def approve_speaker(body: ModerationIn, room_id: str, user: dict = Depends(me)):
    _require_host(room_id, user["id"])
    m = db.q1("SELECT * FROM room_members WHERE room_id=? AND user_id=? AND left_at IS NULL", (room_id, body.user_id))
    if not m or m["role"] != "speaker_requested":
        raise err(409, 40941, "no pending hand raise")
    with db.tx() as c:
        c.execute("UPDATE room_members SET role='speaker', hand_raised=0 WHERE room_id=? AND user_id=?",
                  (room_id, body.user_id))
    _notify(body.user_id, "room", "You are now a speaker", "The host approved your request.", room_id)
    util.track(user["id"], "speaker_approved", room_id=room_id, target=body.user_id)
    return ok({"role": "speaker"})


@router.post("/rooms/{room_id}/moderation/deny")
def deny_speaker(body: ModerationIn, room_id: str, user: dict = Depends(me)):
    """主持人拒绝举手：回到 listener（TC18 的否定路径）。"""
    _require_host(room_id, user["id"])
    with db.tx() as c:
        c.execute("UPDATE room_members SET role='listener', hand_raised=0 WHERE room_id=? AND user_id=? AND role='speaker_requested'",
                  (room_id, body.user_id))
    _notify(body.user_id, "room", "The host declined your request", "You can raise your hand again later.", room_id)
    return ok({"role": "listener"})


@router.post("/rooms/{room_id}/moderation/mute")
def mute_member(body: ModerationIn, room_id: str, user: dict = Depends(me)):
    _require_host(room_id, user["id"])
    until = util.now() + max(1, body.minutes) * 60
    with db.tx() as c:
        c.execute("UPDATE room_members SET muted_until=? WHERE room_id=? AND user_id=?", (until, room_id, body.user_id))
    _notify(body.user_id, "room", "You are temporarily muted", "You are temporarily muted.", room_id)
    return ok({"muted_until": until})  # TC19：被禁言仍可听房（listener 权限不变）


@router.post("/rooms/{room_id}/moderation/remove")
def remove_member(body: ModerationIn, room_id: str, user: dict = Depends(me)):
    _require_host(room_id, user["id"])
    with db.tx() as c:
        c.execute("UPDATE room_members SET left_at=?, role='left' WHERE room_id=? AND user_id=?",
                  (util.now(), room_id, body.user_id))
    _notify(body.user_id, "room", "You were removed from the room",
            "You were removed from the room.", room_id)
    util.audit(user["id"], "room.remove", body.user_id, room_id=room_id, reason=body.reason)
    # TC20：被移出 -> 返回 Rooms 列表并展示原因类别
    return ok({"removed": True, "reason_category": body.reason, "redirect": "rooms"})


@router.post("/rooms/{room_id}/leave")
def leave_room(room_id: str, user: dict = Depends(me)):
    m = _member(room_id, user["id"])
    joined = m["joined_at"]
    with db.tx() as c:
        c.execute("UPDATE room_members SET left_at=?, role='left', hand_raised=0 WHERE room_id=? AND user_id=?",
                  (util.now(), room_id, user["id"]))
    util.track(user["id"], "room_left", room_id=room_id, stay_s=round(util.now() - joined))
    return ok({"left": True})


class RoomMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=ROOM_MESSAGE_MAX)


@router.post("/rooms/{room_id}/messages")
def room_message(room_id: str, body: RoomMessageIn, user: dict = Depends(me)):
    r = _room(room_id)
    if r["status"] != "live":
        raise err(409, 40942, "room is not live")
    m = _member(room_id, user["id"])
    if m["role"] == "listener":
        raise err(403, 40333, "listeners cannot speak yet")  # TC17
    if m["muted_until"] > util.now():
        raise err(403, 40330, "You are temporarily muted.")
    mid = util.new_id("rm")
    with db.tx() as c:
        c.execute("INSERT INTO room_messages(id,room_id,user_id,text,created_at) VALUES(?,?,?,?,?)",
                  (mid, room_id, user["id"], body.text.strip(), util.now()))
    util.track(user["id"], "room_message_delivered", room_id=room_id)
    return ok({"id": mid})


@router.get("/rooms/{room_id}/messages")
def room_messages(room_id: str, after: float = 0, user: dict = Depends(me)):
    _member(room_id, user["id"])
    rows = db.q("SELECT * FROM room_messages WHERE room_id=? AND created_at>? ORDER BY created_at", (room_id, after))
    return ok([dict(r) for r in rows])


class RoomReportIn(BaseModel):
    target_user_id: str
    category: str = "harassment"
    detail: str = ""


@router.post("/rooms/{room_id}/reports")
def room_report(room_id: str, body: RoomReportIn, user: dict = Depends(me)):
    _member(room_id, user["id"])
    from app.routers.misc import create_report, ReportIn

    res = create_report(ReportIn(target_user_id=body.target_user_id, target_type="room",
                                 target_id=room_id, category=body.category, detail=body.detail, also_block=True),
                        user=user)
    return res  # TC22：生成 report_id


@router.post("/rooms/{room_id}/end")
def end_room(room_id: str, user: dict = Depends(me)):
    _require_host(room_id, user["id"])
    with db.tx() as c:
        c.execute("UPDATE rooms SET status='ended', ended_at=? WHERE id=?", (util.now(), room_id))
        members = c.execute("SELECT user_id FROM room_members WHERE room_id=? AND left_at IS NULL", (room_id,)).fetchall()
        for m in members:
            c.execute("INSERT INTO notifications(id,user_id,type,title,body,ref_id,read,created_at) VALUES(?,?,?,?,?,?,0,?)",
                      (util.new_id("n"), m["user_id"], "room", "Room ended",
                       "The room has ended. Thanks for joining!", room_id, util.now()))
    # TC21：房间结束生成通知
    return ok({"status": "ended", "notified": len(members)})


# =================== calls ===================
class CallIn(BaseModel):
    match_id: str


@router.post("/calls")
def create_call(body: CallIn, user: dict = Depends(me)):
    m = db.q1("SELECT * FROM matches WHERE id=?", (body.match_id,))
    if not m or user["id"] not in (m["user_a"], m["user_b"]):
        raise err(404, 40404, "match not found")
    if m["status"] != "active":
        raise err(403, 40322, "match is not active")
    callee = m["user_b"] if m["user_a"] == user["id"] else m["user_a"]
    cid = util.new_id("c")
    with db.tx() as c:
        c.execute("INSERT INTO calls(id,caller_id,callee_id,match_id,status,created_at) VALUES(?,?,?,?, 'requested', ?)",
                  (cid, user["id"], callee, m["id"], util.now()))
        c.execute("UPDATE calls SET status='ringing', ringing_at=? WHERE id=?", (util.now(), cid))
    util.track(user["id"], "call_requested", call_id=cid)
    return ok({"call_id": cid, "status": "ringing", "callee_id": callee})  # TC23


def _call(cid: str, uid: str) -> dict:
    c = db.q1("SELECT * FROM calls WHERE id=?", (cid,))
    if not c or uid not in (c["caller_id"], c["callee_id"]):
        raise err(404, 40411, "call not found")
    return dict(c)


@router.post("/calls/{call_id}/accept")
def accept_call(call_id: str, user: dict = Depends(me)):
    c = _call(call_id, user["id"])
    if user["id"] != c["callee_id"]:
        raise err(403, 40340, "only callee can accept")
    if c["status"] != "ringing":
        raise err(409, 40950, "call not ringing", {"status": c["status"]})
    with db.tx() as c2:
        c2.execute("UPDATE calls SET status='connecting' WHERE id=?", (call_id,))
    return ok({"call_id": call_id, "status": "connecting"})


@router.post("/calls/{call_id}/reject")
def reject_call(call_id: str, user: dict = Depends(me)):
    c = _call(call_id, user["id"])
    if user["id"] != c["callee_id"]:
        raise err(403, 40340, "only callee can reject")
    with db.tx() as c2:
        c2.execute("UPDATE calls SET status='ended', ended_at=?, end_reason='rejected' WHERE id=?", (util.now(), call_id))
    return ok({"status": "ended", "end_reason": "rejected"})


@router.post("/calls/{call_id}/end")
def end_call(call_id: str, user: dict = Depends(me)):
    c = _call(call_id, user["id"])
    if c["status"] == "ended":
        return ok({"status": "ended", "ended_at": c["ended_at"]})  # 幂等
    with db.tx() as c2:
        c2.execute("UPDATE calls SET status='ended', ended_at=?, end_reason=COALESCE(end_reason,'hangup') WHERE id=?",
                   (util.now(), call_id))
    util.track(user["id"], "call_ended", call_id=call_id,
               duration=int(util.now() - (c["connected_at"] or util.now())))
    return ok({"status": "ended", "ended_at": util.now()})  # TC27


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = ""  # 注意：不保存聊天原文，只保存用户主动写的反馈


@router.post("/calls/{call_id}/feedback")
def call_feedback(call_id: str, body: FeedbackIn, user: dict = Depends(me)):
    c = _call(call_id, user["id"])
    if c["status"] != "ended":
        raise err(409, 40951, "call not ended")
    submitted = util.loads(c["feedback_submitted"], [])
    if user["id"] in submitted:
        return ok({"granted": False, "reason": "already_submitted"})
    quality = "qualified" if (body.comment.strip() or body.rating >= 1) and len(set(body.comment.split())) >= 1 else "low"
    fid = util.new_id("fb")
    with db.tx() as c2:
        c2.execute("INSERT INTO feedback(id,user_id,target_type,target_id,rating,comment,quality,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (fid, user["id"], "call", call_id, body.rating, body.comment[:500], quality, util.now()))
        submitted.append(user["id"])
        c2.execute("UPDATE calls SET feedback_submitted=? WHERE id=?", (util.dumps(submitted), call_id))
    from app.services import rewards

    res = rewards.grant(user["id"], "QUALITY_FEEDBACK", source_id=call_id, reason="Quality call feedback")
    return ok({"feedback_id": fid, "reward": res})


class RtcEventIn(BaseModel):
    call_id: str
    provider_event_id: str
    event_type: str  # media_ready | media_lost | connected | network_unstable | network_recovered | hangup
    user_id: str
    media_ready: bool = False
    network_summary: dict | None = None
    signature: str = ""


@router.post("/rtc/events")
def rtc_event(body: RtcEventIn, user: dict = Depends(me)):
    """RTC 回调：必须验签 + 去重。只有双方 media_ready 才写 connected_at，且只写一次。

    演示模式（CROSSMATE_AI_MODE=mock）下，后端对“未签名”事件按内部可信处理，
    等价于 RTC 供应商回调；生产必须由供应商签名，未签名一律 403。
    """
    from app.core.config import AI_MODE
    from app.core.webhook import verify_signature

    if body.signature and not verify_signature(util.as_dict(body)):
        raise err(403, 40341, "invalid signature")
    if not body.signature and AI_MODE != "mock":
        raise err(403, 40341, "invalid signature")
    call = _call(body.call_id, user["id"])
    if db.q1("SELECT 1 FROM rtc_events WHERE provider_event_id=?", (body.provider_event_id,)):
        return ok({"duplicate": True})
    with db.tx() as c:
        c.execute("INSERT INTO rtc_events(provider_event_id,call_id,event_type,payload,created_at) VALUES(?,?,?,?,?)",
                  (body.provider_event_id, body.call_id, body.event_type, util.dumps(util.as_dict(body)), util.now()))
        side = "a_ready" if body.user_id == call["caller_id"] else "b_ready"
        if body.event_type == "media_ready" and body.media_ready:
            c.execute(f"UPDATE calls SET {side}=1 WHERE id=?", (body.call_id,))
        elif body.event_type == "network_unstable" and call["status"] == "connected":
            c.execute("UPDATE calls SET status='reconnecting' WHERE id=?", (body.call_id,))
        elif body.event_type == "network_recovered" and call["status"] == "reconnecting":
            c.execute("UPDATE calls SET status='connected' WHERE id=?", (body.call_id,))
        cur = c.execute("SELECT * FROM calls WHERE id=?", (body.call_id,)).fetchone()
        if cur["a_ready"] and cur["b_ready"] and not cur["connected_at"] and cur["status"] not in ("ended",):
            c.execute("UPDATE calls SET connected_at=?, status='connected' WHERE id=? AND connected_at IS NULL",
                      (util.now(), body.call_id))
            connected = True
        else:
            connected = False
    if connected:
        util.track(user["id"], "call_connected", call_id=body.call_id)  # TC25：只记一次
    return ok({"status": cur["status"], "connected_at": inner_connected_at(body.call_id) if connected else None})


def inner_connected_at(cid: str):
    return db.q1("SELECT connected_at FROM calls WHERE id=?", (cid,))["connected_at"]


@router.get("/calls/{call_id}")
def get_call(call_id: str, user: dict = Depends(me)):
    c = _call(call_id, user["id"])
    c["duration"] = int((c["ended_at"] or util.now()) - c["connected_at"]) if c["connected_at"] else 0
    return ok(c)