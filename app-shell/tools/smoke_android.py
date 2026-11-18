"""原生壳（Android 模拟器）烟雾测试：通过 WebView CDP 在真实 App 内驱动主流程，共 21 项断言。

前置：
    1. 模拟器已开机，APK 已安装并在前台（**不需要**启动电脑上的 backend——
       APK 是离线自包含的，FastAPI 由 Chaquopy 在设备本机 127.0.0.1:8765 跑起来）。
       电脑上的 backend 反而应当先关掉：端口 8000 留着容易让人误以为"测的是线上"。
    2. adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
       （pid 每次重启 App 都会变，要用 `adb shell pidof app.crossmate.demo` 重新取）
    3. 依赖 websocket-client：pip install websocket-client

运行：python tools/smoke_android.py [-v]

只用来联调「电脑上的后端」时才另起 backend（$env:HOST='0.0.0.0'; python -m app.main），
并用 adb -e cm_api 覆盖地址——此时第 2 项断言 "API 地址注入" 会失败，属预期。
"""
import json
import sys
import time
import urllib.request

import websocket

VERBOSE = "-v" in sys.argv


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True)  # WebView 会拒绝带 Origin 的 CDP 连接
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, timeout_ms=20000):
        r = self.call(
            "Runtime.evaluate",
            expression=expr,
            awaitPromise=True,
            returnByValue=True,
            userGesture=True,
        )
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def wait_js(self, expr, timeout=20, label=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.3)
        raise AssertionError(f"wait timeout: {label or expr}")


def pages():
    with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
        return json.loads(r.read())


def main():
    target = next(p for p in pages() if p["type"] == "page" and "localhost" in p["url"])
    c = CDP(target["webSocketDebuggerUrl"])
    c.call("Runtime.enable")
    c.call("Log.enable")

    errors = []
    c.ws.send(json.dumps({"id": 10**6, "method": "Log.enable"}))
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

    # 0. 后端地址注入 & 页面加载
    c.wait_js("!!document.querySelector('meta[name=\"cm-api\"]') && !!document.querySelector('#view')",
              label="首屏加载")
    # 只检查 meta 是不够的：app.js 的 API 常量在模块加载时定死，meta 事后被改写也不生效。
    # 必须验证运行时真正用的地址——历史上就是因为只查了 meta，注入失效被漏了一整轮。
    api = c.js("window.__cm.cfg.api")
    # App 自带后端（Chaquopy 在设备本机 8765 起 FastAPI），不依赖电脑。
    # 想对着电脑上的后端联调时用 adb -e cm_api 覆盖，此时这条会失败——属预期。
    check("API 地址注入", api == "http://127.0.0.1:8765/api/v0.2", api)
    hit = c.js(
        "window.__cm.api('/auth/code',{method:'POST',body:{email:'alex@demo.crossmate.app'},quiet:true})"
        ".then(d => d && d.demo_code ? 'real' : 'wrong:' + JSON.stringify(d))"
        ".catch(e => 'err:' + e.message)"
    )
    check("走真实后端（非本地 404 页面）", hit == "real", hit)
    check("骨架已渲染", c.js("!!document.querySelector('#view').children.length"))
    check("模拟状态栏已隐藏", c.js("getComputedStyle(document.getElementById('statusbar')).display === 'none'"))
    check("演示台已隐藏", c.js("getComputedStyle(document.getElementById('devpanel')).display === 'none'"))

    # 1. 从服务端重置演示数据，保证可重复
    r = c.js("window.__cm.api('/admin/reset', {method:'POST'}).then(r => r && 'ok').catch(e => 'err:' + e.message)")
    check("重置演示数据", r == "ok", r)

    # 2. 真实登录（走 /auth/code + /auth/verify）
    c.js("window.__cm.loginDemo('alex').then(() => 'ok')")
    c.wait_js("!!window.__cm.state.me", label="登录后 me")
    me = c.js("window.__cm.state.me.nickname")
    check("登录 alex", me == "Alex", me)

    # 3. Discover
    c.js("window.__cm.go('#/discover')")
    c.wait_js("!!document.querySelector('.card, [data-like]') || document.body.innerText.includes('Discover')")
    # 列表是异步拉取的，必须等到 Loading 消失再断言文案，否则读到的是骨架态
    c.wait_js("!document.getElementById('view').innerText.includes('Loading')", label="Discover 列表加载完成")
    txt = c.js("document.getElementById('view').innerText")
    check("Discover 渲染", "Discover" in txt or "For you" in txt)
    check("可解释推荐文案", "Never points or sensitive traits" in txt or "Shared:" in txt or "No more" in txt, txt[:160])

    # 4. 双向喜欢 -> 匹配
    pair = c.js(
        """(async () => {
          const a = window.__cm.state.me.id;
          const disc = await window.__cm.api('/discover');
          const target = disc.items[0];
          await window.__cm.loginDemo('sam');
          const samId = window.__cm.state.me.id;
          await window.__cm.api('/likes', {method:'POST', body:{candidate_id:a, type:'like'}});
          await window.__cm.loginDemo('alex');
          const r = await window.__cm.api('/likes', {method:'POST', body:{candidate_id:samId, type:'like'}});
          return JSON.stringify({match: r.match && (r.match.id || r.match.match_id), target: target && target.nickname});
        })()"""
    )
    mid = json.loads(pair).get("match")
    check("双向喜欢生成匹配", bool(mid), pair)

    # 5. 聊天：AI 只回填不发送
    c.js(f"window.__cm.go('#/chat/{mid}')")
    c.wait_js("!!document.querySelector('#aiBtn')", label="AI 按钮")
    check("匹配后无自动发送", c.js("document.querySelectorAll('.bubble').length") == 0)
    c.js("document.getElementById('aiBtn').click()")
    c.wait_js("!!document.querySelector('#ice')", label="破冰入口")
    c.js("document.getElementById('ice').click()")
    c.wait_js("!!document.querySelector(\"[data-sug='0']\")", label="建议列表", timeout=30)
    sug = c.js("document.querySelector(\"[data-sug='0']\").innerText.split('\\n')[0].trim()")
    c.js("document.querySelector(\"[data-sug='0']\").click()")
    c.wait_js(f"document.getElementById('msg').value.length > 0", label="回填输入框")
    val = c.js("document.getElementById('msg').value")
    check("AI 建议只回填输入框", val == sug, f"{val!r} vs {sug!r}")
    check("回填后仍未发送", c.js("document.querySelectorAll('.bubble').length") == 0)

    # 6. 用户点击发送
    c.js("document.getElementById('send').click()")
    c.wait_js("document.querySelectorAll('.bubble.me').length >= 1", label="发送后出现气泡")
    check("用户点击后发送成功", True)

    # 7. 通知 / 积分 / 设置页可达
    c.js("window.__cm.go('#/points')")
    c.wait_js("location.hash === '#/points' && document.getElementById('view').innerText.includes('points')",
              label="#/points")
    pts = c.js("document.getElementById('view').innerText")
    check("积分页声明不影响推荐", "never affect" in pts.lower() or "no ranking effect" in pts.lower(), pts[:120])
    for route, sel in [("#/privacy", "#view"), ("#/settings", "#rm")]:
        c.js(f"window.__cm.go('{route}')")
        c.wait_js(f"location.hash === '{route}' && !!document.querySelector('{sel}')", label=route)
    check("隐私/设置页可打开", True)

    # 7.5 Chats 列表页（Tab 必须有独立路由，否则会被重定向回 Home）
    # 注意：这里不能直接写 state.tab = null。只改字段不改 DOM，底部 Tab 的 .active 高亮
    # 还停在上一页，后面的真实点击会先被 setActiveTab 刷成一致的 tab，导致路由被切走。
    c.js("window.__cm.go('#/chats')")
    c.wait_js("location.hash === '#/chats'", label="#/chats")
    c.wait_js("!!document.querySelector('[data-mid], #chatList .empty')", label="会话列表")
    check("Chats 列表可打开", True, c.js("document.getElementById('view').innerText.slice(0,80)"))

    # 7.6 底部 Tab 的真实行为：点别的 Tab 必须换页；点当前 Tab 只回顶部、不换页
    c.js("document.querySelector('#tabbar button[data-tab=me]').click()")
    c.wait_js("location.hash === '#/me'", label="点 Me Tab")
    check("点底部 Tab 能切页", True)
    c.js("window.__cm.go('#/chats')")
    c.wait_js("location.hash === '#/chats'", label="回到 chats")
    c.js("document.querySelector('#tabbar button[data-tab=chats]').click()")
    js_err_before = c.js("(window.__errs || []).length")
    time.sleep(0.8)
    check("点当前 Tab 不换页", c.js("location.hash") == "#/chats", c.js("location.hash"))
    check("点当前 Tab 无异常", c.js("(window.__errs || []).length") == js_err_before)

    # 8. 无 JS 异常
    js_errors = c.js("JSON.stringify((window.__errs || []))")
    check("页面无未捕获异常", js_errors in ("[]", None), js_errors or "")

    failed = [n for n, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("FAILED:", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())