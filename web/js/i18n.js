// 界面语言（i18n）
//
// 范围：只翻译「界面骨架」——导航、标题、按钮、空状态、设置项等固定文案。
// 不翻译：用户产出的内容（昵称、聊天、房间名、AI 建议、兴趣标签）以及后端返回的
// 业务提示语。翻译用户内容会造假；翻译后端提示要先改后端，不在这一步。
//
// 用法：t('nav.home')；带参数 t('greet.hi', { name: 'Alex' })。
// 语言存 localStorage.cm_lang，<html lang> 同步更新（读屏和系统字体回退都依赖它）。

export const LANGS = [
  ['en', 'English'],
  ['zh', '中文'],
  ['ja', '日本語'],
  ['es', 'Español'],
];

const STRINGS = {
  en: {
    'nav.home': 'Home',
    'nav.discover': 'Discover',
    'nav.rooms': 'Rooms',
    'nav.chats': 'Chats',
    'nav.me': 'Me',
    'common.back': 'Back',
    'common.ok': 'OK',
    'common.cancel': 'Cancel',
    'common.save': 'Save',
    'common.loading': 'Loading…',
    'common.empty': 'Nothing here yet',
    'splash.tagline': 'Find a little moment.',
    'splash.booting': 'Starting local service…',
    'splash.bootFailed': 'Local service failed to start, please restart the app',
    'splash.start': 'Get started',
    'splash.have': 'I already have an account',
    'splash.adults': 'For adults 18+ · Your space, your choice.',
    'splash.demo': 'Demo accounts (no password):',
    'settings.title': 'Settings',
    'settings.account': 'Account',
    'settings.language': 'Language',
    'settings.notifications': 'Notifications',
    'settings.on': 'On',
    'settings.off': 'Off',
    'settings.blocked': 'Blocked users',
    'settings.prefs': 'App preferences',
    'settings.reduceMotion': 'Reduce motion',
    'settings.dataSaver': 'Data saver',
    'settings.ai': 'AI suggestions',
    'settings.support': 'Support',
    'settings.terms': 'Terms & privacy',
    'settings.about': 'About CrossMate',
    'settings.logout': 'Log out',
    'lang.saved': 'Language saved',
  },
  zh: {
    'nav.home': '首页',
    'nav.discover': '发现',
    'nav.rooms': '房间',
    'nav.chats': '聊天',
    'nav.me': '我的',
    'common.back': '返回',
    'common.ok': '好的',
    'common.cancel': '取消',
    'common.save': '保存',
    'common.loading': '加载中…',
    'common.empty': '这里还没有内容',
    'splash.tagline': '找一个轻松开始的瞬间',
    'splash.booting': '正在启动本地服务…',
    'splash.bootFailed': '本地服务未能启动，请重启 App',
    'splash.start': '开始使用',
    'splash.have': '我已有账号',
    'splash.adults': '面向 18 岁以上用户 · 你的空间，你做主。',
    'splash.demo': '演示账号（无需密码）：',
    'settings.title': '设置',
    'settings.account': '账号',
    'settings.language': '语言',
    'settings.notifications': '通知',
    'settings.on': '开启',
    'settings.off': '关闭',
    'settings.blocked': '已屏蔽用户',
    'settings.prefs': '应用偏好',
    'settings.reduceMotion': '减少动态效果',
    'settings.dataSaver': '节省流量',
    'settings.ai': 'AI 建议',
    'settings.support': '支持',
    'settings.terms': '条款与隐私',
    'settings.about': '关于 CrossMate',
    'settings.logout': '退出登录',
    'lang.saved': '语言已切换',
  },
  ja: {
    'nav.home': 'ホーム',
    'nav.discover': '発見',
    'nav.rooms': 'ルーム',
    'nav.chats': 'チャット',
    'nav.me': 'マイページ',
    'common.back': '戻る',
    'common.ok': 'OK',
    'common.cancel': 'キャンセル',
    'common.save': '保存',
    'common.loading': '読み込み中…',
    'common.empty': 'まだ何もありません',
    'splash.tagline': 'ちょっとした瞬間を探そう。',
    'splash.booting': 'ローカルサービスを起動中…',
    'splash.bootFailed': 'ローカルサービスを起動できませんでした。アプリを再起動してください',
    'splash.start': 'はじめる',
    'splash.have': 'アカウントをお持ちの方',
    'splash.adults': '18歳以上の方へ · あなたの空間、あなたの選択。',
    'splash.demo': 'デモアカウント（パスワード不要）：',
    'settings.title': '設定',
    'settings.account': 'アカウント',
    'settings.language': '言語',
    'settings.notifications': '通知',
    'settings.on': 'オン',
    'settings.off': 'オフ',
    'settings.blocked': 'ブロックしたユーザー',
    'settings.prefs': 'アプリの設定',
    'settings.reduceMotion': '動きを減らす',
    'settings.dataSaver': 'データ節約',
    'settings.ai': 'AI 提案',
    'settings.support': 'サポート',
    'settings.terms': '規約とプライバシー',
    'settings.about': 'CrossMate について',
    'settings.logout': 'ログアウト',
    'lang.saved': '言語を変更しました',
  },
  es: {
    'nav.home': 'Inicio',
    'nav.discover': 'Descubrir',
    'nav.rooms': 'Salas',
    'nav.chats': 'Chats',
    'nav.me': 'Yo',
    'common.back': 'Atrás',
    'common.ok': 'OK',
    'common.cancel': 'Cancelar',
    'common.save': 'Guardar',
    'common.loading': 'Cargando…',
    'common.empty': 'Aún no hay nada',
    'splash.tagline': 'Encuentra un pequeño momento.',
    'splash.booting': 'Iniciando el servicio local…',
    'splash.bootFailed': 'El servicio local no pudo iniciarse, reinicia la app',
    'splash.start': 'Empezar',
    'splash.have': 'Ya tengo una cuenta',
    'splash.adults': 'Para mayores de 18 · Tu espacio, tu elección.',
    'splash.demo': 'Cuentas de demostración (sin contraseña):',
    'settings.title': 'Ajustes',
    'settings.account': 'Cuenta',
    'settings.language': 'Idioma',
    'settings.notifications': 'Notificaciones',
    'settings.on': 'Activadas',
    'settings.off': 'Desactivadas',
    'settings.blocked': 'Usuarios bloqueados',
    'settings.prefs': 'Preferencias de la app',
    'settings.reduceMotion': 'Reducir movimiento',
    'settings.dataSaver': 'Ahorro de datos',
    'settings.ai': 'Sugerencias de IA',
    'settings.support': 'Soporte',
    'settings.terms': 'Términos y privacidad',
    'settings.about': 'Acerca de CrossMate',
    'settings.logout': 'Cerrar sesión',
    'lang.saved': 'Idioma actualizado',
  },
};

const KEY = 'cm_lang';

function detect() {
  // 默认跟随系统语言；系统是中文之外的都退回英文（演示账号、房间名都是英文内容）
  let sys = '';
  try {
    sys = (navigator.language || '').toLowerCase();
  } catch (e) { /* 某些 WebView 环境拿不到 */ }
  for (const [code] of LANGS) {
    if (code !== 'en' && sys.startsWith(code)) return code;
  }
  return 'en';
}

let current = (() => {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && STRINGS[saved]) return saved;
  } catch (e) { /* localStorage 被禁用时走系统默认 */ }
  return detect();
})();

function applyDocumentLang() {
  try {
    document.documentElement.lang = current;
  } catch (e) { /* 忽略 */ }
}

export function t(key, vars) {
  const dict = STRINGS[current] || STRINGS.en;
  let s = dict[key] ?? STRINGS.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}

export const getLang = () => current;
export const langName = (code = current) => (LANGS.find(([c]) => c === code) || LANGS[0])[1];

/** 切换语言：写入本地存储、更新 <html lang>，并立刻重渲染当前路由。 */
export function setLang(code) {
  if (!STRINGS[code]) return false;
  current = code;
  try {
    localStorage.setItem(KEY, code);
  } catch (e) { /* 隐私模式：本次会话内仍生效 */ }
  applyDocumentLang();
  return true;
}

/** 按 data-i18n="key" 批量替换文本（静态元素用）。 */
export function applyStatic(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
}

applyDocumentLang();