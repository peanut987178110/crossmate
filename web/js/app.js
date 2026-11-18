// CrossMate Web Demo —— 零依赖 ES Module SPA
// 所有业务规则都在后端（FastAPI），前端只负责：渲染 / 调接口 / 不伪造发送与接通。
// 原生壳（Capacitor）里把 <meta name="cm-api"> 指向局域网后端；网页演示仍用同源
const API = (document.querySelector('meta[name="cm-api"]')?.content || '').replace(/\/+$/, '') || '/api/v0.2';
import { t, getLang, setLang, langName, LANGS } from './i18n.js';
const $ = (sel, root = document) => root.querySelector(sel);
const view = $('#view');
const tabbar = $('#tabbar');

let aiEnabled = localStorage.getItem('cm_ai') !== '0';

const state = {
  token: localStorage.getItem('cm_token') || '',
  me: null,
  tab: 'home',
  scroll: {},
  discover: { items: [], idx: 0, tab: 'for_you', quota: 3 },
  match: null,
  aiGen: null,
  draft: { mid: null, text: '' },
  aiSubmitted: false,
  room: null,
  call: null,
  callTimer: null,
  devices: JSON.parse(localStorage.getItem('cm_devices') || '{}'),
};

// ---------------- API ----------------
// 安卓壳里后端是本机 Python 进程，冷启动要 4~5 秒，页面往往会先于它就绪。
// 这时 fetch 会直接抛 "Failed to fetch"。与其把这个英文错误甩给用户，
// 不如在启动窗口内自动重试，并且让调用方能识别出"还在启动"。
const BOOT_RETRY_MS = 12000;

async function api(path, { method = 'GET', body, quiet, bootRetry = true } = {}) {
  const headers = { 'Content-Type': 'application/json', 'X-Request-Id': crypto.randomUUID() };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const deadline = Date.now() + BOOT_RETRY_MS;
  let res;
  for (;;) {
    try {
      res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
      break;
    } catch (err) {
      // 只对"连不上本机后端"重试；其余网络错误原样抛出
      if (!bootRetry || Date.now() > deadline) {
        const e = new Error('服务正在启动，请稍候再试');
        e.code = 'BOOTING';
        throw e;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  const json = await res.json().catch(() => ({ code: -1, message: 'network' }));
  if (!quiet) devlog(`${method} ${path} → ${res.status}${json.code ? ' code=' + json.code : ''}`);
  if (res.status === 401 && !path.startsWith('/auth')) {
    logoutLocal();
    throw new Error(json.message || 'session expired');
  }
  if (!res.ok) {
    const e = new Error(json.message || `HTTP ${res.status}`);
    e.code = json.code;
    e.data = json.data;
    e.status = res.status;
    throw e;
  }
  return json.data;
}

function devlog(line) {
  const el = $('#devlog');
  if (!el) return;
  const t = new Date().toLocaleTimeString();
  el.innerHTML = `<div>[${t}] ${escapeHtml(line)}</div>` + el.innerHTML;
}

const escapeHtml = (s = '') => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), ms);
}

function modal(html) {
  const m = $('#modal');
  m.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  m.hidden = false;
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = closeModal));
  return m;
}
function closeModal() { $('#modal').hidden = true; $('#modal').innerHTML = ''; }

// ---------------- 渲染工具 ----------------
function top({ back, title, right = '', brand = false } = {}) {
  return `<div class="top">
    ${back ? `<button class="back" data-back aria-label="返回">‹</button>` : '<span></span>'}
    ${brand ? `<span class="brand">Cross<i>Mate</i></span>` : `<span class="${back ? 'title' : 'brand'}" style="${back ? 'font-size:18px' : ''}">${title || ''}</span>`}
    <span>${right}</span>
  </div>`;
}

const avatar = (name = '?', extra = '') => `<div class="avatar ${extra}" aria-hidden="true">${escapeHtml((name || '?')[0].toUpperCase())}</div>`;
const initial = (s) => (s || '?')[0].toUpperCase();

const TAB_LABEL = { home: 'nav.home', discover: 'nav.discover', rooms: 'nav.rooms', chats: 'nav.chats', me: 'nav.me' };

function setActiveTab(tab) {
  tabbar.hidden = !state.token; // 未登录不显示底部导航
  tabbar.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tab);
    const label = b.querySelector('span');
    if (label && TAB_LABEL[b.dataset.tab]) label.textContent = t(TAB_LABEL[b.dataset.tab]);
  });
}

/** 切换语言后整页重渲染：当前路由原样再走一遍，滚动位置复位到顶。 */
function applyLang(code) {
  if (!setLang(code)) return;
  view.scrollTop = 0;
  route();
}

function go(route, opts) {
  if ($('#phone') && state.tab) state.scroll[state.tab] = view.scrollTop;
  window.location.hash = route;
  if (opts) state._opts = opts;
}

// ---------------- 路由 ----------------
const routes = [
  [/^#\/splash$/, renderSplash],
  [/^#\/login$/, renderLogin],
  [/^#\/profile$/, renderProfile],
  [/^#\/interests$/, renderInterests],
  [/^#\/home$/, renderHome],
  [/^#\/discover$/, renderDiscover],
  [/^#\/match\/(\w+)$/, renderMatch],
  [/^#\/chat\/(\w+)$/, renderChat],
  [/^#\/chats$/, renderChats],
  [/^#\/aitools\/(\w+)$/, renderAiTools],
  [/^#\/rooms$/, renderRooms],
  [/^#\/room\/(\w+)$/, renderRoom],
  [/^#\/call\/(\w+)$/, renderCall],
  [/^#\/notifications$/, renderNotifications],
  [/^#\/points$/, renderPoints],
  [/^#\/me$/, renderMe],
  [/^#\/privacy$/, renderPrivacy],
  [/^#\/report\/([\w:-]+)$/, renderReport],
  [/^#\/settings$/, renderSettings],
];

function parseHash(hash) {
  const [pathPart, query = ''] = hash.split('?');
  const queryObj = {};
  for (const [k, v] of new URLSearchParams(query)) queryObj[k] = v;
  return { path: pathPart, query: queryObj };
}

async function route() {
  const hash = window.location.hash || '#/splash';
  const { path, query } = parseHash(hash);
  if (!state.token && !['#/splash', '#/login'].some((p) => path.startsWith(p))) {
    window.location.hash = '#/splash';
    return;
  }
  if (state.callTimer && !path.startsWith('#/call/')) { clearInterval(state.callTimer); state.callTimer = null; }
  if (state.chatTimer && !path.startsWith('#/chat/')) { clearInterval(state.chatTimer); state.chatTimer = null; }
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) {
      tabbar.hidden = true;
      setActiveTab(state.tab); // 登录后切页也会进来：每次进路由都把 Tab 文案按当前语言刷一遍
      state.routeMatch = m[1] || '';
      if (!path.startsWith('#/chat/')) state.aiSubmitted = false;
      const opts = { ...(state._opts || {}), ...query };
      state._opts = null;
      try {
        await fn(...m.slice(1), opts);
      } catch (e) {
        view.innerHTML = top({ back: true }) + `<div class="empty"><div class="emoji">⚠️</div>${escapeHtml(e.message)}</div>`;
      }
      view.onclick = null;
      return;
    }
  }
  if (!state.token) {
    tabbar.hidden = true; // 登录/启动页不留底部导航
    return;
  }
  window.location.hash = '#/home';
}
window.addEventListener('hashchange', route);

// 全局返回 / 重按当前 Tab 回顶
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-back]')) history.length > 1 ? history.back() : go('#/home');
  if (e.target.closest('[data-go]')) go(e.target.closest('[data-go]').dataset.go);
});

tabbar.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]');
  if (!b) return;
  const tab = b.dataset.tab;
  if (state.tab === tab) { view.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  state.tab = tab;
  setActiveTab(tab); // 切语言后 Tab 文案要立刻跟着变，不等路由跑完
  go('#/' + tab);
});

// ---------------- 会话 ----------------
function saveToken(t) { state.token = t; localStorage.setItem('cm_token', t); }
function logoutLocal() {
  state.token = ''; state.me = null;
  localStorage.removeItem('cm_token');
  window.location.hash = '#/splash';
}

async function loginDemo(userKey) {
  const email = userKey.includes('@') ? userKey : `${userKey}@demo.crossmate.app`;
  const gate = { age_confirmed: true, terms_accepted: true };
  const verify = async (code) => {
    const r = await api('/auth/verify', { method: 'POST', body: { email, code, ...gate } });
    state.devices[email] = { code, uid: r.user_id };
    localStorage.setItem('cm_devices', JSON.stringify(state.devices));
    saveToken(r.access_token);
    await loadMe();
    return r;
  };
  // 验证码只缓存在本机；服务端被重置时会失效。最多尝试：缓存 code -> 新申请 -> 再新申请一次
  // 以 localStorage 为准：state.devices 是页面加载时的快照，别的标签页或清理操作改过之后它不会同步，
  // 拿过期快照里的旧码去验，只会白吃一次 429 限流，然后这一步看起来就像「登不进去」。
  try { state.devices = JSON.parse(localStorage.getItem('cm_devices') || '{}'); } catch (e) { state.devices = {}; }
  const recoverable = [40002, 40003, 40004, 42904];
  let lastErr = null;
  for (const code of [state.devices[email]?.code, null, null]) {
    let c = code;
    if (!c) {
      try {
        c = (await api('/auth/code', { method: 'POST', body: { email } })).demo_code;
      } catch (e) {
        if (e.code === 42901 && lastErr) throw lastErr;
        throw e;
      }
    }
    try {
      return await verify(c);
    } catch (e) {
      if (!recoverable.includes(e.code)) throw e;
      lastErr = e;
    }
  }
  throw lastErr;
}

async function loadMe() {
  state.me = await api('/users/me/profile');
  return state.me;
}

// ---------------- P01 启动 ----------------
async function renderSplash() {
  state.tab = 'splash';
  view.innerHTML = `<div class="hero fade">
    <div class="orb" aria-hidden="true">✦</div>
    <h2 style="margin:6px 0">CrossMate</h2>
    <p class="sub" id="tagline">${t('splash.tagline')}</p>
    <p class="sub" id="bootState" style="font-size:11px;opacity:.75">${t('splash.booting')}</p>
    <button class="btn" id="start" style="margin-top:26px">${t('splash.start')}</button>
    <button class="btn alt" id="have">${t('splash.have')}</button>
    <p class="sub" style="font-size:11px;margin-top:22px">${t('splash.adults')}</p>
    <div class="notice" style="text-align:left;margin-top:18px">
      <b>${t('splash.demo')}</b>
      <div class="chips" style="margin-top:6px">
        ${['alex', 'sam', 'maya', 'kenji', 'lina', 'nadia'].map((u) => `<button class="chip" data-demo="${u}">${u}</button>`).join('')}
      </div>
    </div>
  </div>`;
  view.querySelector('#start').onclick = () => go('#/login');
  view.querySelector('#have').onclick = () => go('#/login');

  // 探测本地后端是否就绪：壳里 Python 冷启动约 4~5 秒，期间点按钮会一直等。
  // 探活成功后把提示换成 tagline，用户就能明确知道"可以点了"。
  (async () => {
    const el = view.querySelector('#bootState');
    try {
      await api('/meta', { bootRetry: true, quiet: true });
      if (el && el.isConnected) el.textContent = t('splash.tagline');
    } catch (e) {
      if (el && el.isConnected) el.textContent = t('splash.bootFailed');
    }
  })();

  view.querySelectorAll('[data-demo]').forEach((b) => {
    b.onclick = async () => {
      const chips = view.querySelectorAll('[data-demo]');
      // 登录链路要串行走 /auth/code + /auth/verify（冷启动约 2-3 秒），必须有可见反馈
      chips.forEach((c) => { c.disabled = true; });
      b.textContent = '登录中…';
      b.classList.add('on');
      try {
        await loginDemo(b.dataset.demo);
        go(state.me?.profile_completed ? '#/home' : '#/profile');
      } catch (e) {
        toast(e.message);
        chips.forEach((c) => { c.disabled = false; });
        chips.forEach((c) => { c.textContent = c.dataset.demo; });
      }
    };
  });
}

// ---------------- P02 登录 ----------------
async function renderLogin() {
  state.tab = 'login';
  view.innerHTML = `${top({ back: true, brand: true })}
    <div class="title">Welcome back</div>
    <p class="sub">Sign in to keep your conversations.</p>
    <div class="input"><input id="email" type="email" inputmode="email" placeholder="Email address" aria-label="Email address" autocomplete="email"></div>
    <label class="sub" style="display:flex;gap:8px;align-items:center;margin:6px 0;min-height:44px">
      <input type="checkbox" id="adult"> 我已年满 18 岁</label>
    <label class="sub" style="display:flex;gap:8px;align-items:center;margin:6px 0;min-height:44px">
      <input type="checkbox" id="terms"> 我同意 <a href="#" style="color:var(--orange)">Terms</a> 与 Privacy Policy</label>
    <button class="btn" id="send">Send verification code</button>
    <div id="step2" hidden>
      <div class="input"><input id="code" inputmode="numeric" maxlength="6" placeholder="6-digit code" aria-label="verification code"></div>
      <button class="btn" id="verify">Verify & continue</button>
      <p class="sub" id="resend" style="font-size:12px">Didn't get it? <a href="#" id="resendLink">Resend</a></p>
    </div>
    <div class="row" style="margin:18px 0;color:#a99fb2"><span>────────</span><span>or</span><span>────────</span></div>
    <button class="btn alt" id="apple">Continue with Apple</button>
    <button class="btn alt" id="google">Continue with Google</button>
    <p class="sub" style="font-size:11px;margin-top:18px">By continuing, you agree to our <a href="#" id="termsLink">Terms & Privacy Policy</a>.</p>`;

  const gate = () => {
    if (!$('#adult').checked) throw new Error('请先确认已满 18 岁');
    if (!$('#terms').checked) throw new Error('请先同意条款与隐私政策');
    if (!$('#email').value.includes('@')) throw new Error('请输入有效邮箱');
  };
  $('#send').onclick = async () => {
    try {
      gate();
      const r = await api('/auth/code', { method: 'POST', body: { email: $('#email').value } });
      $('#step2').hidden = false;
      $('#code').value = r.demo_code;
      toast(`演示模式：验证码 ${r.demo_code}（5 分钟内有效）`, 4000);
    } catch (e) { toast(e.message); }
  };
  $('#verify').onclick = async () => {
    try {
      gate();
      const r = await api('/auth/verify', {
        method: 'POST',
        body: { email: $('#email').value, code: $('#code').value, age_confirmed: true, terms_accepted: true },
      });
      saveToken(r.access_token);
      await loadMe();
      go(r.profile_completed ? '#/home' : '#/profile');
    } catch (e) {
      if (e.code === 42901) toast(`慢一点，${e.data?.retry_after || 60}s 后可重发`);
      else toast(e.message);
    }
  };
  $('#resendLink').onclick = async (ev) => {
    ev.preventDefault();
    try {
      const r = await api('/auth/code', { method: 'POST', body: { email: $('#email').value } });
      $('#code').value = r.demo_code;
      delete state.devices[$('#email').value];
      toast(`已重发：${r.demo_code}`);
    } catch (e) { toast(e.data?.retry_after ? `请 ${e.data.retry_after}s 后再试` : e.message); }
  };
  // 社交登录：演示里没有 Apple / Google 的 OAuth 凭据，走的是后端 mock 授权。
  // 点之前必须先弹窗说明——否则用户会以为自己正在进行真实的平台授权。
  // 18 岁与条款仍然强校验：第三方账号也不能绕过成年门槛。
  const social = async (provider) => {
    const name = provider === 'apple' ? 'Apple' : 'Google';
    const email = $('#email').value.trim() || `${provider}.user@demo.crossmate.app`;
    const ok = confirm(
      `演示模式：这里没有接入 ${name} 的真实授权（没有 OAuth 凭据）。

` +
      `点击「确定」将用 ${name} 演示账号以邮箱 ${email} 登录；
` +
      `点击「取消」则返回。`,
    );
    if (!ok) return;
    try {
      if (!$('#adult').checked) throw new Error('请先确认已满 18 岁');
      if (!$('#terms').checked) throw new Error('请先同意条款与隐私政策');
      const r = await api('/auth/social', {
        method: 'POST',
        body: { provider, email, age_confirmed: true, terms_accepted: true },
      });
      saveToken(r.access_token);
      await loadMe();
      toast(`${name} 演示账号登录成功（未进行真实授权）`);
      go(r.profile_completed ? '#/home' : '#/profile');
    } catch (e) { toast(e.message); }
  };
  $('#apple').onclick = () => social('apple');
  $('#google').onclick = () => social('google');
  // 条款链接必须 preventDefault：否则 hash 变成 "#"，路由判不出页面会把用户踢回启动页
  $('#termsLink').onclick = (ev) => {
    ev.preventDefault();
    modal(`<h3>Terms & privacy</h3><p class="sub">演示版本：只说重点。</p>
      <p class="sub">· 仅限 18 岁以上。不提供任何形式与未成年人相关的交往功能。<br>
      · 语音房不录音；通话由第三方 RTC 服务承载，我们只保存接通/结束时间。<br>
      · 你填写的位置、语言、兴趣由你自己决定公开范围，不通过 IP 推断。<br>
      · AI 只生成建议，替你发送任何消息都需要你亲手点「发送」。<br>
      · 积分不折现，也永远不影响谁被推荐给你。<br>
      · 举报内容只有安全团队可见，不会告诉被举报人是谁举报的。</p>
      <button class="btn" data-close>OK</button>`);
  };
}

// ---------------- P03 / P04 资料 & 兴趣 ----------------
const INTEREST_GROUPS = {
  Music: ['Indie', 'Jazz', 'Pop', 'K-pop', 'Music'],
  'Things I enjoy': ['Travel', 'Coffee', 'Films', 'Gaming', 'Design', 'Food', 'Photography', 'Football', 'Books'],
  Languages: ['English', '中文', '日本語', 'Español', 'Deutsch', 'Bahasa', 'العربية', 'Português'],
};
const GOALS = [['make_friends', 'Make friends'], ['language_exchange', 'Language exchange'],
  ['good_conversations', 'Good conversations'], ['dating', 'Dating']];

async function renderProfile() {
  state.tab = 'profile';
  const me = state.me || await loadMe();
  view.innerHTML = `${top({ back: true, title: 'Set up your profile', right: '<span class="sub">2/4</span>' })}
    <div class="progress"><b style="width:50%"></b></div>
    <div style="text-align:center;margin:12px 0">${avatar(me.nickname || 'A', 'sm')}</div>
    <div class="input"><span class="sub">Nickname</span><input id="nick" value="${escapeHtml(me.nickname || '')}" placeholder="Alex"></div>
    <div class="input"><span class="sub">Country</span><input id="country" value="${escapeHtml(me.country || '')}" placeholder="United States"></div>
    <div class="input"><span class="sub">Timezone</span><input id="tz" value="${escapeHtml(me.timezone || '')}" placeholder="UTC+8"></div>
    <div class="sectiontitle">What brings you here?</div>
    <div class="chips" id="goals">${GOALS.map(([v, l]) => `<button class="chip ${me.social_goals?.includes(v) ? 'on' : ''}" data-goal="${v}" aria-pressed="${me.social_goals?.includes(v)}">${l}</button>`).join('')}</div>
    <button class="btn" id="next">Continue</button>
    <p class="sub" style="font-size:11px">国家与语言由你自己填写，我们不会把 IP 当作公开资料。</p>`;
  $('#goals').onclick = (e) => {
    const c = e.target.closest('[data-goal]');
    if (!c) return;
    c.classList.toggle('on');
    c.setAttribute('aria-pressed', c.classList.contains('on'));
  };
  $('#next').onclick = async () => {
    const goals = [...view.querySelectorAll('[data-goal].on')].map((b) => b.dataset.goal);
    const languages = me.languages?.length ? me.languages : ['English'];
    try {
      state.me = await api('/users/me/profile', {
        method: 'PATCH',
        body: { nickname: $('#nick').value, country: $('#country').value, timezone: $('#tz').value, social_goals: goals, languages },
      });
      go('#/interests');
    } catch (e) { toast(e.message); }
  };
}

async function renderInterests() {
  const me = state.me || await loadMe();
  const picked = new Set(me.interests || []);
  view.innerHTML = `${top({ back: true, title: 'Choose your interests', right: '<span class="sub">3/4</span>' })}
    <div class="progress"><b style="width:75%"></b></div>
    <p class="sub">Pick at least 3 so we can find better conversations.</p>
    ${Object.entries(INTEREST_GROUPS).slice(0, 2).map(([g, items]) => `<div class="sectiontitle">${g}</div>
      <div class="chips">${items.map((i) => `<button class="chip ${picked.has(i) ? 'on' : ''}" data-i="${i}" aria-pressed="${picked.has(i)}">${i}</button>`).join('')}</div>`).join('')}
    <div class="sectiontitle">Languages you speak</div>
    <div class="chips" id="langs">${INTEREST_GROUPS.Languages.map((l) => `<button class="chip ${me.languages?.includes(l) ? 'on' : ''}" data-l="${l}">${l}</button>`).join('')}</div>
    <button class="btn" id="save">Save interests</button>
    <p class="sub" id="count">已选 ${picked.size} 个兴趣</p>`;
  const onPick = (e) => {
    const b = e.target.closest('[data-i]');
    if (!b) return;
    const i = b.dataset.i;
    picked.has(i) ? picked.delete(i) : picked.add(i);
    b.classList.toggle('on');
    $('#count').textContent = `已选 ${picked.size} 个兴趣`;
  };
  view.querySelectorAll('.chips').forEach((c) => c.addEventListener('click', onPick));
  $('#save').onclick = async () => {
    if (picked.size < 3) return toast('至少选择 3 个兴趣');
    const languages = [...view.querySelectorAll('[data-l].on')].map((b) => b.dataset.l);
    try {
      state.me = await api('/users/me/profile', { method: 'PATCH', body: { interests: [...picked], languages: languages.length ? languages : ['English'] } });
      if (!state.me.profile_completed) return toast('请补全昵称、国家、时区和社交目标');
      go('#/home');
    } catch (e) { toast(e.message); }
  };
}

// ---------------- P05 Home ----------------
async function renderHome() {
  state.tab = 'home';
  setActiveTab('home');
  const me = state.me || await loadMe();
  let unread = 0;
  try { unread = (await api('/notifications?unread_only=true', { quiet: true })).unread; } catch { /* ignore */ }
  view.innerHTML = `${top({ brand: true, right: `<button class="icon-btn" data-go="#/notifications" aria-label="通知" style="position:relative">✉${unread ? `<sup style="position:absolute;top:2px;right:2px;color:var(--orange);font-size:10px">${unread}</sup>` : ''}</button>` })}
    <div class="title">Hi ${escapeHtml(me.nickname || 'there')} 👋</div>
    <p class="sub">Find a little moment. Meet someone new, one honest conversation at a time.</p>
    <div class="card" style="cursor:pointer" data-go="#/discover">
      <div class="row"><div class="profile">${avatar('✦', 'sm')}
        <div><b>Daily spark</b><div class="sub">A small question to start with</div></div></div><span style="color:var(--orange)">›</span></div>
      <p style="color:#d8cddd">What made you smile today?</p>
    </div>
    <div class="sectiontitle">Your progress</div>
    <div class="card">
      <div class="row"><span>Profile strength</span><b>${me.profile_strength}%</b></div>
      <div class="progress"><b style="width:${me.profile_strength}%"></b></div>
      <button class="btn alt" data-go="#/me">Complete profile</button>
    </div>
    <div class="sectiontitle">Safety first</div>
    <div class="notice">You choose what to share. Block or report anytime. <b>AI never sends messages for you.</b></div>
    <div class="row" style="margin-top:10px">
      <button class="btn alt" data-go="#/points" style="margin-top:0">✦ My points</button>
      <button class="btn alt" data-go="#/rooms" style="margin-top:0">◉ Rooms</button>
    </div>`;
  await routeTop();
}

async function routeTop() { /* 占位：保持接口职责清晰 */ }

// ---------------- P06 Discover ----------------
async function renderDiscover(tabState, opts = {}) {
  state.tab = 'discover';
  setActiveTab('discover');
  const tab = opts.tab || state.discover.tab;
  state.discover.tab = tab;
  view.innerHTML = `${top({ title: 'Discover', right: '<button class="icon-btn" data-go="#/settings" aria-label="设置">⌕</button>' })}
    <div class="tabs" role="tablist">
      ${[['for_you', 'For you'], ['language', 'Language'], ['online', 'Online']].map(([k, l]) =>
        `<button role="tab" data-t="${k}" class="${tab === k ? 'active' : ''}">${l}</button>`).join('')}
    </div>
    <div id="deck"><div class="empty"><div class="emoji">⏳</div>Loading…</div></div>`;
  view.querySelectorAll('[data-t]').forEach((b) => (b.onclick = () => renderDiscover(null, { tab: b.dataset.t })));
  await loadDeck();
}

async function loadDeck() {
  try {
    const d = await api(`/discover?tab=${state.discover.tab}`);
    state.discover.items = d.items;
    state.discover.quota = d.priority_like_quota.remaining;
    drawCard();
  } catch (e) {
    $('#deck').innerHTML = `<div class="empty"><div class="emoji">⚠️</div>${escapeHtml(e.message)}
      <button class="btn alt" id="retry">Retry</button></div>`;
    $('#retry').onclick = loadDeck;
  }
}

function drawCard() {
  const { items } = state.discover;
  const deck = $('#deck');
  if (!deck) return;
  if (!items.length) {
    deck.innerHTML = `<div class="empty"><div class="emoji">✨</div>今天的推荐都看完啦<br><span class="sub">明天再来，或者去 Rooms 认识新朋友</span>
      <button class="btn alt" data-go="#/rooms">Go to Rooms</button></div>`;
    return;
  }
  const p = items[0];
  const bg = ['', 'alt', 'green'][items.length % 3];
  deck.innerHTML = `<div class="feed ${bg} fade" id="card">
      <div><span class="badge">${p.online ? '● ONLINE NOW' : '○ OFFLINE'}</span>
        <div class="face" aria-hidden="true">◕‿◕</div></div>
      <div class="faceinfo"><b>${escapeHtml(p.nickname)}</b>
        <div class="sub">${escapeHtml([p.country, (p.languages || []).join(' / ')].filter(Boolean).join(' · '))}</div>
        <div>${(p.interests || []).slice(0, 3).map((i) => `<span class="tag">${escapeHtml(i)}</span>`).join('')}</div>
      </div>
    </div>
    <div class="actions">
      <button class="round" id="pass" aria-label="跳过">×</button>
      <button class="round main" id="plike" aria-label="重点喜欢" ${state.discover.quota <= 0 ? 'disabled' : ''}>✦</button>
      <button class="round" id="like" aria-label="喜欢">♥</button>
    </div>
    <p class="hint">${escapeHtml(p.match_hint || '')}<span style="opacity:.7"> · Priority likes left today: ${state.discover.quota}</span></p>
    <p class="hint" style="font-size:11px;opacity:.7">Ranking uses interests, languages, timezone, goals and activity. Never points or sensitive traits.</p>
    <p class="hint" style="font-size:11px;margin-top:6px"><a href="#" id="more" style="color:var(--orange)">Load more from this list</a></p>`;

  $('#more').onclick = async (ev) => {
    ev.preventDefault();
    state.discover.offset = (state.discover.offset || 0) + state.discover.items.length;
    try {
      const d = await api(`/discover?tab=${state.discover.tab}&cursor=${state.discover.offset}`);
      state.discover.items = d.items;
      drawCard();
    } catch (e) { toast(e.message); }
  };
  const next = (fn) => async (ev) => {
    ev.target.disabled = true;
    try { await fn(); } catch (e) { toast(e.message); ev.target.disabled = false; return; }
    state.discover.items.shift();
    if (!state.discover.items.length) await loadDeck(); else drawCard();
  };
  $('#pass').onclick = next(async () => { await api('/passes', { method: 'POST', body: { candidate_id: p.user_id } }); });
  $('#like').onclick = next(async () => {
    const r = await api('/likes', { method: 'POST', body: { candidate_id: p.user_id, type: 'like' } });
    if (r.match) { state.match = r.match; setTimeout(() => go(`#/match/${r.match.match_id}`), 260); }
  });
  $('#plike').onclick = next(async () => {
    const r = await api('/likes', { method: 'POST', body: { candidate_id: p.user_id, type: 'priority_like' } });
    state.discover.quota = r.priority_like_quota_left;
    toast(`✦ Priority like sent · 剩余 ${r.priority_like_quota_left}`);
    if (r.match) { state.match = r.match; setTimeout(() => go(`#/match/${r.match.match_id}`), 260); }
  });
}

// ---------------- P07 Match ----------------
async function renderMatch(mid) {
  state.tab = 'match';
  const m = state.match?.match_id === mid ? state.match : (await api('/matches', { quiet: true })).find((x) => x.match_id === mid);
  const me = state.me?.nickname || 'A';
  const peer = m?.peer?.nickname || 'S';
  view.innerHTML = `<div class="hero fade" style="padding-top:30px">
    <div style="display:flex;justify-content:center;gap:10px">${avatar(me)}${avatar(peer)}</div>
    <div class="title" style="margin-top:22px">It's a match!</div>
    <p class="sub">You both chose to connect.</p>
    <div class="card"><b>Start with a shared interest</b><p class="sub">Indie music · language exchange</p></div>
    <button class="btn" id="hello">Say hello</button>
    <button class="btn alt" data-go="#/discover">Keep discovering</button>
  </div>`;
  $('#hello').onclick = () => go(`#/chat/${mid}`);
}

// ---------------- Chats 列表 ----------------
// Tab 栏的 Chats 必须有自己的路由：否则 go('#/chats') 落不到任何规则，会被重定向回 #/home。
// 本机静音列表：演示里没有推送通道，静音只影响「本机界面」（列表打标、聊天气泡不提示），
// 明确不碰服务端——所以别人给你发消息不受影响，也不会因为静音丢掉未读。
function mutedSet() {
  try { return new Set(JSON.parse(localStorage.getItem('cm_muted') || '[]')); } catch (e) { return new Set(); }
}
function isMuted(mid) { return mutedSet().has(mid); }
function setMuted(mid, on) {
  const s = mutedSet();
  if (on) s.add(mid); else s.delete(mid);
  localStorage.setItem('cm_muted', JSON.stringify([...s]));
}

async function renderChats() {
  state.tab = 'chats';
  setActiveTab('chats');
  view.innerHTML = `${top({ title: 'Chats' })}
    <div class="tabs" role="tablist" id="chatTabs">
      <button role="tab" id="tabMatches" data-t="matches" class="active" aria-selected="true">Matches <span class="pill" id="cntM"></span></button>
      <button role="tab" id="tabAll" data-t="all" aria-selected="false">All chats <span class="pill" id="cntA"></span></button>
    </div>
    <div id="chatList"><div class="empty"><div class="emoji">⏳</div>Loading…</div></div>`;
  let onlyMatches = true;
  const draw = async () => {
    const box = $('#chatList');
    if (!box) return;
    let list = [];
    try { list = (await api('/matches', { quiet: true })) || []; } catch (e) { /* 静默降级为空列表 */ }
    // 「Matches」只留还没聊过的；「All」是全部（含已聊过的）。
    // 之前两个 Tab 用的是同一份数据，点了确实看不出任何区别。
    const total = list.length;
    const fresh = list.filter((m) => m.can_chat !== false && !m.last_message).length;
    const cm = $('#cntM'); const ca = $('#cntA');
    if (cm) cm.textContent = fresh;
    if (ca) ca.textContent = total;
    if (onlyMatches) list = list.filter((m) => m.can_chat !== false && !m.last_message);
    list.sort((a, b) => (b.last_at || b.created_at || 0) - (a.last_at || a.created_at || 0));
    box.innerHTML = list.length ? list.map((m) => `
      <div class="match-row" data-mid="${escapeHtml(m.match_id)}" style="cursor:pointer">
        <div class="row">${avatar(m.peer?.nickname || '?', 'sm')}
          <div style="flex:1;min-width:0"><div class="row" style="gap:6px"><b>${escapeHtml(m.peer?.nickname || '')}</b>
            ${isMuted(m.match_id) ? '<span class="pill" style="font-size:10px">🔕 muted</span>' : ''}
            ${m.peer?.online ? '<span class="pill green" style="font-size:10px">online</span>' : ''}</div>
          <div class="sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(m.last_message || 'Matched — say hello 👋')}</div></div>
          <span style="color:var(--muted)">›</span></div>
      </div>`).join('') : `<div class="empty"><div class="emoji">💬</div>${onlyMatches ? 'No new matches yet.' : 'No conversations yet.'}
        <div style="margin-top:8px"><button class="btn alt" data-go="#/discover">Find someone</button></div></div>`;
    box.querySelectorAll('[data-mid]').forEach((row) => { row.onclick = () => go(`#/chat/${row.dataset.mid}`); });
  };
  view.querySelectorAll('#chatTabs [data-t]').forEach((b) => (b.onclick = () => {
    onlyMatches = b.dataset.t === 'matches';
    view.querySelectorAll('#chatTabs [data-t]').forEach((x) => {
      x.classList.toggle('active', x === b);
      x.setAttribute('aria-selected', String(x === b));
    });
    draw();
  }));
  await draw();
}

// ---------------- P08 Chat ----------------
async function renderChat(mid) {
  state.tab = 'chats';
  const [msgs, matches] = await Promise.all([api(`/matches/${mid}/messages`), api('/matches')]);
  const m = matches.find((x) => x.match_id === mid);
  const meId = state.me?.id;
  const draft = state.draft.mid === mid ? state.draft.text : '';
  view.innerHTML = `${top({ back: true, title: '', right: `<button class="icon-btn" id="menu" aria-label="更多">⋯</button>` })}
    <div style="display:flex;gap:10px;align-items:center;margin:-6px 0 10px">${avatar(m?.peer?.nickname)}
      <div><b>${escapeHtml(m?.peer?.nickname || '')}</b>
      <div class="sub">${m?.peer?.online ? '● online' : '○ offline'} · ${escapeHtml((m?.peer?.languages || []).join(' / '))}</div></div></div>
    <div class="thread" id="thread"></div>
    ${m?.can_chat === false ? `<div class="notice warn">This conversation is closed${m?.status === 'blocked' ? ' (blocked)' : ''}.</div>` : `
    <div class="composer">
        ${aiEnabled ? '<button id="aiBtn" aria-label="AI tools" style="background:#ffffff17;color:var(--orange)" title="AI tools">✦</button>' : ''}
      <input id="msg" placeholder="Type a message…" maxlength="2000" aria-label="message" autocomplete="off">
      <button id="send" aria-label="发送">➤</button>
    </div>
    <p class="hint" style="font-size:11px">AI 只会给出建议，发送按钮永远由你按下。</p>`}`;

  let peerReasons = [];
  try { peerReasons = (await api('/ai/generations', { method: 'POST', body: { kind: 'icebreaker', match_id: mid }, quiet: true }))?.output?.suggestions?.map((x) => x.reason) || []; } catch { /* AI 不可用时静默 */ }

  const drawThread = () => {
    const t = $('#thread');
    if (!t) return;
    t.innerHTML = msgs.length ? msgs.map((x) => `<div class="bubble ${x.sender_id === meId ? 'me' : 'them'}">${escapeHtml(x.text)}
      <div class="meta">${new Date(x.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${x.source_generation_id ? ' · edited from AI suggestion' : ''}</div></div>`).join('')
      : `<div class="empty"><div class="emoji">👋</div>Say hello. No pressure, just be yourself.
        ${aiEnabled ? '<div style="margin-top:8px"><button class="btn alt" id="suggestFirst">✦ Need an opener?</button></div>' : ''}</div>`;
    if (!msgs.length && peerReasons.length && t.lastElementChild) {
      t.lastElementChild.insertAdjacentHTML('beforeend',
        `<div class="notice" style="text-align:left;margin-top:12px">为什么推荐你们聊天：${peerReasons.map((r) => escapeHtml(r)).join(' · ')}</div>`);
    }
    if ($('#suggestFirst')) $('#suggestFirst').onclick = () => go(`#/aitools/${mid}`);
  };

  const poll = async () => {
    if (state.chatTimer) clearInterval(state.chatTimer);
    if (m?.can_chat === false) return;
    state.chatTimer = setInterval(async () => {
      if (window.location.hash !== `#/chat/${mid}`) { clearInterval(state.chatTimer); state.chatTimer = null; return; }
      try {
        const all = await api(`/matches/${mid}/messages`, { quiet: true });
        if (all.length !== msgs.length) { msgs.length = 0; msgs.push(...all); drawThread(); }
      } catch { /* ignore */ }
    }, 3000);
  };

  const sendMsg = async () => {
    const input = $('#msg');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    const fromAi = state.aiSubmitted && state.draft.mid === mid;
    try {
      const r = await api(`/matches/${mid}/messages`, {
        method: 'POST',
        body: { client_message_id: crypto.randomUUID(), text, source_generation_id: fromAi ? state.aiGen?.generation_id : undefined },
      });
      if (!r.deduplicated) msgs.push(r.message);
      state.draft = { mid: null, text: '' };
      state.aiSubmitted = false;
      drawThread();
    } catch (e) { input.value = text; toast(e.message); }
  };

  drawThread();
  $('#menu').onclick = chatMenu(mid, m);
  if (m?.can_chat !== false) {
    const input = $('#msg');
    if (draft) {
      input.value = draft;
      toast('AI 建议已回填 —— 检查后点击发送');
    }
    input.addEventListener('input', () => { state.draft = { mid, text: input.value }; });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMsg(); });
    $('#send').onclick = sendMsg;
    if ($('#aiBtn')) $('#aiBtn').onclick = () => go(`#/aitools/${mid}`);
    api(`/matches/${mid}/read`, { method: 'POST', quiet: true }).catch(() => {});
  }
  poll();
}

function chatMenu(mid, m) {
  return async () => {
    const el = modal(`<h3 style="margin:0 0 10px">Conversation</h3>
      <button class="btn alt" id="ai">✦ AI tools</button>
      <button class="btn alt" id="voice">☎ Voice call</button>
      <button class="btn alt" id="mute">${isMuted(mid) ? '🔔 Unmute' : '🔕 Mute (local)'}</button>
      <button class="btn alt" id="rep">⚑ Report</button>
      <button class="btn alt danger" id="block">🚫 Block</button>
      <button class="btn" data-close>Close</button>`);
    el.querySelector('#ai').onclick = () => { closeModal(); go(`#/aitools/${mid}`); };
    el.querySelector('#rep').onclick = () => { closeModal(); go(`#/report/user:${m.peer.user_id}`); };
    el.querySelector('#voice').onclick = async () => {
      try {
        const c = await api('/calls', { method: 'POST', body: { match_id: mid } });
        closeModal(); state.call = c; go(`#/call/${c.call_id}`);
      } catch (e) { toast(e.message); }
    };
    el.querySelector('#mute').onclick = () => {
      const now = !isMuted(mid);
      setMuted(mid, now);
      closeModal();
      toast(now ? '已在本机静音：列表会打标，新消息仍会显示在会话里。' : '已取消静音');
      if (window.location.hash.startsWith('#/chats')) renderChats();
    };
    el.querySelector('#block').onclick = async () => {
      if (!confirm(`Block ${m?.peer?.nickname}? This ends the chat and call access.`)) return;
      try {
        await api('/blocks', { method: 'POST', body: { user_id: m.peer.user_id } });
        closeModal(); toast('Blocked. They can no longer message you.'); go('#/chats');
      } catch (e) { toast(e.message); }
    };
  };
}

// ---------------- P09 AI 工具 ----------------
async function renderAiTools(mid) {
  state.tab = 'chats';
  view.innerHTML = `${top({ back: true, title: 'AI chat tools' })}
    <div class="notice">AI 只给建议（破冰 / 翻译 / 改写），<b>发送永远由你确认</b>。每日额度：破冰 10 次，翻译/改写各 200 次。</div>
    <div class="card fade"><div class="row"><b>Translate</b><span class="pill">→ 中文</span></div>
      <p class="sub">保留原意、语气、人名与数字。</p>
      <div class="input"><textarea id="trText" rows="2" placeholder="Paste a message to translate…" style="width:100%"></textarea></div>
      <button class="btn" id="tr">Translate message</button><div id="trOut"></div></div>
    <div class="card fade"><div class="row"><b>Rewrite tone</b><span class="pill" id="styleP">natural</span></div>
      <div class="chips" id="styles">${['natural', 'polite', 'casual'].map((s, i) => `<button class="chip ${i === 0 ? 'on' : ''}" data-s="${s}">${s}</button>`).join('')}</div>
      <div class="input"><textarea id="rwText" rows="2" placeholder="Draft you want to rewrite…" style="width:100%"></textarea></div>
      <button class="btn" id="rw">Suggest a rewrite</button><div id="rwOut"></div></div>
    <div class="card fade"><div class="row"><b>Conversation starter</b><span class="pill green" id="iceStatus">AI ready</span></div>
      <p class="sub">只使用双方都选择公开的信息（共同兴趣 / 语言 / 社交目标）。</p>
      <button class="btn alt" id="ice">Get 3 ideas</button><div id="iceOut"></div></div>`;

  let style = 'natural';
  view.querySelectorAll('[data-s]').forEach((b) => (b.onclick = () => {
    style = b.dataset.s;
    view.querySelectorAll('[data-s]').forEach((x) => x.classList.toggle('on', x === b));
    $('#styleP').textContent = style;
  }));

  const show = (el, res, withFeedback) => {
    const o = res.output || {};
    const statusNote = res.status === 'safety_blocked'
      ? '<div class="notice warn">⚠️ 内容被安全策略拦截（疑似 prompt injection / 风险内容），未调用模型。</div>'
      : res.status !== 'succeeded' ? `<div class="notice warn">已降级为本地规则建议：${escapeHtml(res.fallback_reason || 'fallback')}</div>` : '';
    const sug = (o.suggestions || []).map((s, i) => `<div class="suggest" data-sug="${i}" tabindex="0" role="button">${escapeHtml(s.text)}
      <div class="sub" style="font-size:11px">${escapeHtml(s.reason || '')}</div></div>`).join('');
    el.innerHTML = statusNote + `<div class="ai"><strong>✦ ${res.latency_ms != null ? res.latency_ms + 'ms · ' : ''}${escapeHtml(res.prompt_code || 'AI')}</strong>
      ${sug}
      ${o.target ? `<p style="margin:6px 0">${escapeHtml(o.target)}</p>` : ''}
      ${(o.warnings || []).length ? `<p class="sub">${o.warnings.map((w) => '⚠️ ' + escapeHtml(w)).join('<br>')}</p>` : ''}
      <button class="btn sm" data-apply>Use suggestion</button>
      ${withFeedback ? '<button class="btn sm alt" data-fb="5">👍</button><button class="btn sm alt" data-fb="1">👎</button>' : ''}
      <p class="sub" style="font-size:10px">Review before sending · AI never sends for you</p></div>`;
    const apply = async (idx) => {
      const text = o.suggestions ? o.suggestions[idx]?.text : o.target;
      try { await api('/ai/adopt', { method: 'POST', body: { generation_id: res.generation_id, index: idx } }); } catch { /* ignore */ }
      state.aiGen = { ...res, match_id: mid };
      state.draft = { mid, text: text || '' };
      state.aiSubmitted = true;
      toast('已回填输入框 —— 请检查后再点击发送');
      go(`#/chat/${mid}`);
    };
    el.querySelectorAll('[data-sug]').forEach((b) => {
      b.onclick = () => apply(Number(b.dataset.sug));
      b.onkeydown = (e) => { if (e.key === 'Enter') apply(Number(b.dataset.sug)); };
    });
    el.querySelector('[data-apply]').onclick = () => apply(0);
    el.querySelectorAll('[data-fb]').forEach((b) => (b.onclick = async () => {
      try { await api('/ai/feedback', { method: 'POST', body: { generation_id: res.generation_id, rating: Number(b.dataset.fb) } }); toast('Thanks — feedback does not earn points.'); }
      catch (e) { toast(e.message); }
    }));
  };

  const run = (el, fn) => async () => {
    try { show(el, await fn(), true); } catch (e) { el.innerHTML = `<div class="notice warn">${escapeHtml(e.message)}</div>`; }
  };
  $('#tr').onclick = run($('#trOut'), () => api('/ai/transformations', { method: 'POST', body: { kind: 'translate', text: $('#trText').value, target_language: 'zh' } }));
  $('#rw').onclick = run($('#rwOut'), () => api('/ai/transformations', { method: 'POST', body: { kind: 'rewrite', text: $('#rwText').value, style, target_language: 'en' } }));
  $('#ice').onclick = async () => {
    try {
      const r = await api('/ai/generations', { method: 'POST', body: { kind: 'icebreaker', match_id: mid } });
      $('#iceStatus').textContent = r.status === 'succeeded' ? `${r.latency_ms}ms · AI` : r.status;
      show($('#iceOut'), r, true);
    } catch (e) { toast(e.message); }
  };
}

// ---------------- P10 / P11 Rooms ----------------
async function renderRooms(tabArg, opts = {}) {
  state.tab = 'rooms';
  setActiveTab('rooms');
  const tab = opts.tab || 'popular';
  let data;
  try { data = await api(`/rooms?tab=${tab}`); } catch (e) {
    view.innerHTML = top({ title: 'Rooms' }) + `<div class="empty">${escapeHtml(e.message)}</div>`;
    return;
  }
  view.innerHTML = `${top({ title: 'Rooms', right: `<button class="icon-btn" id="newRoom" aria-label="创建房间">＋</button>` })}
    <div class="tabs">${[['popular', 'Popular'], ['language', 'Language'], ['topics', 'Topics']].map(([k, l]) =>
      `<button data-t="${k}" class="${tab === k ? 'active' : ''}">${l}</button>`).join('')}</div>
    ${opts.reason ? `<div class="notice warn">You were removed from the room（原因类别：${escapeHtml(opts.reason)}）</div>` : ''}
    ${data.empty ? `<div class="empty"><div class="emoji">◌</div>${escapeHtml(data.empty_text)}<br><br><button class="btn alt" id="newRoom2">Start a room</button></div>`
      : data.items.map((r, i) => `<div class="room fade" style="${i % 2 ? 'background:linear-gradient(135deg,#624b8e,#db6d87)' : ''}">
        <div class="glow g1"></div><div class="glow g2"></div>
        <span class="badge">${r.status === 'live' ? '● LIVE · ON AIR' : '◷ SCHEDULED'}</span>
        <h3>${escapeHtml(r.title)}</h3><p class="sub" style="margin:2px 0">${escapeHtml(r.topic)}</p>
        <div class="roomfoot"><span class="sub">${r.member_count} in room · ${r.speakers.length} speaker(s)</span>
          <button class="btn sm" data-join="${r.id}">Join room</button></div></div>`).join('')}`;
  view.querySelectorAll('[data-t]').forEach((b) => (b.onclick = () => renderRooms(null, { tab: b.dataset.t })));
  view.querySelectorAll('[data-join]').forEach((b) => (b.onclick = async () => {
    try {
      const r = await api(`/rooms/${b.dataset.join}/join`, { method: 'POST' });
      toast(r.role === 'listener' ? 'Joined as listener. Raise your hand to speak.' : 'Joined as host/speaker.');
      go(`#/room/${b.dataset.join}?joined=1`);
    } catch (e) { toast(e.message); }
  }));
  const create = () => {
    const m = modal(`<h3>Create a room</h3>
      <div class="input"><input id="rt" placeholder="Room title" maxlength="60"></div>
      <div class="input"><textarea id="rp" rows="3" placeholder="Public topic — everyone will see this" maxlength="200"></textarea></div>
      <p class="sub" style="font-size:11px">房间语音不会被录制。作为主持人请遵守社区规范。</p>
      <button class="btn" id="rc">Create</button><button class="btn alt" data-close>Cancel</button>`);
    m.querySelector('#rc').onclick = async () => {
      try {
        const r = await api('/rooms', { method: 'POST', body: { title: m.querySelector('#rt').value || 'New room', topic: m.querySelector('#rp').value || 'Say hi' } });
        closeModal(); go(`#/room/${r.id}?joined=1`);
      } catch (e) { toast(e.message); }
    };
  };
  $('#newRoom').onclick = create;
  if ($('#newRoom2')) $('#newRoom2').onclick = create;
}

async function renderRoom(rid, opts = {}) {
  state.tab = 'rooms';
  if (opts.reason) return renderRooms(null, { reason: opts.reason });
  const r = await api(`/rooms/${rid}`);
  const iAmHost = r.is_host;
  // 加入成功后刷新路由状态，避免 ?joined=1 残留导致回到列表
  if (opts.joined) state._opts = {};
  const live = r.status === 'live';
  if (r.status === 'ended') { toast('This room has ended.'); state._opts = null; return go('#/rooms'); }
  let msgs = [];
  if (r.me && live) msgs = await api(`/rooms/${rid}/messages`).catch(() => []);
  const me = r.me;
  view.innerHTML = `${top({ back: true, title: r.title, right: `<button class="icon-btn" id="rMenu" aria-label="更多">⋯</button>` })}
    <div class="room" style="height:250px"><div class="glow g1"></div><div class="glow g2"></div>
      <div class="row"><span class="badge">${live ? '● LIVE' : r.status.toUpperCase()}</span><span class="sub">${r.member_count} in room</span></div>
      <div class="face" style="font-size:60px;margin-top:26px;text-align:center">◉‿◉</div>
      <div class="roomfoot"><span>Host: ${escapeHtml(r.host_name)}</span>
        ${me && me.role === 'listener' ? '<button class="btn sm" id="hand">Raise hand</button>' : ''}
        ${me && me.role === 'speaker_requested' ? '<button class="btn sm alt" id="lower">Hand raised · cancel</button>' : ''}
        ${me && me.role === 'speaker' && !iAmHost ? '<span class="pill green">mic on</span>' : ''}
        ${!me ? '<button class="btn sm" id="join">Join</button>' : ''}
      </div></div>
    <div class="sectiontitle">Topic</div><div class="notice">${escapeHtml(r.topic)}</div>
    ${iAmHost ? '<div class="sectiontitle">Hand raises</div><div id="pending"><p class="sub">Loading members…</p></div>' : ''}
    <div class="sectiontitle">Room chat</div>
    <div class="thread" id="rthread">${msgs.length ? msgs.map((x) => `<div class="bubble them">${escapeHtml(x.text)}<div class="meta">${new Date(x.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div></div>`).join('') : '<p class="sub">No messages yet.</p>'}</div>
    ${me && me.role !== 'listener' && live ? `<div class="composer"><input id="rmsg" placeholder="Say something…" maxlength="500" aria-label="room message"><button id="rsend" aria-label="发送">➤</button></div>`
      : me && live ? '<div class="notice">You are a listener. Raise your hand to request the mic. Listeners can read the chat but not post.</div>'
      : live ? '<div class="notice">Join the room to listen and chat.</div>'
      : '<div class="notice warn">This room is no longer live.</div>'}
    ${r.is_host ? '<div class="sectiontitle">Host controls</div><div id="hostList"><p class="sub">Loading members…</p></div>' : ''}`;

  const rerender = () => renderRoom(rid);
  if ($('#join')) $('#join').onclick = async () => {
    try { await api(`/rooms/${rid}/join`, { method: 'POST' }); go(`#/room/${rid}?joined=1`, { joined: true }); } catch (e) { toast(e.message); }
  };
  if ($('#hand')) $('#hand').onclick = async () => {
    try { await api(`/rooms/${rid}/raise-hand`, { method: 'POST' }); toast('Hand raised. Waiting for the host.'); rerender(); } catch (e) { toast(e.message); }
  };
  if ($('#lower')) $('#lower').onclick = async () => { await api(`/rooms/${rid}/raise-hand`, { method: 'DELETE' }); rerender(); };
  const sendRoom = async () => {
    const t = ($('#rmsg')?.value || '').trim();
    if (!t) return;
    try { await api(`/rooms/${rid}/messages`, { method: 'POST', body: { text: t } }); rerender(); } catch (e) { toast(e.message); }
  };
  if ($('#rsend')) { $('#rsend').onclick = sendRoom; $('#rmsg').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendRoom(); }); }

  $('#rMenu').onclick = () => {
    const m = modal(`<h3>Room</h3>
      ${me ? '<button class="btn alt" id="leave">Leave room</button>' : ''}
      ${r.is_host ? '<button class="btn alt danger" id="end">End room for everyone</button>' : ''}
      <button class="btn alt" id="rep">⚠ Report someone</button>
      <button class="btn" data-close>Close</button>`);
    if (m.querySelector('#leave')) m.querySelector('#leave').onclick = async () => { await api(`/rooms/${rid}/leave`, { method: 'POST' }); closeModal(); go('#/rooms'); };
    if (m.querySelector('#end')) m.querySelector('#end').onclick = async () => {
      if (!confirm('End the room for everyone?')) return;
      await api(`/rooms/${rid}/end`, { method: 'POST' }); closeModal(); toast('Room ended. Everyone was notified.'); go('#/rooms');
    };
    m.querySelector('#rep').onclick = () => { closeModal(); go(`/report/room:${rid}`); };
  };

  if (iAmHost) { drawPending(rid); drawHostList(rid, r); }
}

function fmtUntil(ts) {
  const s = Math.max(0, Math.round(ts - Date.now() / 1000));
  return s > 60 ? `${Math.ceil(s / 60)}m` : `${s}s`;
}

async function drawPending(rid) {
  const box = $('#pending');
  if (!box) return;
  const [r, apiDoc] = await Promise.all([api(`/rooms/${rid}`, { quiet: true })]);
  // 举手者 = role 为 speaker_requested 的成员；服务端 /rooms/{id} 通过 members 暴露
  const waiting = (r.members || []).filter((m) => m.role === 'speaker_requested');
  box.innerHTML = waiting.length ? waiting.map((m) => `<div class="list">${avatar('?', 'sm')}
      <div><b>${escapeHtml(m.nickname || 'Listener ' + (m.user_id || '').slice(-4))}</b><div class="sub">raised hand</div></div>
      <span style="display:flex;gap:6px"><button class="btn sm" data-approve="${m.user_id}">Approve</button><button class="btn sm alt" data-mute="${m.user_id}">Deny</button></span>
    </div>`).join('') : '<p class="sub">No one is waiting. Listeners raise their hand to request a mic.</p>';
  box.querySelectorAll('[data-approve]').forEach((b) => (b.onclick = async () => {
    try { await api(`/rooms/${rid}/moderation/approve`, { method: 'POST', body: { user_id: b.dataset.approve } }); toast('Approved — they are now a speaker.'); renderRoom(rid); }
    catch (e) { toast(e.message); }
  }));
  box.querySelectorAll('[data-mute]').forEach((b) => (b.onclick = async () => {
    try { await api(`/rooms/${rid}/moderation/deny`, { method: 'POST', body: { user_id: b.dataset.mute } }); toast('Hand request declined.'); renderRoom(rid); }
    catch (e) { toast(e.message); }
  }));
  void apiDoc;
}

async function drawHostList(rid, r) {
  const box = $('#hostList');
  if (!box) return;
  const members = r.members || [];
  const speakers = members.filter((m) => m.role === 'speaker' || m.user_id === r.host_id);
  const listeners = members.filter((m) => m.role === 'listener' || m.role === 'speaker_requested');
  const now = Date.now() / 1000;
  const row = (m) => {
    const self = m.user_id === r.host_id;
    const muted = m.muted_until > now;
    return `<div class="list">${avatar(self ? 'H' : 'S', 'sm')}
      <div><b>${self ? 'You (host)' : escapeHtml(m.nickname || 'Speaker ' + m.user_id.slice(-4))}</b>
      <div class="sub">${self ? 'host' : muted ? `muted ${fmtUntil(m.muted_until)}` : 'speaker'}</div></div>
      ${self ? '' : `<span style="display:flex;gap:6px"><button class="btn sm alt" data-mute="${m.user_id}">${muted ? 'Muted' : 'Mute 10m'}</button><button class="btn sm alt danger" data-remove="${m.user_id}">Remove</button></span>`}
    </div>`;
  };
  box.innerHTML = (speakers.length ? speakers.map(row).join('') : '<p class="sub">No speakers yet.</p>') +
    `<p class="sub" style="margin-top:8px">Listeners: ${listeners.length}</p>`;
  box.querySelectorAll('[data-mute]').forEach((b) => (b.onclick = async () => {
    try { await api(`/rooms/${rid}/moderation/mute`, { method: 'POST', body: { user_id: b.dataset.mute, minutes: 10 } }); toast('Muted for 10 minutes. They can still listen.'); renderRoom(rid); }
    catch (e) { toast(e.message); }
  }));
  box.querySelectorAll('[data-remove]').forEach((b) => (b.onclick = async () => {
    if (!confirm('Remove this member from the room?')) return;
    try {
      await api(`/rooms/${rid}/moderation/remove`, { method: 'POST', body: { user_id: b.dataset.remove, reason: 'host_action' } });
      toast('Removed. They will see the reason category in Rooms.');
      renderRoom(rid);
    } catch (e) { toast(e.message); }
  }));
}

// ---------------- P12 一对一语音 ----------------
let callFeedbackDone = false;

async function renderCall(cid) {
  state.tab = 'call';
  const call = await api(`/calls/${cid}`);
  const iAmCallee = call.callee_id === state.me.id;
  const matches = await api('/matches', { quiet: true }).catch(() => []);
  const peerName = matches.find((x) => x.match_id === call.match_id)?.peer?.nickname || 'Your match';
  let muted = false;

  const draw = (c) => {
    const connected = !!c.connected_at;
    const statusText = {
      requested: 'Calling…', ringing: iAmCallee ? 'Incoming call' : 'Calling…', connecting: 'Connecting…',
      connected: 'Connected', reconnecting: 'Connection unstable. Reconnecting…', ended: 'Call ended', failed: 'Call failed',
    }[c.status] || c.status;
    const dur = connected ? Math.max(0, Math.floor((c.ended_at || Date.now() / 1000) - c.connected_at)) : 0;
    const submitted = (c.feedback_submitted || []).includes(state.me.id) || callFeedbackDone;
    view.innerHTML = `${top({ back: true, title: 'Voice call' })}
      <div class="call">
        <div class="sub">CrossMate voice call · not recorded</div>
        <div class="bigavatar">${initial(peerName)}</div>
        <div class="title">${escapeHtml(peerName)}</div>
        <div class="status-pill" id="callStatus">${statusText}</div>
        <div class="sub" id="callDur" style="margin-top:8px">${connected ? fmtDur(dur) : 'Timer starts once both sides are media-ready'}</div>
        <div class="callactions">
          <button id="muteBtn" aria-label="mute" aria-pressed="${muted}" title="静音（演示）">${muted ? '🔇' : '🎙'}</button>
          <button id="spkBtn" aria-label="speaker" aria-pressed="false" title="扬声器（演示）">🔈</button>
          ${c.status === 'ended' ? '<button id="backBtn" aria-label="back">✓</button>' : '<button class="end" id="endBtn" aria-label="hang up">☎</button>'}
        </div>
      </div>
      ${c.status === 'ended' ? `<div class="card"><b>Call ended</b><p class="sub">${escapeHtml(c.end_reason || '')} · ${fmtDur(dur)}</p>
          ${submitted ? '<p class="sub">Thanks for the feedback.</p>' : '<button class="btn" id="fb">Leave quick feedback (+3 pts, max 2/week)</button>'}</div>`
        : `<div class="card"><div class="row"><b>Conversation card</b><span class="pill">AI ready</span></div>
          <p class="sub">Shared topics · follow-up ideas · 每日 5 次</p><button class="btn alt" id="prep">✦ Call prep</button></div>
          <p class="hint" style="font-size:11px">演示版本没有真实音轨：按钮只切换本机图标状态，不调用麦克风/扬声器，也不会上传任何声音。计时与接通条件由后端按双方 media_ready 判定。</p>`}`;
    if ($('#endBtn')) $('#endBtn').onclick = async () => { try { await api(`/calls/${cid}/end`, { method: 'POST' }); } catch (e) { toast(e.message); } poll(); };
    if ($('#backBtn')) $('#backBtn').onclick = () => go('#/chats');
    const mb = $('#muteBtn');
    // 演示里没有真实音轨：只切换图标，并明确告诉用户「只是演示」，
    // 免得以为真的把对方静音了。生产环境这里要调 RTC SDK 的 setMuted()。
    if (mb) mb.onclick = () => {
      muted = !muted;
      mb.textContent = muted ? '🔇' : '🎙';
      mb.setAttribute('aria-pressed', String(muted));
      toast(muted ? '已静音（演示：仅界面状态）' : '已取消静音（演示：仅界面状态）');
    };
    const sb = $('#spkBtn');
    if (sb) sb.onclick = () => {
      const on = sb.textContent === '🔈';
      sb.textContent = on ? '🔊' : '🔈';
      sb.setAttribute('aria-pressed', String(on));
      toast(on ? '已切到扬声器（演示：仅界面状态）' : '已切到听筒（演示：仅界面状态）');
    };
    if ($('#fb')) $('#fb').onclick = async () => {
      try {
        const r = await api(`/calls/${cid}/feedback`, { method: 'POST', body: { rating: 4, comment: 'Good talk' } });
        callFeedbackDone = true;
        toast(r.granted === false ? 'Feedback saved (no reward this time).' : `You earned ${r.reward?.points || 0} points: quality feedback.`);
        poll();
      } catch (e) { toast(e.message); }
    };
    if ($('#prep')) $('#prep').onclick = async () => {
      try {
        const r = await api('/ai/generations', { method: 'POST', body: { kind: 'call_prep', call_id: cid } });
        modal(`<h3>✦ Call prep</h3>${(r.output.suggestions || []).map((s) => `<div class="suggest">${escapeHtml(s.text)}</div>`).join('')}
          <p class="sub">${escapeHtml(r.output.notes || '')}</p><button class="btn" data-close>OK</button>`);
      } catch (e) { toast(e.message); }
    };
  };

  const stopPoll = () => { if (state.callTimer) { clearInterval(state.callTimer); state.callTimer = null; } };
  const poll = async () => {
    if (window.location.hash !== `#/call/${cid}`) { stopPoll(); return; }
    try {
      const c = await api(`/calls/${cid}`, { quiet: true });
      if (c.status === 'ended') stopPoll();
      draw(c);
    } catch { /* ignore */ }
  };
  const startPoll = () => {
    stopPoll();
    poll();
    state.callTimer = setInterval(poll, 1500);
  };

  if (iAmCallee && call.status === 'ringing') {
    draw(call);
    const m = modal(`<h3>Incoming call</h3><p class="sub">${escapeHtml(peerName)} is calling you.</p>
      <button class="btn green" id="acc">Accept</button><button class="btn alt" id="rej">Decline</button>`);
    m.querySelector('#acc').onclick = async () => {
      try {
        await api(`/calls/${cid}/accept`, { method: 'POST' });
        closeModal();
        await simulateRtc(cid, call);
        startPoll();
      } catch (e) { toast(e.message); }
    };
    m.querySelector('#rej').onclick = async () => { await api(`/calls/${cid}/reject`, { method: 'POST' }); closeModal(); toast('Call declined.'); go('#/chats'); };
    return;
  }
  draw(call);
  if (call.status === 'ringing' || call.status === 'connecting' || call.status === 'connected' || call.status === 'reconnecting') {
    await simulateRtc(cid, call);
    startPoll();
  }
}

// 演示辅助：模拟 RTC 回调（先对端、再自己），connected_at 只有双方都 ready 才写入
async function simulateRtc(cid, call) {
  const peerId = call.caller_id === state.me.id ? call.callee_id : call.caller_id;
  for (const uid of [peerId, state.me.id]) {
    try {
      await api('/rtc/events', { method: 'POST', quiet: true, body: { call_id: cid, provider_event_id: crypto.randomUUID(), event_type: 'media_ready', user_id: uid, media_ready: true } });
    } catch { /* ignore */ }
  }
}

const fmtDur = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

// ---------------- P13 通知 ----------------
async function renderNotifications() {
  state.tab = 'home';
  const d = await api('/notifications');
  const icon = { match: '♥', reward: '✦', room: '◉', message: '✉', safety: '🛡' };
  const unreadIds = d.items.filter((n) => !n.read).map((n) => n.id);
  view.innerHTML = `${top({ back: true, title: `Notifications${d.unread ? ` · ${d.unread}` : ''}`, right: `<button class="btn sm alt" id="readAll">Mark all read</button>` })}
    ${d.items.length ? d.items.map((n) => `<div class="list">${avatar(icon[n.type] || '✦', 'sm')}
      <div><b>${escapeHtml(n.title)}</b><div class="sub">${escapeHtml(n.body)} · ${new Date(n.created_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</div></div>
      ${n.read ? '' : `<button class="pill" data-read="${n.id}" style="background:var(--orange);color:#fff;border:0;cursor:pointer">new</button>`}</div>`).join('')
      : `<div class="empty"><div class="emoji">◌</div>No more notifications</div>`}`;
  $('#readAll').onclick = async () => {
    await api('/notifications/read', { method: 'POST', body: {} });
    renderNotifications();
  };
  view.querySelectorAll('[data-read]').forEach((b) => (b.onclick = async () => {
    await api('/notifications/read', { method: 'POST', body: { ids: [b.dataset.read] } });
    renderNotifications();
  }));
  void unreadIds;
}

// ---------------- P14 积分 ----------------
async function renderPoints() {
  state.tab = 'home';
  const d = await api('/rewards/ledger');
  view.innerHTML = `${top({ back: true, title: 'My points', right: '<span class="pill" title="Points never affect who you see">no ranking effect</span>' })}
    <div class="hero" style="padding-top:0"><div class="orb" style="width:100px;height:100px;font-size:34px">✦</div>
      <div class="title">${d.balance}</div><div class="sub">available points · ${d.weekly_granted}/100 this week</div></div>
    <div class="card">
      ${d.items.length ? d.items.slice(0, 10).map((r) => `<div class="row" style="padding:4px 0"><span>${escapeHtml(r.reason)} <span class="pill ${r.status === 'granted' ? 'green' : r.status === 'pending' ? 'warn' : 'bad'}">${r.status}</span></span>
        <b style="color:${r.status === 'reversed' ? 'var(--danger)' : 'var(--green)'}">${r.status === 'reversed' ? '-' : '+'}${r.points}</b></div>`).join('')
        : '<p class="sub">No rewards yet. Complete your profile to earn 20 points.</p>'}
    </div>
    <div class="sectiontitle">Redeem</div>
    ${Object.entries(d.catalog).map(([id, it]) => `<div class="list">${avatar('✦', 'sm')}<div><b>${escapeHtml(it.name)}</b><div class="sub">${it.cost} points</div></div>
      <button class="btn sm" data-redeem="${id}" ${d.balance < it.cost ? 'disabled aria-disabled="true"' : ''}>${d.balance < it.cost ? `还差 ${it.cost - d.balance}` : 'Redeem'}</button></div>`).join('')}
    ${d.redemptions?.length ? `<div class="sectiontitle">My unlocks</div>
      <div class="chips">${d.redemptions.map((r) => `<span class="chip on">${escapeHtml(d.catalog[r.item_id]?.name || r.item_id)}</span>`).join('')}</div>` : ''}
    <div class="notice">Points have <b>no cash value</b> and <b>never affect</b> who gets recommended. Rewards: profile complete +20, first match +10, first valid interaction +30 (pending 24h; both sides sent 2+ messages), quality feedback +3 (max 2/week). Reports, logins and room attendance never earn points.</div>`;
  view.querySelectorAll('[data-redeem]').forEach((b) => (b.onclick = async () => {
    try {
      const r = await api('/rewards/redemptions', { method: 'POST', body: { item_id: b.dataset.redeem } });
      toast(`Unlocked ${r.item?.name || b.dataset.redeem} · balance ${r.balance}`);
      renderPoints();
    } catch (e) {
      toast(e.code === 40920 ? `积分不足（还差 ${(e.data?.cost || 0) - (e.data?.balance || 0)}）` : e.message);
    }
  }));
}

// ---------------- P15 我的 ----------------
async function renderMe() {
  state.tab = 'me';
  setActiveTab('me');
  const me = await loadMe();
  view.innerHTML = `${top({ title: 'My profile', right: '<button class="icon-btn" data-go="#/settings" aria-label="设置">⚙</button>' })}
    <div class="hero" style="padding-top:0">${avatar(me.nickname)}<div class="title">${escapeHtml(me.nickname)}</div>
      <p class="sub">${escapeHtml(me.country || '')} · ${escapeHtml((me.languages || []).join(' / '))}</p>
      <span class="pill">${me.profile_strength}% profile strength · ${escapeHtml(me.timezone || '')}</span></div>
    <div class="card"><div class="row"><b>About me</b><button class="chip" id="editBio">Edit</button></div>
      <p class="sub" id="bioText">${escapeHtml(me.bio || 'Add a short intro so people know how to say hi.')}</p></div>
    <div class="card"><div class="row"><b>My interests</b><span class="sub" data-go="#/interests" style="cursor:pointer">Edit ›</span></div>
      <div class="chips">${(me.interests || []).map((i) => `<span class="chip">${escapeHtml(i)}</span>`).join('') || '<span class="sub">Pick at least 3 interests</span>'}</div></div>
    <button class="btn" data-go="#/discover">Preview how I appear</button>
    <div class="card">
      <div class="list" data-go="#/privacy" style="cursor:pointer"><b>Privacy & safety</b><span class="arrow">›</span></div>
      <div class="list" data-go="#/points" style="cursor:pointer"><b>My points</b><span class="arrow">›</span></div>
      <div class="list" data-go="#/settings" style="cursor:pointer"><b>Settings</b><span class="arrow">›</span></div>
    </div>`;
  view.querySelectorAll('[data-go]').forEach((el) => (el.onclick = () => go(el.dataset.go)));
  $('#editBio').onclick = () => {
    const m = modal(`<h3>About me</h3>
      <div class="input"><textarea id="bio" rows="3" maxlength="200" style="width:100%">${escapeHtml(me.bio || '')}</textarea></div>
      <button class="btn" id="saveBio">Save</button><button class="btn alt" data-close>Cancel</button>`);
    m.querySelector('#saveBio').onclick = async () => {
      try {
        state.me = await api('/users/me/profile', { method: 'PATCH', body: { bio: m.querySelector('#bio').value } });
        closeModal(); renderMe();
      } catch (e) { toast(e.message); }
    };
  };
}

// ---------------- P16 隐私安全 ----------------
async function renderPrivacy() {
  state.tab = 'me';
  const [p, blocks] = await Promise.all([api('/users/me/privacy'), api('/blocks')]);
  const row = (key, label) => {
    const opts = [['public', 'Public'], ['matches_only', 'Only matches'], ['private', 'Private']];
    return `<div class="setting"><span>${label}</span>
      <select data-v="${key}" aria-label="${label} visibility" style="background:#ffffff14;border:1px solid var(--line);color:#fff;border-radius:10px;padding:8px">
        ${opts.map(([v, l]) => `<option value="${v}" ${(p[key] || (key === 'timezone_visibility' ? 'matches_only' : 'public')) === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select></div>`;
  };
  view.innerHTML = `${top({ back: true, title: 'Privacy & safety' })}
    <div class="notice">You control what you share. We never reveal who reported someone. Location is never inferred from IP.</div>
    <div class="sectiontitle">Profile visibility</div>
    ${row('country_visibility', 'Country')}${row('interests_visibility', 'Interests')}${row('timezone_visibility', 'Timezone')}
    <div class="sectiontitle">Blocked users (${blocks.length})</div>
    ${blocks.length ? blocks.map((b) => `<div class="list">${avatar(b.nickname || '?', 'sm')}<b style="flex:1">${escapeHtml(b.nickname || b.user_id)}</b>
      <button class="btn sm alt" data-unblock="${b.user_id}">Unblock</button></div>`).join('')
      : '<p class="sub">Nobody is blocked.</p>'}
    <div class="sectiontitle">Safety actions</div>
    <div class="list" data-go="#/report/self" style="cursor:pointer"><span>Report a problem</span><span class="arrow">›</span></div>
    <div class="list" style="cursor:pointer" id="guide"><span>Community guidelines</span><span class="arrow">›</span></div>
    <button class="btn alt" id="export">Download my data (export)</button>
    <button class="btn alt danger" id="delete">Delete account</button>`;
  view.querySelectorAll('[data-v]').forEach((s) => (s.onchange = async () => {
    try { await api('/users/me/privacy', { method: 'PATCH', body: { [s.dataset.v]: s.value } }); toast('Saved. Visible fields refresh on next load.'); }
    catch (e) { toast(e.message); }
  }));
  view.querySelectorAll('[data-unblock]').forEach((b) => (b.onclick = async () => {
    try { await api(`/blocks/${b.dataset.unblock}`, { method: 'DELETE' }); toast('Unblocked.'); renderPrivacy(); } catch (e) { toast(e.message); }
  }));
  $('#guide').onclick = () => modal(`<h3>Community guidelines</h3>
    <p class="sub">Be kind. No harassment, hate, scams or sexual content with minors. CrossMate is adults 18+. Voice rooms are never recorded. Report anything that feels off — reports are private.</p>
    <button class="btn" data-close>OK</button>`);
  $('#export').onclick = async () => {
    try {
      const d = await api('/users/me/privacy/export');
      const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'crossmate-export.json'; a.click();
      toast("Export ready (no tokens, no other people's messages).");
    } catch (e) { toast(e.message); }
  };
  $('#delete').onclick = async () => {
    if (!confirm('Delete your account? You will stop appearing in Discover and your token will be revoked.')) return;
    try { await api('/users/me/deletion', { method: 'POST', body: { confirm: true } }); logoutLocal(); }
    catch (e) { toast(e.message); }
  };
  view.querySelectorAll('[data-go]').forEach((el) => (el.onclick = () => go(el.dataset.go)));
}

// ---------------- P17 举报 ----------------
async function renderReport(target) {
  state.tab = 'me';
  const [kind, id] = target.includes(':') ? target.split(':') : ['user', ''];
  const cats = [['harassment', 'Harassment or bullying'], ['fraud', 'Scam or impersonation'], ['hate', 'Hate or discrimination'],
    ['minor_risk', 'Minor safety risk'], ['privacy_request', 'Privacy or personal information'], ['other', 'Something else']];
  let targetUser = '';
  if (kind === 'room') {
    try {
      const r = await api(`/rooms/${id}`);
      const others = (r.speakers || []).filter((u) => u !== state.me.id);
      targetUser = others[0] || r.host_id;
    } catch { /* ignore */ }
  } else if (kind === 'user') {
    targetUser = id;
  }
  const source = kind === 'room' ? `房间「${id.slice(-6)}」` : `用户 ${targetUser.slice(-6)}`;
  let block = true;
  let cat = '';
  view.innerHTML = `${top({ back: true, title: 'Report a problem' })}
    <p class="sub">Your report is private. Only our trust & safety team sees it. 举报对象：${escapeHtml(source)}</p>
    <div class="card" id="cats">${cats.map(([v, l]) => `<div class="list"><button class="link" data-c="${v}">${l}</button><span class="arrow" data-radio="0">○</span></div>`).join('')}</div>
    <div class="sectiontitle">Add details (optional)</div>
    <div class="input"><textarea id="detail" rows="3" style="width:100%" maxlength="1000" placeholder="Tell us what happened…"></textarea></div>
    <p class="sub" style="font-size:11px">只保存类别与长度等脱敏证据，不会保存聊天原文。</p>
    <div class="setting"><span>Block this person too</span><button class="switch on" id="blockSw" role="switch" aria-checked="true"><i></i></button></div>
    <button class="btn" id="submit">Submit report</button>
    <p class="sub" style="font-size:11px;text-align:center">Reports never earn points. We may contact you about this report.</p>`;
  $('#cats').onclick = (e) => {
    const b = e.target.closest('[data-c]');
    if (!b) return;
    cat = b.dataset.c;
    view.querySelectorAll('[data-radio]').forEach((r) => (r.textContent = '○'));
    b.parentElement.querySelector('[data-radio]').textContent = '◉';
  };
  $('#blockSw').onclick = () => {
    block = !block;
    $('#blockSw').classList.toggle('on', block);
    $('#blockSw').setAttribute('aria-checked', String(block));
  };
  $('#submit').onclick = async () => {
    if (!cat) return toast('请选择举报类别');
    if (!targetUser) return toast('这个入口需要从聊天或房间内发起');
    $('#submit').disabled = true;
    try {
      const r = await api('/reports', { method: 'POST', body: {
        target_user_id: targetUser, target_type: kind, target_id: id || null, category: cat,
        detail: $('#detail').value, also_block: block,
      } });
      modal(`<h3>Report received</h3><p class="sub">${escapeHtml(r.message)}</p>
        <p class="sub">Risk: <b>${escapeHtml(r.risk_level)}</b> · Queue: ${escapeHtml(r.queue)}</p>
        <p class="sub">A human reviewer will look at this. ${block ? 'This person has been blocked.' : ''}</p>
        <button class="btn" data-close>OK</button>`);
      $('#modal .btn').onclick = () => { closeModal(); go(block ? '#/chats' : '#/me'); };
    } catch (e) { toast(e.message); $('#submit').disabled = false; }
  };
}

// ---------------- P18 设置 ----------------
async function renderSettings() {
  state.tab = 'me';
  const rm = localStorage.getItem('cm_reduced_motion') === '1';
  const ds = localStorage.getItem('cm_data_saver') === '1';
  const b = await api('/blocks', { quiet: true }).catch(() => []);
  view.innerHTML = `${top({ back: true, title: t('settings.title') })}
    <div class="sectiontitle">${t('settings.account')}</div>
    <div class="card">
      <button class="setting setting-btn" id="nickRow" style="width:100%;background:none;border:0;border-bottom:1px solid var(--line);color:inherit;font:inherit;cursor:pointer;text-align:left">
        <span>Nickname</span><span class="sub">${escapeHtml(state.me?.nickname || '')} ›</span>
      </button>
      <button class="setting setting-btn" id="langRow" style="width:100%;background:none;border:0;border-bottom:1px solid var(--line);color:inherit;font:inherit;cursor:pointer;text-align:left">
        <span>${t('settings.language')}</span><span class="sub">${escapeHtml(langName())} ›</span>
      </button>
      <button class="setting setting-btn" id="notifyRow" style="width:100%;background:none;border:0;border-bottom:1px solid var(--line);color:inherit;font:inherit;cursor:pointer;text-align:left">
        <span>${t('settings.notifications')}</span><span class="sub">${localStorage.getItem('cm_notify') === '0' ? t('settings.off') : t('settings.on')} ›</span>
      </button>
      <div class="setting"><span>${t('settings.blocked')}</span><span class="sub" data-go="#/privacy" style="cursor:pointer">${b.length} ›</span></div>
    </div>
    <div class="sectiontitle">${t('settings.prefs')}</div>
    <div class="card">
      <div class="setting"><span>${t('settings.reduceMotion')}</span><button class="switch ${rm ? 'on' : ''}" id="rm" role="switch" aria-checked="${rm}"><i></i></button></div>
      <div class="setting"><span>${t('settings.dataSaver')}</span><button class="switch ${ds ? 'on' : ''}" id="ds" role="switch" aria-checked="${ds}"><i></i></button></div>
      <div class="setting"><span>${t('settings.ai')}</span><button class="switch ${aiEnabled ? 'on' : ''}" id="ai" role="switch" aria-checked="${aiEnabled}"><i></i></button></div>
    </div>
    <p class="sub" style="font-size:11px">关闭 AI 建议后，聊天页与房间页不再展示 AI 入口；已发送的消息不受影响。</p>
    <div class="sectiontitle">${t('settings.support')}</div>
    <div class="card">
      <div class="list" data-go="#/privacy" style="cursor:pointer"><span>${t('settings.terms')}</span><span class="arrow">›</span></div>
      <div class="list" id="about" style="cursor:pointer"><span>${t('settings.about')}</span><span class="arrow">›</span></div>
    </div>
    <button class="btn alt" id="logout">${t('settings.logout')}</button>`;

  // 昵称：就地改，走 profile PATCH（和资料页同一个接口）
  $('#nickRow').onclick = () => {
    modal(`<h3 style="margin:0 0 10px">Nickname</h3>
      <div class="input"><input id="nickIn" maxlength="20" value="${escapeHtml(state.me?.nickname || '')}" placeholder="Nickname"></div>
      <p class="sub" style="font-size:11px">2–20 个字符。改昵称不会影响谁能在发现页看到你。</p>
      <button class="btn" id="nickSave">Save</button>
      <button class="btn alt" data-close>Cancel</button>`);
    $('#nickSave').onclick = async () => {
      const v = $('#nickIn').value.trim();
      if (v.length < 2) return toast('昵称至少 2 个字符');
      try {
        state.me = await api('/users/me/profile', { method: 'PATCH', body: { nickname: v } });
        closeModal(); toast('Nickname updated'); renderSettings();
      } catch (e) { toast(e.message); }
    };
  };

  // 通知开关：存在本机（演示里没有推送通道，不能假装发了通知）
  $('#notifyRow').onclick = () => {
    const on = localStorage.getItem('cm_notify') !== '0';
    modal(`<h3 style="margin:0 0 10px">${t('settings.notifications')}</h3>
      <div class="setting" style="border:0"><span>在 App 内提醒新消息与匹配</span>
        <button class="switch ${on ? 'on' : ''}" id="notifySw" role="switch" aria-checked="${on}"><i></i></button></div>
      <p class="sub" style="font-size:11px">演示版本没有系统推送通道，开关只保存本机偏好；生产环境接 FCM/APNs 后才真正推送。</p>
      <button class="btn" id="notifyDone">OK</button>`);
    $('#notifySw').onclick = () => {
      const el = $('#notifySw');
      const now = !el.classList.contains('on');
      el.classList.toggle('on', now);
      el.setAttribute('aria-checked', String(now));
      localStorage.setItem('cm_notify', now ? '1' : '0');
    };
    $('#notifyDone').onclick = () => { closeModal(); renderSettings(); };
  };

  // 语言切换：底部弹层选语言 → 立刻重渲染整页（不需要重启 App）
  $('#langRow').onclick = () => {
    modal(`<h3 style="margin:0 0 10px">${t('settings.language')}</h3>
      <div class="chips" id="langPick" style="flex-direction:column;align-items:stretch">
        ${LANGS.map(([code, name]) => `<button class="chip ${code === getLang() ? 'on' : ''}" data-lang="${code}"
          style="text-align:left;padding:13px 14px;font-size:14px">${escapeHtml(name)}${code === getLang() ? ' ✓' : ''}</button>`).join('')}
      </div>
      <button class="btn alt" data-close>${t('common.cancel')}</button>`);
    $('#langPick').onclick = (e) => {
      const b = e.target.closest('[data-lang]');
      if (!b) return;
      closeModal();
      applyLang(b.dataset.lang);
      toast(t('lang.saved'));
    };
  };
  const toggle = (id, key) => {
    const el = $(id);
    el.onclick = () => {
      const on = !el.classList.contains('on');
      el.classList.toggle('on', on);
      el.setAttribute('aria-checked', String(on));
      localStorage.setItem(key, on ? '1' : '0');
      if (key === 'cm_ai') aiEnabled = on;
      applyPrefs();
    };
  };
  toggle('#rm', 'cm_reduced_motion');
  toggle('#ds', 'cm_data_saver');
  toggle('#ai', 'cm_ai');
  $('#about').onclick = () => modal(`<h3>CrossMate v0.2</h3><p class="sub">A small, honest social app: real conversations, explainable recommendations, AI that never speaks for you.</p>
    <p class="sub">Demo build · API v0.2 · mock AI mode</p><button class="btn" data-close>OK</button>`);
  view.querySelectorAll('[data-go]').forEach((el) => (el.onclick = () => go(el.dataset.go)));
  $('#logout').onclick = async () => {
    await api('/auth/logout', { method: 'POST' }).catch(() => {});
    logoutLocal();
  };
}

function applyPrefs() {
  const rm = localStorage.getItem('cm_reduced_motion') === '1';
  const ds = localStorage.getItem('cm_data_saver') === '1';
  document.body.classList.toggle('reduced-motion', rm);
  document.body.classList.toggle('data-saver', ds);
  if ($('#reduced-motion')) $('#reduced-motion').checked = rm;
  if ($('#data-saver')) $('#data-saver').checked = ds;
}

// ---------------- 演示台 ----------------
function initDev() {
  $('#apiurl').textContent = API;
  $('#reduced-motion').onchange = (e) => { localStorage.setItem('cm_reduced_motion', e.target.checked ? '1' : '0'); applyPrefs(); };
  $('#data-saver').onchange = (e) => { localStorage.setItem('cm_data_saver', e.target.checked ? '1' : '0'); applyPrefs(); };
  $('#switch-user').onclick = () => {
    const users = ['alex', 'sam', 'maya', 'kenji', 'lina', 'nadia', 'omar', 'leo'];
    modal(`<h3>切换演示账号</h3><p class="sub">每个账号都是真实注册的用户数据</p>
      ${users.map((u) => `<button class="btn alt" data-u="${u}">${u}@demo.crossmate.app</button>`).join('')}
      <button class="btn" data-close>Cancel</button>`);
    $('#modal').querySelectorAll('[data-u]').forEach((b) => (b.onclick = async () => {
      try { await loginDemo(b.dataset.u); closeModal(); go('#/home'); } catch (e) { toast(e.message); }
    }));
  };
  $('#seed-btn').onclick = async () => {
    if (!confirm('重置演示数据会清空所有演示账号的匹配、消息与积分。继续？')) return;
    try { await api('/admin/reset', { method: 'POST' }); localStorage.removeItem('cm_devices'); toast('已重置演示数据'); logoutLocal(); }
    catch (e) { toast(e.message); }
  };
  $('#demo-run').onclick = startGuidedDemo;
  applyPrefs();
}

// 一键演示：双账号完成双向喜欢 -> 进入聊天 -> 用 AI 破冰建议回填（不发送）
async function startGuidedDemo() {
  const steps = [
    '① alex 登录并浏览 Discover',
    '② sam 登录，喜欢 alex（双向喜欢即匹配）',
    '③ 回到 alex，打开 Chat，AI 给出 3 条破冰建议',
    '④ 点击某条建议 -> 只回填输入框 -> 由你按发送',
    '⑤ 双方各发 2 条 -> “有效互动”奖励进入 24h 观察期',
    '⑥ Rooms：举手 -> 主持人批准 -> 成为 speaker',
    '⑦ 发起语音通话：双方 media ready 后才开始计时',
  ];
  const m = modal(`<h3>▶ 一键演示</h3><ol style="padding-left:18px;color:#d8cddd;line-height:1.7">${steps.map((s) => `<li>${s}</li>`).join('')}</ol>
    <p class="sub">演示会真实调用 API，过程写入右侧日志。</p>
    <button class="btn" id="runGo">Run demo</button><button class="btn alt" data-close>Cancel</button>`);
  m.querySelector('#runGo').onclick = async () => {
    closeModal();
    try {
      await api('/admin/reset', { method: 'POST' });
      localStorage.removeItem('cm_devices');
      // sam 先喜欢 alex
      await loginDemo('alex');
      const alexId = state.me.id;
      await loginDemo('sam');
      const r = await api('/likes', { method: 'POST', body: { candidate_id: alexId, type: 'like' } });
      await loginDemo('alex');
      const match = await api('/likes', { method: 'POST', body: { candidate_id: r.match ? alexId : (await api('/discover')).items[0].user_id } });
      toast('演示已完成第 ①–② 步：双向喜欢');
      if (match.match) { state.match = match.match; go(`#/match/${match.match.match_id}`); }
      else go('#/discover');
    } catch (e) { toast(e.message); }
  };
}

// ---------------- E2E 钩子 ----------------
// 仅当页面带 ?e2e=1 时暴露内部渲染函数，供 Playwright 驱动（生产构建可移除）
if (new URLSearchParams(location.search).has('e2e')) {
  window.__cmE2E = {
    fire(action, payload = {}) {
      return fetch(API + '/admin/e2e', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, ...payload }) })
        .then(async (r) => {
          const j = await r.json();
          if (!r.ok) throw new Error(`${action}: ${j.message}`);
          return j.data;
        });
    },
    async loginAs(user) {
      const { token } = await this.fire('token', { user });
      saveToken(token);
      await loadMe();
      go('#/home');
      return state.me;
    },
    async act(token, method, path, body) {
      const r = await this.fire('call', { token, method, path, body });
      return r;
    },
    wait: (ms) => new Promise((res) => setTimeout(res, ms)),
    render: (fn, ...args) => Promise.resolve(fn(...args)),
    token: () => state.token,
    me: () => state.me,
  };
}

// ---------------- 启动 ----------------
(async function boot() {
  initDev();
  if (state.token) {
    try { await loadMe(); } catch { logoutLocal(); }
  }
  await route();
})();

// 暴露给 E2E 测试
window.__cm = { state, api, loginDemo, go, route, cfg: { api: API } };