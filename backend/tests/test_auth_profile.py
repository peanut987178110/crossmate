"""TC01-TC07, TC33：认证、资料、隐私、发现、额度、删除账号。"""
from __future__ import annotations

from app.core import util
from conftest import API, PROFILE, auth


def test_health(client):
    assert client.get("/healthz").json()["ok"] is True


def test_tc01_code_requested_and_adult_gate(client):
    r = client.post(f"{API}/auth/code", json={"email": "a@b.com"})
    assert r.status_code == 200 and len(r.json()["data"]["demo_code"]) == 6
    code = r.json()["data"]["demo_code"]
    # TC01/TC02：未成年人确认未勾选不能继续
    r = client.post(f"{API}/auth/verify", json={"email": "a@b.com", "code": code,
                                               "age_confirmed": False, "terms_accepted": True})
    assert r.status_code == 422
    r = client.post(f"{API}/auth/verify", json={"email": "a@b.com", "code": code,
                                               "age_confirmed": True, "terms_accepted": False})
    assert r.status_code == 422


def test_tc03_resend_within_60s_returns_429(client):
    client.post(f"{API}/auth/code", json={"email": "c@d.com"})
    r = client.post(f"{API}/auth/code", json={"email": "c@d.com"})
    assert r.status_code == 429
    assert r.json()["data"]["retry_after"] > 0
    util.advance(61)
    r = client.post(f"{API}/auth/code", json={"email": "c@d.com"})
    assert r.status_code == 200


def test_code_locks_after_5_wrong_attempts(client):
    code = client.post(f"{API}/auth/code", json={"email": "e@f.com"}).json()["data"]["demo_code"]
    for _ in range(4):
        r = client.post(f"{API}/auth/verify", json={"email": "e@f.com", "code": "000000",
                                                   "age_confirmed": True, "terms_accepted": True})
        assert r.status_code == 400
    r = client.post(f"{API}/auth/verify", json={"email": "e@f.com", "code": "000000",
                                               "age_confirmed": True, "terms_accepted": True})
    assert r.status_code == 429
    # 正确验证码也进不来
    r = client.post(f"{API}/auth/verify", json={"email": "e@f.com", "code": code,
                                               "age_confirmed": True, "terms_accepted": True})
    assert r.status_code == 429


def test_code_expires_after_5_minutes(client):
    code = client.post(f"{API}/auth/code", json={"email": "g@h.com"}).json()["data"]["demo_code"]
    util.advance(301)
    r = client.post(f"{API}/auth/verify", json={"email": "g@h.com", "code": code,
                                               "age_confirmed": True, "terms_accepted": True})
    assert r.status_code == 400 and "expired" in r.json()["message"]


def test_new_user_needs_profile(client, user):
    r = client.get(f"{API}/users/me/profile", headers=auth(user))
    assert r.json()["data"]["profile_completed"] == 0
    r = client.get(f"{API}/discover", headers=auth(user))
    assert r.status_code == 403


def test_tc04_less_than_3_interests_blocks_completion(client, user):
    body = {**PROFILE, "interests": ["Indie", "Travel"]}
    r = client.patch(f"{API}/users/me/profile", json=body, headers=auth(user))
    assert r.status_code == 200
    assert r.json()["data"]["profile_completed"] == 0
    body["interests"] = ["Indie", "Travel", "Coffee"]
    r = client.patch(f"{API}/users/me/profile", json=body, headers=auth(user))
    assert r.json()["data"]["profile_completed"] == 1


def test_profile_strength_and_email_not_leaked(client, maker):
    u = maker("x@y.com")
    d = client.get(f"{API}/users/me/profile", headers=auth(u)).json()["data"]
    assert d["profile_strength"] == 100
    assert "email" not in d


def test_tc05_private_fields_not_in_discover(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    r = client.patch(f"{API}/users/me/privacy", json={"interests_visibility": "private",
                                                     "country_visibility": "matches_only"},
                     headers=auth(b))
    assert r.status_code == 200
    items = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    peer = [i for i in items if i["user_id"] == b["uid"]]
    assert peer, "b should be discoverable"
    assert "interests" not in peer[0]
    assert "country" not in peer[0]
    # timezone 默认 matches_only：未匹配前不可见，匹配后可见
    assert "timezone" not in peer[0]


def test_tc06_pass_cooldown_does_not_repeat(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    client.post(f"{API}/passes", json={"candidate_id": b["uid"]}, headers=auth(a))
    items = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    assert all(i["user_id"] != b["uid"] for i in items)
    util.advance(24 * 3600 + 1)
    items = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    assert any(i["user_id"] == b["uid"] for i in items)


def test_tc07_priority_like_quota(client, maker):
    a = maker("a@x.com")
    peers = [maker(f"p{i}@x.com") for i in range(4)]
    for p in peers[:3]:
        r = client.post(f"{API}/likes", json={"candidate_id": p["uid"], "type": "priority_like"}, headers=auth(a))
        assert r.status_code in (200, 409)
    r = client.post(f"{API}/likes", json={"candidate_id": peers[3]["uid"], "type": "priority_like"}, headers=auth(a))
    assert r.status_code == 409 and r.json()["code"] == 40930
    # 普通 like 不受额度影响
    r = client.post(f"{API}/likes", json={"candidate_id": peers[3]["uid"], "type": "like"}, headers=auth(a))
    assert r.status_code == 200


def test_tc32_privacy_change_invalidates_cache(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    first = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    assert "interests" in [i for i in first if i["user_id"] == b["uid"]][0]
    client.patch(f"{API}/users/me/privacy", json={"interests_visibility": "matches_only"}, headers=auth(b))
    second = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    row = [i for i in second if i["user_id"] == b["uid"]]
    assert row and "interests" not in row[0]


def test_tc33_account_deletion_stops_discovery_and_revokes_token(client, maker):
    a = maker("a@x.com")
    b = maker("b@x.com")
    r = client.post(f"{API}/users/me/deletion", json={"confirm": True}, headers=auth(b))
    assert r.status_code == 200
    assert client.get(f"{API}/users/me/profile", headers=auth(b)).status_code == 401
    items = client.get(f"{API}/discover", headers=auth(a)).json()["data"]["items"]
    assert all(i["user_id"] != b["uid"] for i in items)


def test_profile_validation(client, user):
    r = client.patch(f"{API}/users/me/profile", json={"social_goals": ["hack"]}, headers=auth(user))
    assert r.status_code == 400
    r = client.patch(f"{API}/users/me/profile", json={"nickname": ""}, headers=auth(user))
    assert r.status_code == 400