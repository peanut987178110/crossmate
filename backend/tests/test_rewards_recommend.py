"""TC28-TC30, TC31：奖励幂等、观察期、周上限、兑换、违规回滚。"""
from __future__ import annotations

from app.core import db, util
from app.services import rewards
from app.services.recommend import RECOMMEND_WEIGHTS, rank
from conftest import API, PROFILE, auth, make_match


def test_tc28_profile_complete_reward_only_once(client, maker):
    a = maker("a@x.com")
    r1 = rewards.grant(a["uid"], "PROFILE_COMPLETE", source_id=a["uid"])
    r2 = rewards.grant(a["uid"], "PROFILE_COMPLETE", source_id=a["uid"])
    assert r1["created"] is True and r1["points"] == 20
    assert r2["created"] is False


def test_tc29_spam_does_not_trigger_valid_interaction(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    mid = make_match(client, a, b)
    # a 刷 5 条同样的 client_message_id（幂等）+ 5 条不同内容，b 不回
    for i in range(5):
        client.post(f"{API}/matches/{mid}/messages",
                    json={"client_message_id": f"spam-{i}", "text": "hi hi hi"}, headers=auth(a))
    rows = db.q("SELECT * FROM rewards_ledger WHERE reward_code='FIRST_VALID_INTERACTION'")
    assert rows == []
    # 双方各 2 条 -> 都落 pending
    for u, tag in ((a, "a"), (b, "b")):
        for i in range(2):
            client.post(f"{API}/matches/{mid}/messages",
                        json={"client_message_id": f"{tag}{i}", "text": f"real {i}"}, headers=auth(u))
    rows = db.q("SELECT * FROM rewards_ledger WHERE reward_code='FIRST_VALID_INTERACTION'")
    assert len(rows) == 2 and all(r["status"] == "pending" for r in rows)
    # 观察 24h 后结算为 granted
    util.advance(24 * 3600 + 1)
    assert rewards.settle_due() >= 2
    rows = db.q("SELECT * FROM rewards_ledger WHERE reward_code='FIRST_VALID_INTERACTION'")
    assert len(rows) == 2 and all(r["status"] == "granted" for r in rows)


def test_valid_interaction_skipped_after_block(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    mid = make_match(client, a, b)
    for i in range(2):
        client.post(f"{API}/matches/{mid}/messages", json={"client_message_id": f"a{i}", "text": "hi"}, headers=auth(a))
    client.post(f"{API}/blocks", json={"user_id": b["uid"]}, headers=auth(a))
    # 拉黑后 b 不能回复 -> 不满足“双方各 2 条”
    r = client.post(f"{API}/matches/{mid}/messages", json={"client_message_id": "b0", "text": "hi"}, headers=auth(b))
    assert r.status_code == 403


def test_tc30_insufficient_balance_returns_409(client, maker):
    a = maker("a@x.com")
    r = client.post(f"{API}/rewards/redemptions", json={"item_id": "theme_sunset"}, headers=auth(a))
    assert r.status_code == 409 and r.json()["code"] == 40920


def test_redeem_success_and_balance(client, maker):
    a = maker("a@x.com")
    rewards.grant(a["uid"], "PROFILE_COMPLETE", source_id=a["uid"])
    r = client.post(f"{API}/rewards/redemptions", json={"item_id": "theme_sunset"}, headers=auth(a))
    assert r.status_code == 200 and r.json()["data"]["balance"] == 0
    led = client.get(f"{API}/rewards/ledger", headers=auth(a)).json()["data"]
    assert led["balance"] == 0 and led["items"][0]["status"] == "granted"
    assert set(led["catalog"]) == {"theme_sunset", "frame_wave"}


def test_weekly_cap(client, maker):
    a = maker("a@x.com")
    for i in range(40):  # 40*3 = 120 > 100
        rewards.grant(a["uid"], "QUALITY_FEEDBACK", source_id=f"x{i}")
    total = db.q1("SELECT COALESCE(SUM(points),0) AS s FROM rewards_ledger WHERE user_id=? AND status IN ('granted','pending')",
                  (a["uid"],))["s"]
    assert total <= 100


def test_reward_reversal_on_violation(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    rewards.grant(a["uid"], "PROFILE_COMPLETE", source_id=a["uid"])
    assert rewards.balance(a["uid"]) == 20
    rewards.reverse_for_user(a["uid"], "violation")
    assert rewards.balance(a["uid"]) == 0


def test_negative_reward_rejected(client, maker):
    a = maker("a@x.com")
    try:
        rewards.grant(a["uid"], "REPORT_SUBMITTED", source_id="x")
        raised = False
    except ValueError:
        raised = True
    assert raised  # 非法奖励码一律拒绝（举报/登录永不奖励）


def test_recommend_weights_sum_and_points_not_used():
    assert abs(sum(RECOMMEND_WEIGHTS.values()) - 1.0) < 1e-9
    assert "points" not in RECOMMEND_WEIGHTS and "room_stay" not in RECOMMEND_WEIGHTS
    viewer = {"id": "u1", "interests": ["Indie", "Travel", "Coffee"], "languages": ["English"],
              "social_goals": ["make_friends"], "timezone": "UTC+0"}
    good = {"id": "u2", "interests": '["Indie","Travel","Coffee"]', "languages": '["English"]',
            "social_goals": '["make_friends"]', "timezone": "UTC+0", "last_active_at": util.now()}
    bad = {"id": "u3", "interests": '["Gaming"]', "languages": '["日本語"]', "social_goals": '["dating"]',
           "timezone": "UTC+8", "last_active_at": util.now() - 100 * 3600}
    ranked = rank(viewer, [bad, good])
    assert ranked[0]["id"] == "u2"
    assert ranked[0]["_features"]["interest"] == 1.0
    assert ranked[0]["_risk"] <= 0.30


def test_discover_never_returns_points_or_sensitive(client, maker):
    a = maker("a@x.com")
    maker("b@x.com")
    items = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    assert items
    for it in items:
        assert "points" not in it and "gender" not in it and "religion" not in it
        assert "match_hint" in it
    _ = PROFILE