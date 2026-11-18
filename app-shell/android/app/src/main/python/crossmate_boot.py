"""CrossMate 安卓壳的后端启动器（由 Chaquopy 在设备上执行）。

设计要点
--------
1. **只读源码 + 可写数据**：APK 里的 Python 源码位于只读的 assets 目录，
   SQLite 需要写权限，所以数据库放到 App 的文件目录（files/backend/data/）。
2. **首次启动播种**：数据库不存在时由 app.seed 写入 8 个演示账号 + 3 个语音房。
3. **后台线程跑 uvicorn**：主线程要留给 WebView，服务监听 127.0.0.1，
   只能被本机访问（不暴露到 WiFi）。
4. **幂等**：重复调用 start() 不会起第二个服务。

调试：adb logcat -s CrossMatePy
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("CrossMatePy")

PORT = 8765
_started = threading.Event()
_error: str = ""


def _prepare_env(files_dir: str) -> None:
    """把环境变量和数据库路径指向 App 的可写目录。"""
    data_dir = os.path.join(files_dir, "backend", "data")
    os.makedirs(data_dir, exist_ok=True)
    os.environ.setdefault("CROSSMATE_DB", os.path.join(data_dir, "crossmate.db"))
    os.environ.setdefault("CROSSMATE_AI_MODE", "mock")  # 离线演示：不调用外部模型
    os.environ.setdefault("CROSSMATE_SEED", "1")
    os.environ.setdefault("CROSSMATE_ALLOW_RESET", "1")  # 演示台可一键重置


def _run_server() -> None:
    global _error
    try:
        import uvicorn

        from app.main import app  # noqa: F401  （导入即注册路由与静态目录）

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="warning",
            access_log=False,
            # 手机 CPU 核少，单 worker 足够；loop 用 asyncio（uvloop 无 Android 轮子）
        )
        server = uvicorn.Server(config)
        _started.set()
        server.run()
    except Exception as exc:  # pragma: no cover - 设备上执行
        _error = repr(exc)
        log.exception("backend failed: %s", exc)
        _started.set()


def start(files_dir: str) -> dict:
    """启动后端起服务。返回 {"port": int, "error": str}。可重复调用。"""
    _prepare_env(files_dir)
    if not _started.is_set():
        threading.Thread(target=_run_server, name="crossmate-api", daemon=True).start()
    _started.wait(timeout=30)
    return {"port": PORT, "error": _error}


def wait_ready(timeout: float = 30.0) -> bool:
    """轮询 /healthz，确认服务真的能应答（而不只是线程起来了）。"""
    import urllib.error
    import urllib.request

    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def info() -> dict:
    """给原生侧做状态展示用。"""
    return {"port": PORT, "started": _started.is_set(), "error": _error}