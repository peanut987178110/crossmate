"""在已安装的真机/模拟器 App 里验证语言切换，共 8 项断言。

用 CDP 的 Input.dispatchTouchEvent 发**真实触摸**，而不是 evaluate('...click()')——
后者绕过了命中区域、遮挡和手势层，历史上正是因此漏掉了"按钮只有 17px 宽"这类问题。

前置：App 在前台（APK 自带后端，不需要启动电脑 backend），且已做端口转发：
    adb forward tcp:9223 localabstract:webview_devtools_remote_<pid>

依赖：pip install websocket-client

运行：python tools/check_lang_device.py [端口，默认 9223]
      端口要和上面 forward 的本地端口一致；smoke_android.py 用的是 9222，两个可以同时开着。
"""
import json
import sys
import time
import urllib.request

import websocket

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9223


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
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

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:500])
        return r.get("result", {}).get("value")

    def tap(self, x, y):
        """真实触摸事件（走 Input 域），和手指按下等价。"""
        for t in ("touchStart", "touchEnd"):
            self.call("Input.dispatchTouchEvent", type=t,
                      touchPoints=[{"x": x, "y": y, "radiusX": 1, "radiusY": 1}] if t == "touchStart" else [],)

    def wait_js(self, expr, timeout=20, label=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.25)
        raise AssertionError(f"wait timeout: {label or expr}")

    def center(self, sel):
        v = self.js(f"""(()=>{{const e=document.querySelector({json.dumps(sel)});
            if(!e)return null; const r=e.getBoundingClientRect();
            return {{x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height}};}})()""")
        if not v:
            raise AssertionError(f"找不到元素 {sel}")
        return v


def main():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        target = next(p for p in json.loads(r.read()) if p["type"] == "page")
    c = CDP(target["webSocketDebuggerUrl"])
    c.call("Runtime.enable")

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok)))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

    c.wait_js("!!window.__cm && !!document.querySelector('#view')", label="页面加载")
    # 干净起点：清掉语言偏好，按正常流程点演示账号登录
    c.js("localStorage.removeItem('cm_lang'); 'ok'")
    c.js("window.__cm.state.token ? 'in' : 'out'")
    if c.js("!window.__cm.state.token"):
        b = c.center("[data-demo='alex']")
        c.tap(b["x"], b["y"])
        c.wait_js("!!window.__cm.state.me", timeout=40, label="点账号后登录")
    c.js("window.__cm.go('#/settings'); 'ok'")
    c.wait_js("!!document.querySelector('#langRow')", label="设置页")

    tabs_en = [c.js(f"document.querySelectorAll('#tabbar span')[{i}].innerText") for i in range(5)]
    check("初始为英文 Tab", tabs_en[:5] == ["Home", "Discover", "Rooms", "Chats", "Me"], tabs_en)

    # 真实触摸：点 Language 行
    b = c.center("#langRow")
    c.tap(b["x"], b["y"])
    c.wait_js("!!document.querySelector(\"[data-lang='zh']\")", timeout=8, label="语言弹层")
    check("点击 Language 打开语言选择", True, f"行尺寸 {b['w']:.0f}x{b['h']:.0f}")

    b2 = c.center("[data-lang='zh']")
    c.tap(b2["x"], b2["y"])
    time.sleep(1.2)

    tabs_zh = [c.js(f"document.querySelectorAll('#tabbar span')[{i}].innerText") for i in range(5)]
    html_lang = c.js("document.documentElement.lang")
    saved = c.js("localStorage.getItem('cm_lang')")
    row = c.js("document.getElementById('langRow').innerText")
    check("切中文后 Tab 变中文", tabs_zh == ["首页", "发现", "房间", "聊天", "我的"], tabs_zh)
    check("html lang = zh", html_lang == "zh", html_lang)
    check("偏好已持久化", saved == "zh", saved)
    check("设置行显示当前语言", "中文" in (row or ""), row)

    # 重启进程后是否还记得（真正落到存储，而非只在内存）
    c.js("location.reload(); 'ok'")
    time.sleep(4)
    c.wait_js("!!window.__cm && !!document.querySelector('#tabbar')", timeout=30, label="重载")
    tabs_after = [c.js(f"document.querySelectorAll('#tabbar span')[{i}].innerText") for i in range(5)]
    check("页面重载后仍是中文", tabs_after == ["首页", "发现", "房间", "聊天", "我的"], tabs_after)

    # 切回英文，确认双向可用（不是一次性）
    c.js("window.__cm.go('#/settings'); 'ok'")
    c.wait_js("!!document.querySelector('#langRow')", label="设置页")
    b = c.center("#langRow")
    c.tap(b["x"], b["y"])
    c.wait_js("!!document.querySelector(\"[data-lang='en']\")", timeout=8, label="语言弹层")
    b2 = c.center("[data-lang='en']")
    c.tap(b2["x"], b2["y"])
    time.sleep(1.0)
    tabs_back = [c.js(f"document.querySelectorAll('#tabbar span')[{i}].innerText") for i in range(5)]
    check("能切回英文", tabs_back == ["Home", "Discover", "Rooms", "Chats", "Me"], tabs_back)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())