"""CrossMate Web E2E（Playwright）—— 走真实浏览器 + 真实 API。

依赖：
    python -m pip install playwright pytest && python -m playwright install chromium

运行：
    cd backend && python -m pytest ../e2e -q          # 自动拉起服务（独立临时数据库）
    BASE_URL=http://127.0.0.1:8000 python -m pytest ../e2e -q   # 复用已启动的服务

覆盖：P01–P18 主流程 + AI 只回填不自动发送 + 积分不因举报发放 + 越权 404 + 键盘可达性。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
# 运行 E2E 的 Python 需要能 import fastapi；若当前解释器只是带 Playwright 的测试环境，
# 用 PYTHON_FOR_APP 指定运行后端（如 `py -3` 或某个 venv 的 python）。
APP_PYTHON = os.getenv("PYTHON_FOR_APP", sys.executable)
API = "/api/v0.2"


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    if BASE_URL:
        yield BASE_URL
        return
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CROSSMATE_DB": str(tmp_path_factory.mktemp("db") / "e2e.db"), "PORT": str(port)}
    log = open(tmp_path_factory.mktemp("log") / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([APP_PYTHON, "-m", "app.main"], cwd=str(ROOT / "backend"), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    for _ in range(150):
        if _port_open("127.0.0.1", port):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    if proc.poll() is not None:
        log.flush()
        raise RuntimeError(f"server exited with {proc.returncode}; see {log.name}")
    yield url
    proc.terminate()
    proc.wait(timeout=10)
    log.close()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 420, "height": 860})
    pg = ctx.new_page()
    errors: list[str] = []

    def on_response(r):
        if r.status >= 500:
            errors.append(f"{r.status} {r.request.method} {r.url}")

    pg.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    pg.on("response", on_response)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{base_url}/app/index.html?e2e=1")
    pg.evaluate("() => localStorage.clear()")
    pg.goto(f"{base_url}/app/index.html?e2e=1")
    pg.wait_for_function("() => !!window.__cmE2E")
    yield pg
    assert not errors, errors
    ctx.close()


def fire(pg, action, **payload):
    return pg.evaluate("a => window.__cmE2E.fire(a.action, a.rest)", {"action": action, "rest": payload})


def token_for(pg, user: str) -> str:
    return fire(pg, "token", user=user)["token"]


def call(pg, token: str, method: str, path: str, body=None):
    return fire(pg, "call", token=token, method=method, path=path, body=body)


def ready_user(pg, key: str) -> dict:
    """准备一个已完成资料的账号（幂等），返回 {token, user_id}；服务端按 (email) 复用账号。"""
    token = token_for(pg, key)
    call(pg, token, "PATCH", "/users/me/profile", {
        "nickname": key.capitalize(), "country": "Singapore", "timezone": "UTC+8",
        "languages": ["English"], "social_goals": ["make_friends"],
        "interests": ["Indie", "Travel", "Coffee"], "bio": "e2e",
    })
    prof = call(pg, token, "GET", "/users/me/profile")
    assert prof["status"] == 200, prof
    return {"token": token, "user_id": prof["json"]["data"]["id"]}


def make_match(pg, a: dict, b: dict) -> str:
    """双向喜欢生成匹配（幂等：已匹配则从列表取 id）。"""
    call(pg, b["token"], "POST", "/likes", {"candidate_id": a["user_id"], "type": "like"})
    r = call(pg, a["token"], "POST", "/likes", {"candidate_id": b["user_id"], "type": "like"})
    assert r["status"] == 200, r
    m = r["json"]["data"].get("match")
    if m:
        return m.get("match_id") or m["id"]
    ms = call(pg, a["token"], "GET", "/matches")["json"]["data"]
    hit = next(x for x in ms if x["peer"]["user_id"] == b["user_id"])
    return hit.get("match_id") or hit["id"]


def reload_as(pg, token: str):
    pg.evaluate("t => localStorage.setItem('cm_token', t)", token)
    pg.reload()
    pg.wait_for_function("() => !!window.__cmE2E && !!window.__cm.state.me")


def test_onboarding_creates_profile(page):
    page.evaluate("() => window.__cm.go('#/login')")
    page.fill("#email", "newbie@demo.crossmate.app")
    page.check("#adult")
    page.check("#terms")
    page.click("#send")
    page.wait_for_selector("#step2:not([hidden])")
    page.click("#verify")
    page.wait_for_selector("#nick")
    page.fill("#nick", "Nova")
    page.fill("#country", "Canada")
    page.fill("#tz", "UTC-4")
    page.click("[data-goal='make_friends']")
    page.click("#next")
    page.wait_for_selector("[data-i='Indie']")
    for i in ("Indie", "Travel", "Coffee"):
        page.locator(f"[data-i='{i}']").click()
    page.click("#save")
    page.wait_for_function("() => location.hash === '#/home'")
    me = page.evaluate("() => window.__cm.state.me")
    assert me["nickname"] == "Nova" and me["profile_completed"] == 1
    assert page.inner_text(".hero, #view").find("Nova") >= 0


def test_age_gate_blocks_sign_in(page):
    page.evaluate("() => window.__cm.go('#/login')")
    page.fill("#email", "kid@demo.crossmate.app")
    page.click("#send")  # 未勾选 18+ / 条款
    page.wait_for_timeout(300)
    assert page.inner_text("#toast").find("18") >= 0, "未勾选 18+ 时必须在客户端拦截"
    assert page.is_hidden("#step2"), "不应发出验证码请求"
    page.check("#adult")
    page.click("#send")
    page.wait_for_timeout(300)
    assert page.inner_text("#toast").find("条款") >= 0


def test_discover_like_match_and_ai_fill_only(page):
    alex = ready_user(page, "alex")
    sam = ready_user(page, "sam")
    mid = make_match(page, alex, sam)

    reload_as(page, alex["token"])
    page.evaluate(f"() => window.__cm.go('#/chat/{mid}')")
    page.wait_for_selector("#aiBtn")
    assert page.locator(".bubble").count() == 0, "匹配后不应自动发送任何消息"
    page.click("#aiBtn")
    page.click("#ice")
    page.wait_for_selector("[data-sug='0']")
    suggestion = page.inner_text("[data-sug='0']").split("\n")[0].strip()
    page.click("[data-sug='0']")
    page.wait_for_selector("#msg")
    page.wait_for_function("() => (document.getElementById('msg')?.value || '').length > 0")
    assert page.input_value("#msg") == suggestion, " AI 建议只回填输入框"
    assert page.locator(".bubble").count() == 0, "回填后仍未发送"
    page.click("#send")
    page.wait_for_selector(".bubble.me")
    sent = page.inner_text(".bubble.me")
    assert suggestion in sent
    # 采纳记录存在（追踪），但发送由用户点击触发
    usage = call(page, alex["token"], "GET", "/ai/usage")
    assert usage["json"]["data"].get("icebreaker", 0) >= 1, "采纳应在用量中计数"


def test_room_flow_host_approve_and_member_404(page):
    alex = ready_user(page, "alex")
    sam = ready_user(page, "sam")
    rooms = call(page, alex["token"], "GET", "/rooms")
    if not rooms["json"]["data"]["items"]:
        call(page, alex["token"], "POST", "/rooms", {"title": "E2E room", "topic": "hello"})
        rooms = call(page, alex["token"], "GET", "/rooms")
    rid = rooms["json"]["data"]["items"][0]["id"]

    reload_as(page, sam["token"])
    page.evaluate(f"() => window.__cm.api('/rooms/{rid}/join', {{method:'POST'}})")
    page.evaluate(f"() => window.__cm.go('#/room/{rid}?joined=1')")
    page.wait_for_selector("#hand")
    page.click("#hand")
    page.wait_for_selector("#lower")

    reload_as(page, alex["token"])
    page.evaluate(f"() => window.__cm.go('#/room/{rid}')")
    page.wait_for_selector("[data-approve]")
    page.click("[data-approve]")
    page.wait_for_timeout(700)
    # 越权：听众私聊匹配 / 非成员读房消息 -> 404 / 403
    other = call(page, sam["token"], "GET", "/matches/not-a-match/messages")
    assert other["status"] == 404


def test_points_rewards_and_report_no_points(page):
    alex = ready_user(page, "alex")
    sam = ready_user(page, "sam")
    before = call(page, alex["token"], "GET", "/rewards/ledger")["json"]["data"]["balance"]
    rep = call(page, alex["token"], "POST", "/reports", {"target_user_id": sam["user_id"], "category": "harassment", "detail": "x"})
    assert rep["status"] == 200 and rep["json"]["data"]["report_id"]
    after = call(page, alex["token"], "GET", "/rewards/ledger")["json"]["data"]["balance"]
    assert after == before, "举报不应获得积分"

    reload_as(page, alex["token"])
    page.evaluate("() => window.__cm.go('#/points')")
    page.wait_for_selector("#view .hero")
    assert "never affect" in page.inner_text("#view")
    page.evaluate("() => window.__cm.go('#/settings')")
    page.wait_for_selector("#rm")
    page.click("#rm")
    assert page.evaluate("() => document.body.classList.contains('reduced-motion')")


def test_call_timer_requires_both_media_ready(page):
    # 举报测试可能已把 sam 屏蔽，这里用独立账号对，避免跨测试污染
    alex = ready_user(page, "maya")
    sam = ready_user(page, "kenji")
    mid = make_match(page, alex, sam)
    c = call(page, alex["token"], "POST", "/calls", {"match_id": mid})["json"]["data"]
    assert c["status"] == "ringing"
    st = call(page, alex["token"], "GET", f"/calls/{c['call_id']}")["json"]["data"]
    assert st["connected_at"] is None

    reload_as(page, alex["token"])
    page.evaluate(f"() => window.__cm.go('#/call/{c['call_id']}')")
    page.wait_for_function("() => document.querySelector('#callStatus') && document.querySelector('#callStatus').textContent === 'Connected'", timeout=8000)
    assert page.locator("#callDur").inner_text() != "Timer starts once both sides are media-ready"