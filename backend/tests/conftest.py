"""pytest 夹具：每个测试用独立的临时 SQLite，并重置时钟。"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMPDIR = tempfile.mkdtemp(prefix="crossmate_test_")
os.environ["CROSSMATE_DB"] = os.path.join(_TMPDIR, "unused.db")
os.environ["CROSSMATE_SEED"] = "0"

from app.core import db, util  # noqa: E402
from app.core.webhook import sign  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API = "/api/v0.2"


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "test.db"))
    util.reset_clock()
    c = TestClient(app)
    yield c
    pass


def _login(c: TestClient, email: str) -> dict:
    r = c.post(f"{API}/auth/code", json={"email": email})
    assert r.status_code == 200, r.text
    code = r.json()["data"]["demo_code"]
    r = c.post(f"{API}/auth/verify", json={"email": email, "code": code,
                                           "age_confirmed": True, "terms_accepted": True})
    assert r.status_code == 200, r.text
    tok = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}", "uid": r.json()["data"]["user_id"]}


PROFILE = {
    "nickname": "Alex", "country": "United States", "timezone": "UTC+0",
    "languages": ["English", "中文"], "social_goals": ["make_friends", "language_exchange"],
    "interests": ["Indie", "Travel", "Coffee", "Design"], "bio": "Small moments.",
}


@pytest.fixture()
def user(client):
    return _login(client, "alex@example.com")


@pytest.fixture()
def user2(client):
    return _login(client, "sam@example.com")


@pytest.fixture()
def maker(client):
    def make(email: str, **over):
        u = _login(client, email)
        body = {**PROFILE, **over}
        r = client.patch(f"{API}/users/me/profile", json=body, headers={"Authorization": u["Authorization"]})
        assert r.status_code == 200, r.text
        return u

    return make


def auth(u: dict) -> dict:
    return {"Authorization": u["Authorization"]}


def make_match(client, a: dict, b: dict) -> str:
    r = client.post(f"{API}/likes", json={"candidate_id": b["uid"], "type": "like"}, headers=auth(a))
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/likes", json={"candidate_id": a["uid"], "type": "like"}, headers=auth(b))
    assert r.status_code == 200, r.text
    return r.json()["data"]["match"]["id"]


__all__ = ["API", "auth", "make_match", "sign", "PROFILE"]