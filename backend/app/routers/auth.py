"""认证：邮箱验证码 + Apple/Google 演示登录。

规则（FR01）：
- 验证码 5 分钟有效，60 秒内不可重发（重发 -> 429），错 5 次锁 15 分钟；
- 必须确认 18+ 并同意条款与隐私政策；
- 删除中的账号返回 409 并进入删除流程；已删除账号不可登录。
演示 / 测试：验证码直接返回在 data.demo_code 中且打 WARNING 日志（生产必须走邮件）。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.config import (
    CODE_LOCK_SECONDS,
    CODE_MAX_ATTEMPTS,
    CODE_RESEND_SECONDS,
    CODE_TTL_SECONDS,
    MIN_AGE,
    SESSION_TTL_SECONDS,
)
from app.core.http import err, ok

router = APIRouter()
log = logging.getLogger("crossmate.auth")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CodeIn(BaseModel):
    email: str


class VerifyIn(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=8)
    age_confirmed: bool = False
    terms_accepted: bool = False
    consent_version: str = "2026-11-01"


class SocialIn(BaseModel):
    provider: str  # apple | google
    email: str
    age_confirmed: bool = False
    terms_accepted: bool = False
    consent_version: str = "2026-11-01"


def _norm(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise err(400, 40001, "invalid email")
    return email


def issue_session(user_id: str) -> dict:
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute(
            "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (token, user_id, util.now(), util.now() + SESSION_TTL_SECONDS),
        )
    return {"access_token": token, "token_type": "Bearer", "expires_in": SESSION_TTL_SECONDS}


@router.post("/auth/code")
def send_code(body: CodeIn):
    email = _norm(body.email)
    t = util.now()
    row = db.q1("SELECT * FROM auth_codes WHERE email=?", (email,))
    if row:
        if row["locked_until"] > t:
            raise err(429, 42904, "too many attempts, locked", {"retry_after": int(row["locked_until"] - t)})
        if t - row["last_sent_at"] < CODE_RESEND_SECONDS:
            raise err(429, 42901, "code resent too soon",
                      {"retry_after": int(CODE_RESEND_SECONDS - (t - row["last_sent_at"]))})
    code = f"{secrets.randbelow(1_000_000):06d}"
    with db.tx() as c:
        c.execute(
            """INSERT INTO auth_codes(email,code,expires_at,last_sent_at,attempts,locked_until)
               VALUES(?,?,?,?,0,0)
               ON CONFLICT(email) DO UPDATE SET code=excluded.code, expires_at=excluded.expires_at,
               last_sent_at=excluded.last_sent_at, attempts=0""",
            (email, code, t + CODE_TTL_SECONDS, t),
        )
        c.execute("DELETE FROM sessions WHERE user_id=''")
    # 生产：调用邮件服务。演示：直接下发。日志不打印邮箱（TC36）。
    log.warning("demo mode: verification code issued for a user")
    return ok({"sent": True, "expires_in": CODE_TTL_SECONDS, "resend_in": CODE_RESEND_SECONDS,
               "demo_code": code})


def _check_age(body) -> None:
    if not body.age_confirmed:
        raise err(422, 42201, "age confirmation required (18+)")
    if not body.terms_accepted:
        raise err(422, 42202, "terms and privacy acceptance required")


def _login_or_create(email: str, consent_version: str) -> tuple[str, bool]:
    row = db.q1("SELECT * FROM users WHERE email=?", (email,))
    if row:
        if row["status"] == "deleted":
            raise err(403, 40310, "account deleted")
        if row["status"] == "deleting":
            raise err(409, 40910, "account deletion pending, cancel first")
        if row["status"] == "frozen":
            raise err(403, 40311, "account frozen")
        with db.tx() as c:
            c.execute("UPDATE users SET last_active_at=?, age_confirmed=1 WHERE id=?", (util.now(), row["id"]))
        return row["id"], bool(row["profile_completed"])
    uid = util.new_id("u")
    t = util.now()
    with db.tx() as c:
        c.execute(
            """INSERT INTO users(id,email,age_confirmed,status,avatar_seed,consent_version,created_at,last_active_at)
               VALUES(?,?,1,'active',?,'',?,?)""",
            (uid, email, hashlib.sha1(email.encode()).hexdigest()[:8], t, t),
        )
        c.execute("INSERT INTO privacy(user_id) VALUES(?)", (uid,))
    util.track(uid, "account_created")
    util.audit(uid, "account.created", uid, consent_version=consent_version)
    return uid, False


@router.post("/auth/verify")
def verify(body: VerifyIn):
    email = _norm(body.email)
    _check_age(body)
    t = util.now()
    row = db.q1("SELECT * FROM auth_codes WHERE email=?", (email,))
    if not row:
        raise err(400, 40002, "no code requested")
    if row["locked_until"] > t:
        raise err(429, 42904, "locked", {"retry_after": int(row["locked_until"] - t)})
    if row["expires_at"] < t:
        raise err(400, 40003, "code expired")
    if not hmac.compare_digest(str(row["code"]), str(body.code).strip()):
        attempts = row["attempts"] + 1
        locked = t + CODE_LOCK_SECONDS if attempts >= CODE_MAX_ATTEMPTS else 0
        with db.tx() as c:
            c.execute("UPDATE auth_codes SET attempts=?, locked_until=? WHERE email=?", (attempts, locked, email))
        if locked:
            raise err(429, 42903, "too many attempts, locked 15 minutes")
        raise err(400, 40004, "incorrect code", {"attempts_left": CODE_MAX_ATTEMPTS - attempts})
    uid, completed = _login_or_create(email, body.consent_version)
    with db.tx() as c:
        c.execute("DELETE FROM auth_codes WHERE email=?", (email,))
    sess = issue_session(uid)
    util.track(uid, "app_opened", via="email")
    return ok({**sess, "user_id": uid, "profile_completed": completed,
               "next": "home" if completed else "profile"})


@router.post("/auth/social")
def social(body: SocialIn):
    """Apple / Google 演示登录：真实实现需校验 provider id_token。"""
    if body.provider not in {"apple", "google"}:
        raise err(400, 40005, "unsupported provider")
    email = _norm(body.email)
    _check_age(body)
    uid, completed = _login_or_create(email, body.consent_version)
    sess = issue_session(uid)
    util.track(uid, "app_opened", via=body.provider)
    return ok({**sess, "user_id": uid, "profile_completed": completed,
               "next": "home" if completed else "profile"})


@router.post("/auth/logout")
def logout(authorization: str = Header(default="", alias="Authorization")):
    token = authorization.replace("Bearer ", "").strip()
    with db.tx() as c:
        c.execute("UPDATE sessions SET revoked=1 WHERE token=?", (token,))
    return ok({"logged_out": True})


def current_user(authorization: str) -> dict:
    """依赖注入：解析 Bearer Token -> 用户字典。"""
    token = (authorization or "").replace("Bearer ", "").strip()
    if not token:
        raise err(401, 40101, "missing token")
    row = db.q1(
        "SELECT u.*, s.expires_at, s.revoked FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,),
    )
    if not row or row["revoked"] or row["expires_at"] < util.now():
        raise err(401, 40102, "invalid or expired token")
    if row["status"] in ("deleted", "deleting"):
        raise err(401, 40103, "account unavailable")
    user = dict(row)
    user["interests"] = util.loads(user["interests"], [])
    user["languages"] = util.loads(user["languages"], [])
    user["social_goals"] = util.loads(user["social_goals"], [])
    return user


def is_adult_ok(user: dict) -> bool:
    return bool(user.get("age_confirmed")) and MIN_AGE == 18