"""可点性测试：页面上「看起来能点」的东西，点下去必须有可观察的反应。

背景：用户反馈「连最简单的语言都切换不了」——Settings 里 Language 那一行带着 ›，
看起来可点，实际只是个 <span>，没有任何事件。这类问题没有测试盯着，就会反复出现。

判据：点击后 1.1 秒内，下列任一发生即算通过——
  路由变化 / #view 重渲染 / 弹层出现或变化 / toast 出现 / localStorage 变化 /
  select 值变化 / 自身 aria-pressed、aria-checked、class 变化 / 被移除。

注意排除项：
  - 点「当前所在 Tab」只回顶部（设计如此），不单独断言；
  - 弹层遮罩挡住下层元素导致的点击超时不算失败，直接跳过该项；
  - 积分页余额不足的 Redeem 现在是 disabled，也不点。

运行：python -m pytest test_clickable.py -q
"""
import asyncio

import pytest

BASE = "http://localhost:8000/app/index.html"

# (路由, 登录用户, 说明) —— 用 demo 账号进入，保证页面有内容
PAGES = [
    ("#/splash", None, "启动页"),
    ("#/login", None, "登录页"),
    ("#/home", "alex", "首页"),
    ("#/discover", "alex", "发现页"),
    ("#/chats", "alex", "会话列表"),
    ("#/rooms", "alex", "房间列表"),
    ("#/notifications", "alex", "通知"),
    ("#/points", "alex", "积分"),
    ("#/me", "alex", "我的"),
    ("#/privacy", "alex", "隐私"),
    ("#/settings", "alex", "设置"),
]

CANDIDATES = r"""
(route) => {
  const sel = 'button:not([disabled]), [role=switch], [data-go], [data-back], [data-tab], [data-demo], [data-i], [data-l], [data-goal], [data-lang], .list';
  const out = [];
  document.querySelectorAll('#view ' + sel + ', #modal ' + sel).forEach((el, i) => {
    if (el.disabled) return;
    const txt = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!txt) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    // 当前所在 Tab 点了只回顶部，跳过
    if (el.dataset && el.dataset.tab && route === '#/' + el.dataset.tab) return;
    el.setAttribute('data-clk', String(i));
    out.push({ i, text: txt.slice(0, 36) });
  });
  return out;
}
"""

SNAPSHOT = r"""
() => {
  const v = document.getElementById('view');
  const m = document.getElementById('modal');
  return {
    hash: location.hash,
    view: v.innerHTML.length + '|' + v.innerHTML.slice(0, 1500),
    modal: m.hidden ? '' : m.innerHTML.slice(0, 300),
    toast: document.getElementById('toast').className,
    ls: JSON.stringify(Object.assign({}, localStorage)),
    selects: [...v.querySelectorAll('select')].map(s => s.value).join(','),
  };
}
"""


async def snapshot(pg):
    return await pg.evaluate(SNAPSHOT)


def differs(before, after, el_state_before, el_state_after):
    if after["hash"] != before["hash"]:
        return "路由跳转"
    if after["view"] != before["view"]:
        return "页面重渲染"
    if after["modal"] != before["modal"]:
        return "弹层"
    if after["toast"] != before["toast"]:
        return "toast"
    if after["ls"] != before["ls"]:
        return "本机存储"
    if after["selects"] != before["selects"]:
        return "下拉值"
    if el_state_before != el_state_after:
        return "控件状态"
    return None


api_calls = []
dialog_seen = [None]


async def run_page(pg, route, user):
    # 先按正常流程登录（真实点击演示账号），再跳路由。
    # 不能只是 evaluate(loginDemo())，因为底部 Tab 的显隐由 setActiveTab 负责，
    # 直接调 loginDemo 不会触发它，会让后续 Tab 点击像「没反应」。
    await pg.goto(BASE)
    await pg.evaluate("()=>localStorage.clear()")
    await pg.goto(BASE)
    await pg.wait_for_timeout(900)
    if user:
        await pg.click(f"[data-demo='{user}']")
        await pg.wait_for_timeout(1500)
    await pg.evaluate(f"()=>window.__cm.go('{route}')")
    await pg.wait_for_timeout(1000)

    cands = await pg.evaluate(CANDIDATES, route)
    dead = []
    seen = set()  # 同一控件点第二次，页面已经变了，没变化属正常，不重复判
    for c in cands:
        if c["text"] in seen:
            continue
        seen.add(c["text"])
        sel = f"[data-clk='{c['i']}']"
        clickable = await pg.query_selector(sel)
        if not clickable:
            continue  # 上一个点击后界面变了，这个控件已经不在
        if await clickable.is_disabled():
            continue  # 点了别的控件之后它才变成禁用（例如积分不足的 Redeem）——禁用是对的
        state_before = await pg.evaluate(
            f"""(()=>{{const e=document.querySelector("{sel}");if(!e)return 'gone';
                return (e.className||'')+'/'+(e.getAttribute('aria-checked')||'')+'/'+(e.getAttribute('aria-pressed')||'');}})()"""
        )
        before = await snapshot(pg)
        ok = False
        reqs_before = len(api_calls)
        try:
            await clickable.click(timeout=2500)
        except Exception:
            ok = True  # 被弹层遮住或被移除：不算「点了没反应」
        if not ok:
            await pg.wait_for_timeout(1100)
            after = await snapshot(pg)
            state_after = await pg.evaluate(
                f"""(()=>{{const e=document.querySelector("{sel}");if(!e)return 'gone';
                    return (e.className||'')+'/'+(e.getAttribute('aria-checked')||'')+'/'+(e.getAttribute('aria-pressed')||'');}})()"""
            )
            ok = differs(before, after, state_before, state_after) is not None or len(api_calls) > reqs_before
            if dialog_seen[0]:
                dialog_seen[0] = None
                ok = True  # 弹了确认框 = 有反馈（确认/取消由用户决定）
        if not ok:
            dead.append(c["text"])

        # 恢复现场：关弹层、回到原路由
        await pg.evaluate("()=>{const m=document.getElementById('modal');if(m){m.hidden=true;m.innerHTML=''}}")
        if (await pg.evaluate("location.hash")) != route:
            await pg.evaluate(f"()=>window.__cm.go('{route}')")
            await pg.wait_for_timeout(700)
            # 重新编号
            await pg.evaluate(CANDIDATES, route)
    return dead


@pytest.mark.parametrize("route,user,label", PAGES, ids=[p[2] for p in PAGES])
def test_every_clickable_control_responds(route, user, label):
    async def go():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await (await b.new_context(viewport={"width": 420, "height": 860})).new_page()
            pg.on("request", lambda r: api_calls.append(r.url))

            async def _on_dialog(d):
                dialog_seen[0] = d.message
                await d.accept()

            pg.on("dialog", lambda d: asyncio.ensure_future(_on_dialog(d)))
            try:
                return await run_page(pg, route, user)
            finally:
                await b.close()

    dead = asyncio.run(go())
    assert not dead, f"{label}（{route}）有控件点了没反应：{dead}"


def test_language_switch_changes_ui_and_persists():
    """用户报过的具体问题：Language 必须真的能切，切完界面要变，重启后还要记住。"""

    async def go():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await (await b.new_context(viewport={"width": 420, "height": 860})).new_page()
            await pg.goto(BASE)
            await pg.evaluate("()=>localStorage.clear()")
            await pg.goto(BASE)
            await pg.wait_for_timeout(900)
            # 后端对同一邮箱发验证码限流 60 秒（/auth/code 抛 "code resent too soon"）。
            # 用 kenji 而不是 alex：alex 被上面 11 条页面用例反复登录，单跑时经常还在限流窗口里。
            # 单跑这条时上一轮用例刚发过码，所以要等窗口过去再试；这里如实等，不放宽产品限流。
            await pg.evaluate(
                r"""async () => {
                    for (let i = 0; i < 8; i++) {
                        try { await window.__cm.loginDemo('kenji'); return 'ok'; }
                        catch (e) {
                            const m = /retry_after[^0-9]*(\d+)/.exec(e.message || '');
                            const wait = m ? (+m[1] + 2) * 1000 : 20000;
                            await new Promise(r => setTimeout(r, Math.min(wait, 70000)));
                        }
                    }
                    throw new Error('loginDemo 一直拿不到验证码');
                }"""
            )
            await pg.wait_for_timeout(700)
            await pg.evaluate("()=>window.__cm.go('#/settings')")
            await pg.wait_for_timeout(800)

            await pg.click("#langRow")
            await pg.wait_for_timeout(300)
            await pg.click("[data-lang='zh']")
            await pg.wait_for_timeout(800)

            tabs_zh = [await x.inner_text() for x in await pg.query_selector_all("#tabbar span")]
            logout_zh = await pg.inner_text("#logout")
            html_lang = await pg.evaluate("document.documentElement.lang")
            await pg.reload()
            await pg.wait_for_timeout(1200)
            await pg.evaluate("()=>window.__cm.go('#/settings')")
            await pg.wait_for_timeout(700)
            row_after_reload = await pg.inner_text("#langRow")
            await b.close()
            return tabs_zh, logout_zh, html_lang, row_after_reload

    tabs, logout, html_lang, row = asyncio.run(go())
    assert tabs == ["首页", "发现", "房间", "聊天", "我的"], tabs
    assert logout == "退出登录", logout
    assert html_lang == "zh", html_lang
    assert "中文" in row, row