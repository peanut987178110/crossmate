// 从 ../web 同步前端资源到 www，并注入壳专用文件（单一来源，避免两份代码漂移）
import { cpSync, mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, '..', 'web');
const dst = join(root, 'www');
const shellCss = join(dst, 'css', 'shell.css');
const shellJs = join(dst, 'js', 'shell.js');
const keepCss = existsSync(shellCss) ? readFileSync(shellCss, 'utf8') : '';
const keepJs = existsSync(shellJs) ? readFileSync(shellJs, 'utf8') : '';

rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
cpSync(join(src, 'css'), join(dst, 'css'), { recursive: true });
cpSync(join(src, 'js'), join(dst, 'js'), { recursive: true });
writeFileSync(shellCss, keepCss);
writeFileSync(shellJs, keepJs);

let html = readFileSync(join(src, 'index.html'), 'utf8');
html = html.replace('<link rel="stylesheet" href="css/app.css">',
  '<link rel="stylesheet" href="css/app.css">\n<link rel="stylesheet" href="css/shell.css">');
html = html.replace('<title>CrossMate · Find a little moment</title>',
  '<title>CrossMate · Find a little moment</title>\n<script src="js/shell.js"></script>');
// 默认后端地址：App 内置的 Python 后端（Chaquopy 在设备本机 127.0.0.1:8765 起服务）。
// 写在 <head>，由 shell.js 同步读取写回 meta[name=cm-api]——必须早于 app.js 定死 API 常量。
// 对着电脑上的后端联调时用 adb -e cm_api "http://<IP>:8000/api/v0.2" 覆盖（写入 localStorage，优先级更高）。
const DEFAULT_API = process.env.CM_DEFAULT_API || 'http://127.0.0.1:8765/api/v0.2';
html = html.replace('<meta name="cm-api" content="">',
  `<meta name="cm-api" content="">\n<meta name="cm-api-default" content="${DEFAULT_API}">`);
writeFileSync(join(dst, 'index.html'), html);
console.log('www synced from ../web');

// 后端 Python 源码同样是"单一来源"：APK 里打的必须就是 backend/app。
// 以前这一份是手工拷进去的，改完后端忘记拷 → 打包出来还是旧逻辑，且没有任何报错。
// 这里每次同步都整目录覆盖，避免两份代码漂移。
const pySrc = join(root, '..', 'backend', 'app');
const pyDst = join(root, 'android', 'app', 'src', 'main', 'python', 'app');
if (existsSync(pySrc)) {
  rmSync(pyDst, { recursive: true, force: true });
  cpSync(pySrc, pyDst, { recursive: true, filter: (s) => !s.includes('__pycache__') });
  console.log('backend/app synced into android/src/main/python/app');
} else {
  console.warn('WARN: 找不到 ../backend/app，APK 里的后端不会被更新');
}
