"""TC15-TC27, TC22：房间、语音房状态机、一对一通话、RTC 回调。"""
from __future__ import annotations
import time
from app.core import db, util
from app.core.webhook import SECRET, sign
from conftest import API, PROFILE, auth, make_match
from app.routers.rooms import RtcEventIn

# ---------------- rooms ----------------


def _room(client, host, title="Late night language lounge", topic="Talk about languages and music"):
    r = client.post(f"{API}/rooms", json={"title": title, "topic": topic}, headers=auth(host))
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_tc15_empty_rooms_state(client, user):
    d = client.get(f"{API}/rooms", headers=auth(user)).json()["data"]
    assert d["empty"] is True and d["items"] == [] and d["empty_text"]


def test_tc16_join_defaults_to_listener(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    rid = _room(client, host)
    d = client.post(f"{API}/rooms/{rid}/join", headers=auth(guest)).json()["data"]
    assert d["role"] == "listener"
    got = client.get(f"{API}/rooms/{rid}", headers=auth(guest)).json()["data"]
    assert got["me"]["role"] == "listener"
    assert got["is_host"] is False


def test_tc17_listener_cannot_speak(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    rid = _room(client, host)
    client.post(f"{API}/rooms/{rid}/join", headers=auth(guest))
    r = client.post(f"{API}/rooms/{rid}/messages", json={"text": "hi"}, headers=auth(guest))
    assert r.status_code == 403


def test_tc18_raise_hand_and_host_approves(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    rid = _room(client, host)
    client.post(f"{API}/rooms/{rid}/join", headers=auth(guest))
    r = client.post(f"{API}/rooms/{rid}/raise-hand", headers=auth(guest)).json()["data"]
    assert r["role"] == "speaker_requested"
    # 主持人收到举手通知
    notes = client.get(f"{API}/notifications", headers=auth(host)).json()["data"]["items"]
    assert any("Hand raised" in n["title"] for n in notes)
    # 非主持人不能批准
    r = client.post(f"{API}/rooms/{rid}/moderation/approve", json={"user_id": guest["uid"]}, headers=auth(guest))
    assert r.status_code == 403
    r = client.post(f"{API}/rooms/{rid}/moderation/approve", json={"user_id": guest["uid"]}, headers=auth(host))
    assert r.json()["data"]["role"] == "speaker"
    assert client.post(f"{API}/rooms/{rid}/messages", json={"text": "hello!"}, headers=auth(guest)).status_code == 200


def test_tc19_muted_speaker_can_still_listen(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    rid = _room(client, host)
    client.post(f"{API}/rooms/{rid}/join", headers=auth(guest))
    client.post(f"{API}/rooms/{rid}/raise-hand", headers=auth(guest))
    client.post(f"{API}/rooms/{rid}/moderation/approve", json={"user_id": guest["uid"]}, headers=auth(host))
    client.post(f"{API}/rooms/{rid}/moderation/mute", json={"user_id": guest["uid"], "minutes": 10}, headers=auth(host))
    r = client.post(f"{API}/rooms/{rid}/messages", json={"text": "hi"}, headers=auth(guest))
    assert r.status_code == 403 and "muted" in r.json()["message"]
    # 仍然可以看房间、听消息
    assert client.get(f"{API}/rooms/{rid}", headers=auth(guest)).status_code == 200
    assert client.get(f"{API}/rooms/{rid}/messages", headers=auth(guest)).status_code == 200
    # 已晋升 speaker 时举手返回 muted=true，且依旧不能发言
    up = client.post(f"{API}/rooms/{rid}/raise-hand", headers=auth(guest)).json()["data"]
    assert up["muted"] is True


def test_tc20_removed_member_returns_to_rooms(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    rid = _room(client, host)
    client.post(f"{API}/rooms/{rid}/join", headers=auth(guest))
    r = client.post(f"{API}/rooms/{rid}/moderation/remove", json={"user_id": guest["uid"], "reason": "harassment"},
                    headers=auth(host)).json()["data"]
    assert r["removed"] is True and r["redirect"] == "rooms" and r["reason_category"] == "harassment"
    assert client.post(f"{API}/rooms/{rid}/messages", json={"text": "x"}, headers=auth(guest)).status_code == 403
    assert client.get(f"{API}/rooms/{rid}/messages", headers=auth(guest)).status_code == 403


def test_tc21_room_end_notifies_members(client, maker):
    host = maker("host@x.com")
    g1 = maker("g1@x.com")
    g2 = maker("g2@x.com")
    rid = _room(client, host)
    client.post(f"{API}/rooms/{rid}/join", headers=auth(g1))
    client.post(f"{API}/rooms/{rid}/join", headers=auth(g2))
    d = client.post(f"{API}/rooms/{rid}/end", headers=auth(host)).json()["data"]
    assert d["status"] == "ended" and d["notified"] == 3
    for u in (g1, g2):
        notes = client.get(f"{API}/notifications", headers=auth(u)).json()["data"]["items"]
        assert any("Room ended" in n["title"] for n in notes)
    # 结束后不能再加入
    r = client.post(f"{API}/rooms/{rid}/join", headers=auth(g1))
    assert r.status_code == 409


def test_tc22_room_report_generates_id(client, maker):
    host = maker("host@x.com")
    guest = maker("guest@x.com")
    bad = maker("bad@x.com")
    rid = _room(client, host)
    for u in (guest, bad):
        client.post(f"{API}/rooms/{rid}/join", headers=auth(u))
    r = client.post(f"{API}/rooms/{rid}/reports", json={"target_user_id": bad["uid"], "category": "harassment"},
                    headers=auth(guest))
    assert r.status_code == 200 and r.json()["data"]["report_id"]


def test_room_rag_topic_uses_public_topic_only(client, maker):
    host = maker("host@x.com")
    rid = _room(client, host)
    r = client.post(f"{API}/ai/generations", json={"kind": "room_topic", "room_id": rid}, headers=auth(host))
    d = r.json()["data"]
    assert d["status"] == "succeeded"
    assert all("languages" in s["text"] or "topic" in s["reason"] or "room" in s["reason"] for s in d["output"]["suggestions"])


# ---------------- calls ----------------


def _signed(ev: dict) -> dict:
    """模拟 RTC 供应商：按服务端将解析出的完整 payload 签名。"""
    payload = util.as_dict(RtcEventIn(**ev))
    ev = dict(ev)
    ev["signature"] = sign(payload)
    return ev


def _call_pair(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    mid = make_match(client, a, b)
    cid = client.post(f"{API}/calls", json={"match_id": mid}, headers=auth(a)).json()["data"]["call_id"]
    return a, b, cid


def test_tc23_call_request_shows_ringing(client, maker):
    a, b, cid = _call_pair(client, maker)
    d = client.get(f"{API}/calls/{cid}", headers=auth(b)).json()["data"]
    assert d["status"] == "ringing"


def test_tc24_tc25_media_ready_gates_connected_once(client, maker):
    a, b, cid = _call_pair(client, maker)
    client.post(f"{API}/calls/{cid}/accept", headers=auth(b))
    # 仅 avatars 一方 ready 不算 connected
    ev = _signed({"call_id": cid, "provider_event_id": "e1", "event_type": "media_ready", "user_id": a["uid"],
                  "media_ready": True})
    r = client.post(f"{API}/rtc/events", json=ev, headers=auth(a))
    assert r.status_code == 200
    d = client.get(f"{API}/calls/{cid}", headers=auth(a)).json()["data"]
    assert d["status"] in ("connecting", "reconnecting") and d["connected_at"] is None
    util.advance(5)
    ev2 = _signed({"call_id": cid, "provider_event_id": "e2", "event_type": "media_ready", "user_id": b["uid"],
                   "media_ready": True})
    client.post(f"{API}/rtc/events", json=ev2, headers=auth(b))
    d = client.get(f"{API}/calls/{cid}", headers=auth(a)).json()["data"]
    assert d["status"] == "connected" and d["connected_at"] is not None
    first_at = d["connected_at"]
    # 重复 media_ready 不会重写 connected_at
    ev3 = _signed(dict(ev2, provider_event_id="e3"))
    util.advance(3)
    client.post(f"{API}/rtc/events", json=ev3, headers=auth(b))
    d = client.get(f"{API}/calls/{cid}", headers=auth(a)).json()["data"]
    assert d["connected_at"] == first_at
    assert len(db.q("SELECT * FROM events WHERE name='call_connected'")) == 1


def test_rtc_rejects_bad_signature_and_dedup(client, maker):
    a, b, cid = _call_pair(client, maker)
    ev = {"call_id": cid, "provider_event_id": "x1", "event_type": "media_ready", "user_id": a["uid"],
          "media_ready": True, "signature": "bad"}
    assert client.post(f"{API}/rtc/events", json=ev, headers=auth(a)).status_code == 403
    ev = _signed({"call_id": cid, "provider_event_id": "x2", "event_type": "media_ready", "user_id": a["uid"],
                  "media_ready": True})
    assert client.post(f"{API}/rtc/events", json=ev, headers=auth(a)).json()["data"].get("duplicate") is None
    # 生产格式 ts=<ts>;v1=<sig> 也能验签
    payload = util.as_dict(RtcEventIn(call_id=cid, provider_event_id="x3", event_type="media_ready",
                                 user_id=a["uid"], media_ready=True))
    ev_ts = {"call_id": cid, "provider_event_id": "x3", "event_type": "media_ready", "user_id": a["uid"],
             "media_ready": True, "signature": sign(payload, ts=int(time.time()), with_ts=True)}
    assert client.post(f"{API}/rtc/events", json=ev_ts, headers=auth(a)).status_code == 200
    payload_stale = util.as_dict(RtcEventIn(call_id=cid, provider_event_id="x4", event_type="media_ready",
                                      user_id=a["uid"], media_ready=True))
    stale = {"call_id": cid, "provider_event_id": "x4", "event_type": "media_ready", "user_id": a["uid"],
             "media_ready": True, "signature": sign(payload_stale, ts=int(time.time()) - 3600, with_ts=True)}
    assert client.post(f"{API}/rtc/events", json=stale, headers=auth(a)).status_code == 403
    again = client.post(f"{API}/rtc/events", json=ev, headers=auth(a)).json()["data"]
    assert again["duplicate"] is True


def test_tc26_tc27_reconnecting_and_hangup(client, maker):
    a, b, cid = _call_pair(client, maker)
    client.post(f"{API}/calls/{cid}/accept", headers=auth(b))
    for i, (uid, h) in enumerate(((a["uid"], a), (b["uid"], b))):
        ev = _signed({"call_id": cid, "provider_event_id": f"m{i}", "event_type": "media_ready", "user_id": uid,
                      "media_ready": True})
        assert client.post(f"{API}/rtc/events", json=ev, headers=auth(h)).status_code == 200
    ev = _signed({"call_id": cid, "provider_event_id": "u1", "event_type": "network_unstable", "user_id": a["uid"]})
    client.post(f"{API}/rtc/events", json=ev, headers=auth(a))
    assert client.get(f"{API}/calls/{cid}", headers=auth(b)).json()["data"]["status"] == "reconnecting"
    ev = _signed({"call_id": cid, "provider_event_id": "u2", "event_type": "network_recovered",
                  "user_id": a["uid"]})
    client.post(f"{API}/rtc/events", json=ev, headers=auth(a))
    assert client.get(f"{API}/calls/{cid}", headers=auth(b)).json()["data"]["status"] == "connected"
    r = client.post(f"{API}/calls/{cid}/end", headers=auth(a)).json()["data"]
    assert r["status"] == "ended"
    d = client.get(f"{API}/calls/{cid}", headers=auth(b)).json()["data"]
    assert d["ended_at"] and d["end_reason"] in ("hangup", "rejected")
    # 挂断幂等
    assert client.post(f"{API}/calls/{cid}/end", headers=auth(b)).status_code == 200


def test_reject_call(client, maker):
    a, b, cid = _call_pair(client, maker)
    r = client.post(f"{API}/calls/{cid}/reject", headers=auth(b)).json()["data"]
    assert r["status"] == "ended" and r["end_reason"] == "rejected"


def test_call_feedback_grants_quality_feedback_once(client, maker):
    a, b, cid = _call_pair(client, maker)
    client.post(f"{API}/calls/{cid}/reject", headers=auth(b))
    r1 = client.post(f"{API}/calls/{cid}/feedback", json={"rating": 4, "comment": "good talk"}, headers=auth(b))
    assert r1.json()["data"]["reward"]["created"] is True
    r2 = client.post(f"{API}/calls/{cid}/feedback", json={"rating": 5, "comment": "again"}, headers=auth(b))
    assert r2.json()["data"]["granted"] is False
    _ = time


def test_rtc_unsigned_rejected_when_not_mock(client, user, user2, monkeypatch):
    """TC24 旁路：非 mock 模式下未签名 RTC 事件一律 403（验签是硬要求）。"""
    from app.core import config as cfg
    for u in (user, user2):
        client.patch(f"{API}/users/me/profile", json=PROFILE, headers=auth(u))
    client.post(f"{API}/likes", json={"candidate_id": user2["uid"], "type": "like"}, headers=auth(user))
    lr = client.post(f"{API}/likes", json={"candidate_id": user["uid"], "type": "like"}, headers=auth(user2))
    assert lr.status_code == 200, lr.text
    mid = lr.json()["data"]["match"]["id"]
    cr = client.post(f"{API}/calls", json={"match_id": mid}, headers=auth(user))
    assert cr.status_code == 200, cr.text
    call = cr.json()["data"]
    # handler 在 import 时读取 AI_MODE 常量，因此直接替换模块级常量（等价于生产环境）
    monkeypatch.setattr(cfg, "AI_MODE", "realtime", raising=False)
    r = client.post(f"{API}/rtc/events", json={
        "call_id": call["call_id"], "provider_event_id": "pe-unsigned", "event_type": "media_ready",
        "user_id": user["uid"], "media_ready": True,
    }, headers=auth(user))
    assert r.status_code == 403 and r.json()["code"] == 40341
