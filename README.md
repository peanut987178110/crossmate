# CrossMate

> 面向海外青年的轻社交 App 原型：**18 个页面 + REST API + 确定性 AI + 安卓离线安装包**。
> 定位是给准备「社交产品经理 / AI 产品经理」面试的人，一个能**从需求讲到实现再讲回数据**的完整案例。

一个能真正跑起来的作品，不是 PPT：后端 57 个接口测试、6 个主流程 E2E、12 个逐控件可点性测试、
21 项安卓真实 App 烟雾断言，全部可复现。

---

## 30 秒跑起来（只要 Python 3.10+）

```bash
cd backend
python -m pip install -r requirements.txt
python -m app.main                      # 默认 http://127.0.0.1:8000
```

打开 **http://127.0.0.1:8000/** 即可（会自动跳到 `/app/index.html`）。

- 启动页上有 6 个演示账号可以一键登录（`alex / sam / maya / kenji / lina / nadia`），另有 `omar / leo` 可在右侧演示台切换
- API 文档：http://127.0.0.1:8000/docs
- 端口被占用：`PORT=8010 python -m app.main`（PowerShell：`$env:PORT=8010; python -m app.main`）
- 前端是**无构建**的原生 JS，改完刷新浏览器即可，不需要 npm

## 手机上直接装（不需要电脑，不需要网络）

`CrossMate-0.2.0-debug.apk`（约 41 MB，实测 40.7 MB）在仓库根目录。传到安卓手机安装后直接打开——
后端（FastAPI）由 Chaquopy 一起打进了 APK，App 启动时在本机 `127.0.0.1:8765` 跑起来，
SQLite 存在 App 私有目录。**飞行模式下也能用。**

支持 ABI：`arm64-v8a`、`x86_64`。Android 7.0（API 24）及以上。

> APK 是 debug 签名，只用于演示，不能上架应用商店。

## 仓库结构

```
backend/     FastAPI + SQLite，确定性 Mock AI（无外部依赖，离线可测）
web/         原生 ES module SPA（无打包、无框架），18 个页面
e2e/         Playwright 端到端 + 逐控件可点性测试
app-shell/   Capacitor 安卓壳 + Chaquopy 内嵌 Python
docs/        产品文档（PRD / AI 工作流 / Prompt / 指标 / 测试报告 / 方法库 / 学习指南）
页面/        25 张 PNG 原型图
screenshots/ 实现截图（网页端 + 模拟器实拍）
```

## 跑测试

```bash
# 接口测试（57 个，~10 秒）
cd backend && python -m pytest -q

# 端到端（6 个主流程 + 12 个可点性，~4 分钟）
cd e2e
pip install -r requirements.txt
python -m playwright install chromium
PYTHON_FOR_APP=$(which python) python -m pytest test_e2e.py -q
python -m pytest test_clickable.py -q
```

`test_clickable.py` 会把 11 个页面里所有"看起来能点"的控件真实点一遍。加它是因为上线前发现
设置里的「语言」那一行带着 `›` 箭头却**完全没绑定事件**——主流程测试再绿也照不出来。

## 自己打安卓包

需要：JDK 17、Android SDK、NDK 26.x、本机 Python 3.12（Chaquopy 的 `buildPython`）。

```bash
cd app-shell
npm install
npm run sync           # 同步 web/ 和 backend/ 进壳
cd android && ./gradlew assembleDebug
```

产物：`app-shell/android/app/build/outputs/apk/debug/app-debug.apk`

`npm run sync` 做了两件事：把 `web/` 生成到 `www/`（并注入壳专用的 `shell.js`），
把 `backend/app` 覆盖到 `android/app/src/main/python/app`。**这两份都是从源码生成的，不要手改**——
只跑 `gradlew` 而不跑 `sync` 会把旧代码打进包里。

## 文档

从 [docs/00-项目总览.md](docs/00-项目总览.md) 开始，那里有完整的文档地图。

| 想了解 | 读这个 |
|---|---|
| 产品怎么来的、做了什么取舍 | [docs/01-PRD-v0.2.md](docs/01-PRD-v0.2.md)（含 14 条设计修正） |
| AI 怎么接、怎么防越界 | [docs/02-Skills与AI工作流.md](docs/02-Skills与AI工作流.md)、[docs/03-Prompt与RAG.md](docs/03-Prompt与RAG.md) |
| 指标怎么定口径 | [docs/04-指标与数据看板.md](docs/04-指标与数据看板.md) |
| 测了什么、修了哪些坑 | [docs/05-测试报告.md](docs/05-测试报告.md)（D1–D27） |
| 想从零复刻一遍 | [docs/07-学习指南.md](docs/07-学习指南.md)（8 天路线） |

## 几条硬红线（都有测试守着）

- AI 只把建议**回填到输入框**，发送必须由用户点——不代替用户说话，不冒充真人
- 无未成年人；18+ 与条款双重确认
- 积分**不影响推荐排序**，也不能购买曝光
- 不录音、不回放、不转写
- 举报不发积分（防止恶意举报），只存脱敏证据
- 日志与埋点不含邮箱、token、聊天原文

## License

MIT，见 [LICENSE](LICENSE)。