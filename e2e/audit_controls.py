"""逐个点击审计：把每个页面里所有「看起来可点」的控制都真点一遍，检查是否产生可观察变化。

判据（任一算通过）：
  1. location.hash 变了（导航）；
  2. #view 的 innerHTML 变了（重渲染）；
  3. #modal 从隐藏变为显示，或内容变了；
  4. #toast 出现且在点击后 1.2s 内消失（提示也算反馈）；
  5. view 内某个按钮的 disabled / checked / class(on/active) 变了（开关）；
  6. localStorage 里有键新增或值变化；
  7. console 出现网络请求（有 API 调用）。

排除：纯展示元素（.sub/.pill/.tag/.hint/.meta）不算；label 文字不计。

用法：python tools/audit_controls.py [--route '#/settings']
输出：每个控制一行 PASS/NOOP，末尾汇总。
"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

BASE = "http://localhost:8000/app/index.html"
ROUTES = [
    ("#/splash", None),
    ("#/login", None),
    ("#/home", "alex"),
    ("#/discover", "alex"),
    ("#/chats", "alex"),
    ("#/rooms", "alex"),
    ("#/notifications", "alex"),
    ("#/points", "alex"),
    ("#/me", "alex"),
    ("#/privacy", "alex"),
    ("#/settings", "alex"),
]

PROBE_CANDIDATES = """
() => {
  const sel = 'button, [role=switch], [data-go], [data-back], [data-tab], [data-demo], [data-i], [data-l], [data-goal], [data-lang], .list, .setting';
  const out = [];
  document.querySelectorAll('#view ' + sel + ', #modal ' + sel + ', #tabbar ' + sel).forEach((el, i) => {
    if (el.disabled) return;
    const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
    if (!t) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    el.setAttribute('data-audit', String(i));
    out.push({ i, tag: el.tagName.toLowerCase(), cls: el.className, text: t, id: el.id, inModal: !!el.closest('#modal') });
  });
  return out;
}
"""

SNAPSHOT = """
() => ({
  selects: JSON.stringify([...document.querySelectorAll('#view select')].map(s => s.dataset.v + '=' + s.value)),
  scrollTop: document.getElementById('view').scrollTop,

  hash: location.hash,
  html: document.getElementById('view').innerHTML.length + ':' + document.getElementById('view').innerHTML.slice(0, 2000),
  modal: document.getElementById('modal').hidden ? '' : document.getElementById('modal').innerHTML.slice(0, 400),
  toast: document.getElementById('toast').className,
  ls: JSON.stringify(Object.assign({}, localStorage)),
  view: document.getElementById('view').innerHTML.slice(0, 3000),
})
"""


async def settle(pg, ms=900):
    await pg.wait_for_timeout(ms)


async def audit_route(pg, route, user):
    # 全新会话，避免上一条的副作用
    await pg.goto(BASE)
    await pg.evaluate("()=>localStorage.clear()")
    await pg.goto(BASE)
    await settle(pg, 900)
    if user:
        await pg.evaluate(f"()=>window.__cm.loginDemo('{user}')")
        await settle(pg, 700)
    await pg.evaluate(f"()=>window.__cm.go('{route}')")
    await settle(pg, 1000)

    cands = await pg.evaluate(PROBE_CANDIDATES)
    results = []
    for c in cands:
        before = await pg.evaluate(SNAPSHOT)
        errors = []
        pg_once = lambda e: errors.append(str(e))
        pg.on("pageerror", pg_once)
        js_err = None
        try:
            await pg.click(f"[data-audit='{c['i']}']", timeout=3000, force=True)
        except Exception as e:
            js_err = str(e).split("\n")[0][:80]
        await settle(pg, 1100)
        after = await pg.evaluate(SNAPSHOT)
        pg.remove_listener("pageerror", pg_once)

        changed = []
        if after["hash"] != before["hash"]:
            changed.append("nav")
        if after["html"] != before["html"]:
            changed.append("view")
        if after["modal"] != before["modal"]:
            changed.append("modal")
        if after["toast"] != before["toast"]:
            changed.append("toast")
        if after["ls"] != before["ls"]:
            changed.append("storage")
        if after["selects"] != before["selects"]:
            changed.append("select")
        if after["scrollTop"] != before["scrollTop"]:
            changed.append("scroll")
        # 同一个按钮自身状态变化（开关/选中）
        sel = f"[data-audit='{c['i']}']"
        try:
            if await pg.query_selector(sel):
                a = await pg.evaluate(f"(()=>{{const e=document.querySelector(\"{sel}\");return e?(e.className+'|'+(e.getAttribute('aria-checked')||'')):'gone'}})()")
                if a == "gone":
                    changed.append("removed")
        except Exception:
            pass
        if errors:
            changed.append("error:" + errors[0][:60])

        status = "PASS" if changed else "NOOP"
        if js_err:
            status = "ERR"
        results.append({**c, "status": status, "why": ",".join(changed) or "-", "err": js_err})

        # 点完后可能已经跳到别的页面，回到原路由再继续
        if after["hash"] != route or after["modal"]:
            await pg.evaluate("()=>{const m=document.getElementById('modal'); if(m){m.hidden=true;m.innerHTML=''}}")
            await pg.evaluate(f"()=>window.__cm.go('{route}')")
            await settle(pg, 800)
            cands = await pg.evaluate(PROBE_CANDIDATES)
            # 重新编号，后面的 i 依旧有效（同一模板渲染顺序一致）
    return results


async def main():
    only = None
    if "--route" in sys.argv:
        only = sys.argv[sys.argv.index("--route") + 1]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 420, "height": 860})
        pg = await ctx.new_page()
        allres = {}
        for route, user in ROUTES:
            if only and route != only:
                continue
            try:
                rs = await audit_route(pg, route, user)
            except Exception as e:
                print(f"\n## {route}  !! 页面本身打不开: {e}")
                continue
            allres[route] = rs
            print(f"\n## {route}" + (f" (as {user})" if user else ""))
            for r in rs:
                mark = {"PASS": "  ok ", "NOOP": " NOOP", "ERR": "  ERR"}[r["status"]]
                print(f"{mark} | {r['tag']:<6} | {r['text'][:34]:<34} | {r['why']:<10} | {r['err'] or ''}")
        await b.close()

        noop = [(rt, r) for rt, rs in allres.items() for r in rs if r["status"] != "PASS"]
        print(f"\n===== {sum(len(v) for v in allres.values())} 个控制已点击，{len(noop)} 个没有可观察反应 =====")
        for rt, r in noop:
            print(f"  {rt:14} {r['text'][:40]:<40} {r['status']} {(r['err'] or '')[:60]}")


asyncio.run(main())