# 传到 GitHub（在另一台电脑上操作）

仓库已经在 `F:\CrossMate` 初始化好了，**包含一个完整的初始提交**。下面三种方式任选一种，都是"拷过去 → 推上去"，不需要重新 init、不需要重新提交。

> 三种方式里，**方式 A 最省事**（直接把整个文件夹拷走）。

---

## 方式 A：直接拷整个文件夹（推荐）

把 **`F:\CrossMate` 整个文件夹** 拷到 U 盘 / 移动硬盘 / 网盘，在新电脑上放到任意位置（比如 `D:\CrossMate`）。

`.git` 目录是隐藏的，Windows 默认不显示——拷的时候用命令，别用资源管理器的拖拽，免得漏掉：

```powershell
# 在旧电脑上（把 U 盘的 X: 换成你的盘符）
robocopy F:\CrossMate X:\CrossMate /E /XD node_modules .gradle build
```

新电脑上：

```powershell
cd D:\CrossMate
git log --oneline          # 应该能看到 1 条提交：CrossMate v0.2.4...
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

---

## 方式 B：用 bundle 文件（最小，34 MB）

`F:\CrossMate.bundle` 已经生成好并校验通过，里面是**完整历史**（不是压缩包，是 git 自己的打包格式，`.git` 全在里面）。

新电脑上：

```powershell
git clone F:\CrossMate.bundle CrossMate
cd CrossMate
git remote remove origin
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

> 别用 `git push origin main` 之前先设好 `git config user.name / user.email`——那台电脑如果没配，提交记录会显示成 `unknown`。已有提交的作者信息不会变，这里只是为以后的提交准备。

---

## 方式 C：zip 压缩包（约 40 MB）

`F:\CrossMate-源码.zip`，解压后是同样的文件夹（含 `.git`）。

```powershell
# 新电脑：解压后
cd CrossMate
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

---

## 推之前，先在 GitHub 上建仓库

1. 打开 https://github.com/new
2. Repository name 填 `crossmate`（或你喜欢的）
3. **不要**勾 "Add a README file"、不要选 .gitignore、不要选 license——本地已经有了，勾了会导致推送冲突
4. Public / Private 随你
5. 建完页面会给出 `https://github.com/xxx/crossmate.git`，就是上面命令里的 `<仓库名>.git`

---

## 关于 `.gitattributes`（已自带，无需操作）

仓库里已经带好了 `.gitattributes`：
- `.bat` / `.ps1` 按 CRLF 检出，`gradlew` / `.sh` 按 LF（否则 Linux 上 `./gradlew` 直接报 `bad interpreter`）
- `gradlew` 已经设了可执行位
- `.png` / `.apk` 标记为二进制

---

## 没有传输介质时：用本机当临时服务器

两台电脑在同一 WiFi 下，旧机器：

```powershell
cd F:\
python -m http.server 8080
```

新机器浏览器打开 `http://<旧机器IP>:8080/CrossMate.bundle` 下载，然后走方式 B。

---

## 推不上去的常见原因

| 报错 | 原因 | 处理 |
|---|---|---|
| `remote: Support for password authentication was removed` | GitHub 不再接受账号密码 | 用 Personal Access Token 当密码（github.com → Settings → Developer settings → Personal access tokens → Fine-grained token，勾 `Contents: Read and write`），或者装 GitHub Desktop / 用 SSH |
| `Updates were rejected because the remote contains work...` | GitHub 建仓库时勾了 README/license | `git pull --rebase origin main` 后再 push；或者干脆建个空仓库重来 |
| `Filename too long`（Windows） | 安卓工程里有长路径 | `git config --global core.longpaths true` |
| `fatal: bad object` / 仓库损坏 | 拷贝时漏了 `.git` 里的文件 | 重拷，用方式 A 的 `robocopy` 命令 |

---

## 推上去之后

- 仓库里 **不含** `node_modules/`、`.gradle/`、`android/app/build/`、`www/`、`android/app/src/main/python/app/`、`local.properties`、`backend/data/*.db*`——这些都是本机生成的，`.gitignore` 已经挡掉了
- 别人 clone 下来照 [README.md](README.md) 操作即可跑起来
- 那 42 MB 的 APK **在里面**（GitHub 单文件上限 100 MB），clone 完就能直接拿到安装包
- 想重新打安卓包：`cd app-shell && npm install && npm run sync && cd android && ./gradlew.bat assembleDebug`