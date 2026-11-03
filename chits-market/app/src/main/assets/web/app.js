(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const storage = {
    get(key, fallback) { try { const value = localStorage.getItem(`chits:${key}`); return value === null ? fallback : JSON.parse(value); } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(`chits:${key}`, JSON.stringify(value)); } catch (_) {} }
  };
  const native = window.ChitsNative || null;
  const state = {
    page: 'catalog', apps: [], deviceApps: [], downloads: [], progress: {}, query: '', category: 'all',
    serverUrl: '', connected: false, loading: false, catalogError: '',
    token: '', admin: '', ai: {available: false, installed: false, vision:false, mode:'text', model: 'Qwen2.5 0.5B Instruct'}, photo:null,
    messages: storage.get('messages', []), chatDraft: '', assistantBusy: false, assistantPartial:'', cancelling:false,
    uploadBusy: false, detailId: null, confirm: null, serverGeneration: 0
  };
  if (!Array.isArray(state.messages)) state.messages = [];
  state.messages = state.messages.filter(x => x && ['user','assistant','help'].includes(x.role) && typeof x.text === 'string').slice(-24);
  const main = $('#main');
  let toastTimeout;
  const localApiPending=new Map();
  let localApiSequence=0;

  function toast(text) {
    const el = $('#toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => el.classList.remove('show'), 4200);
  }
  function size(bytes) {
    const value = Number(bytes) || 0;
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1).replace('.', ',')} ГБ`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1).replace('.', ',')} МБ`;
    return `${Math.max(1, Math.round(value / 1024))} КБ`;
  }
  function appName(app) { return app.title || app.name || 'Приложение'; }
  function appIcon(app) {
    const title = appName(app).toLowerCase();
    const kind = title.includes('орбита') || title.includes('orbita') ? 'orbita' : title.includes('прорыв') || title.includes('breach') ? 'breach' : 'generic';
    return `<span class="app-icon ${kind}" aria-hidden="true">${kind === 'orbita' ? '◒' : kind === 'breach' ? 'B›' : esc(appName(app).slice(0, 1).toUpperCase())}</span>`;
  }
  function allApps() {
    const apps = [];
    [...state.deviceApps, ...state.apps, ...state.downloads].forEach(app => {
      const existing = apps.find(x => String(x.id) === String(app.id) || app.sha256 && x.sha256 === app.sha256);
      if (!existing) apps.push(app);
      // Device IDs remain stable when a server seed has an identical APK under a UUID.
      else Object.assign(existing, {...app, ...existing});
    });
    return apps;
  }
  function getApp(id) { return allApps().find(app => String(app.id) === String(id)); }
  function downloaded(id) { const candidate = getApp(id); return state.downloads.find(app => String(app.id) === String(id) || candidate?.sha256 && app.sha256 === candidate.sha256); }
  function currentProgress(id) { return state.progress[id]; }
  function cleanApps(input) {
    return Array.isArray(input) ? input.filter(app => app && typeof app.id === 'string' && app.id.length < 160).map(app => ({...app, title: appName(app)})) : [];
  }
  function readNativeState() {
    if (!native || typeof native.getState !== 'function') return;
    try {
      const raw = native.getState();
      const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (data && typeof data === 'object') {
        if (typeof data.serverUrl === 'string') state.serverUrl = data.serverUrl;
        if (Array.isArray(data.downloads)) state.downloads = cleanApps(data.downloads);
        if (Array.isArray(data.apps)) state.deviceApps = cleanApps(data.apps);
        if (data.ai) {state.ai = {...state.ai, ...data.ai};if(data.ai.status)state.ai.waitingWifi=data.ai.status==='waiting-wifi';}
        if (Object.prototype.hasOwnProperty.call(data, 'assistantPhoto')) state.photo = safePhoto(data.assistantPhoto);
        if (typeof data.firebaseConfig?.enabled === 'boolean' && window.ChitsFirebase) {
          window.ChitsFirebase.configure(data.firebaseConfig);
          storage.set('firebaseConfig', window.ChitsFirebase.getConfig());
        }
      }
    } catch (_) { toast('Не удалось прочитать данные устройства'); }
  }
  const firebase = () => window.ChitsFirebase;
  const firebaseMode = () => Boolean(firebase()?.configured());
  const catalogKey = () => firebaseMode() ? 'firebase:' + firebase().getConfig().projectId : state.serverUrl;
  function safePhoto(photo) {
    if (!photo || typeof photo !== 'object') return null;
    const preview = typeof photo.preview === 'string' && /^data:image\/(jpeg|png|webp);base64,[a-zA-Z0-9+/=]+$/.test(photo.preview) && photo.preview.length < 4000000 ? photo.preview : '';
    return {id:String(photo.id || ''), name:String(photo.name || 'Фотография').slice(0,120), width:Number(photo.width)||0,height:Number(photo.height)||0,preview};
  }
  function updateBadge() {
    const badge = $('#connection-badge');
    badge.classList.toggle('online', state.connected);
    $('span', badge).textContent = state.connected ? firebaseMode() ? 'Firebase подключён' : 'Сервер подключён' : 'Локальный режим';
    const count = $('#download-counter');
    count.textContent = state.downloads.length;
    count.hidden = !state.downloads.length;
  }
  function navigate(page) {
    if (!['catalog','search','downloads','assistant','settings'].includes(page)) page = 'catalog';
    state.page = page;
    document.querySelectorAll('[data-page]').forEach(button => {
      const active = button.dataset.page === page;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    render();
    main.classList.remove('page-enter');
    void main.offsetWidth;
    main.classList.add('page-enter');
    window.scrollTo(0, 0);
  }
  function render() {
    updateBadge();
    if (state.page === 'catalog') renderCatalog();
    else if (state.page === 'search') renderSearch();
    else if (state.page === 'downloads') renderDownloads();
    else if (state.page === 'assistant') renderAssistant();
    else renderSettings();
  }
  function catalogSearch() {
    const categories = [...new Set(allApps().map(app => app.category || 'Приложения'))];
    return `<label class="search-box">${icon('search')}<input id="app-search" type="search" maxlength="100" placeholder="Игры, приложения и кое-что ещё" aria-label="Поиск приложений" value="${esc(state.query)}"></label><div class="chips" aria-label="Категории"><button class="chip ${state.category === 'all' ? 'active' : ''}" data-category="all">Всё</button>${categories.map(category=>`<button class="chip ${state.category === category ? 'active' : ''}" data-category="${esc(category)}">${esc(category)}</button>`).join('')}<button class="chip ${state.category === 'downloaded' ? 'active' : ''}" data-category="downloaded">Сохранённое</button></div>`;
  }
  function renderCatalog() {
    main.innerHTML = `<div class="today-heading"><div><p class="eyebrow">ВАШ ЛИЧНЫЙ ВЫБОР</p><h1>Сегодня<span>.</span></h1></div><button class="profile-orb" data-action="settings" aria-label="Профиль администратора">${state.admin ? esc(state.admin.slice(0,1).toUpperCase()) : 'c'}</button></div>
      <section class="hero editorial-hero" aria-labelledby="hero-title"><div class="hero-grid" aria-hidden="true"></div><div class="eyebrow">В ЦЕНТРЕ ВНИМАНИЯ</div><div class="hero-art" aria-hidden="true"><span class="art-tile art-one">${icon('market')}</span><span class="art-tile art-two">${icon('assistant')}</span><span class="art-dot"></span></div><h1 id="hero-title">Больше свободы.<br>Меньше интернета.</h1><p>Ваши любимые приложения.<br>Уже рядом. Даже без сети.</p><div class="hero-footer"><span class="hero-tag">CHITS COLLECTION</span><button class="hero-cta" data-action="downloads">Открыть загрузки ${icon('arrow')}</button></div></section>
      <div class="section-heading editorial-section"><h2>Создано для вас</h2><span class="section-kicker">ВЫБИРАЙТЕ СВОЁ</span></div><div class="spotlight-strip" aria-label="Подборки Chits"><button class="spotlight spotlight-games" data-action="downloads"><div class="spotlight-art" aria-hidden="true"><span>◒</span><i></i></div><span class="eyebrow">ИГРАЙТЕ ГДЕ УГОДНО</span><strong>Хорошие игры.<br>Без лишних условий.</strong><span class="spotlight-bottom">Сохранённые APK ${icon('arrow')}</span></button><button class="spotlight spotlight-ai" data-action="assistant"><div class="spotlight-art" aria-hidden="true">${icon('assistant')}</div><span class="eyebrow">ТОЛЬКО НА ТЕЛЕФОНЕ</span><strong>Есть вопрос?<br>Есть помощник.</strong><span class="spotlight-bottom">Текст и фотографии ${icon('arrow')}</span></button><button class="spotlight spotlight-owner" data-action="settings"><div class="spotlight-art" aria-hidden="true">${icon('upload')}</div><span class="eyebrow">ДЛЯ ВАШИХ ИДЕЙ</span><strong>Ваш каталог.<br>Ваши правила.</strong><span class="spotlight-bottom">Публикация приложений ${icon('arrow')}</span></button></div>
      <div class="section-heading editorial-section"><h2>Библиотека Chits</h2><button class="text-link" data-action="refresh">Обновить ${icon('refresh')}</button></div>${catalogSearch()}
      ${state.loading ? '<div class="loading-line" aria-label="Обновление каталога"></div>' : ''}<div id="catalog-results"></div>
      ${state.catalogError ? `<div class="inline-note">${esc(state.catalogError)} Скачанные приложения доступны в «Загрузках».</div>` : ''}
      <aside class="offline-note">${icon('shield')}<div><h3>Офлайн — это удобно</h3><p>Сохраните APK один раз. Потом устанавливайте приложение без сети с подтверждением Android.</p></div></aside>`;
    renderCatalogResults();
  }
  function renderSearch() {
    main.innerHTML=`<div class="page-heading search-heading"><div><div class="eyebrow">НАЙДИТЕ ЧТО-ТО СВОЁ</div><h1>Поиск.</h1></div></div>${catalogSearch()}<div class="section-heading"><h2>${state.query ? 'Результаты поиска' : 'Все приложения'}</h2><small>${allApps().length} в каталоге</small></div><div id="catalog-results"></div>`;
    renderCatalogResults();
  }
  function renderCatalogResults() {
    const target = $('#catalog-results');
    if (!target) return;
    const query = state.query.trim().toLocaleLowerCase('ru');
    const apps = allApps().filter(app => (!query || `${appName(app)} ${app.description || ''} ${app.category || ''}`.toLocaleLowerCase('ru').includes(query)) && (state.category === 'all' || state.category === 'downloaded' && downloaded(app.id) || (app.category || 'Приложения') === state.category));
    if (!apps.length) {
      target.innerHTML = `<div class="empty"><div class="empty-icon">${icon(query ? 'search' : 'market')}</div><h2>${query ? 'Пока ничего не нашлось' : 'Ваш маркет начинается здесь'}</h2><p>${query ? 'Попробуйте другое название или категорию.' : 'Подключите сервер, чтобы получать новые приложения. Сохранённые APK будут доступны и без сети.'}</p>${!query ? '<button class="button small" data-action="settings">Подключить сервер</button>' : ''}</div>`;
      return;
    }
    target.innerHTML = `<div class="apps-grid">${apps.map(app => card(app)).join('')}</div>`;
  }
  function card(app) {
    const stored=downloaded(app.id),progress=currentProgress(app.id);
    const pending=progress&&!['error','done','complete','ready','completed'].includes(progress.status);
    return `<article class="app-card"><button class="card-main" data-detail="${esc(app.id)}" aria-label="Подробнее: ${esc(appName(app))}">${appIcon(app)}<span class="card-copy"><h3>${esc(appName(app))}</h3><span class="app-meta">${esc(app.category||'Приложения')} · ${size(app.size)}</span><span class="card-description">${esc(app.description||'Сохранено на устройстве. Установка без интернета.')}</span></span></button><button class="app-button ${stored?'install':''}" data-${stored?'install':'download'}="${esc(app.id)}" ${pending?'disabled':''}>${stored?'Установить':pending?`${Math.round(progress.progress||0)}%`:app.bundled||['orbita-v6','breach-v2'].includes(app.id)?'Сохранить':'Загрузить'}</button></article>`;
  }
  function renderDownloads() {
    const total = state.downloads.reduce((sum, app) => sum + (Number(app.size) || 0), 0);
    const pending = Object.values(state.progress).filter(item => !['done','complete','ready','completed'].includes(item.status));
    main.innerHTML = `<div class="page-heading"><div><div class="eyebrow">ВСЁ ОСТАЁТСЯ С ВАМИ</div><h1>Загрузки<span class="brand-dot">.</span></h1></div><button class="icon-button" data-action="import" aria-label="Добавить APK с устройства">${icon('upload')}</button></div>
      <div class="storage-banner">${icon('download')}<div><strong>${state.downloads.length ? size(total) : '0 МБ'}</strong><small>${state.downloads.length} ${plural(state.downloads.length, ['APK сохранён','APK сохранено','APK сохранено'])} · доступны без интернета</small></div></div>
      <div id="download-list">${pending.map(progressRow).join('')}${state.downloads.map(downloadRow).join('')}</div>
      ${!state.downloads.length && !pending.length ? `<div class="empty"><div class="empty-icon">${icon('download')}</div><h2>Возьмите приложения с собой</h2><p>Скачайте APK из маркета или добавьте файл с телефона. После этого интернет для установки не нужен.</p><button class="button" data-action="catalog">Открыть маркет ${icon('arrow')}</button></div>` : ''}
      <aside class="offline-note">${icon('lock')}<div><h3>Вы решаете, что установить</h3><p>Нажмите «Установить» и разрешите установку из Chits Market в системном окне Android. Никаких скрытых установок.</p></div></aside>
      <button class="button secondary full" data-action="import" style="margin-top:16px">${icon('upload')}Добавить APK с устройства</button>`;
  }
  function plural(n, forms) { return forms[n % 10 === 1 && n % 100 !== 11 ? 0 : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? 1 : 2]; }
  function downloadRow(app) {
    return `<article class="download-row"><div class="download-top">${appIcon(app)}<div class="download-info"><h3>${esc(appName(app))}</h3><small>${esc(app.versionName || 'APK')} · ${size(app.size)} · сохранено</small></div>${icon('check')}</div><div class="download-actions"><button class="button small" data-install="${esc(app.id)}">Установить ${icon('arrow')}</button><button class="icon-button" data-delete-download="${esc(app.id)}" aria-label="Удалить сохранённый APK ${esc(appName(app))}">${icon('trash')}</button></div></article>`;
  }
  function progressRow(progress) {
    const app = getApp(progress.id) || progress.app || {title: 'Приложение'};
    const error = progress.status === 'error';
    const value = Math.max(0, Math.min(100, Number(progress.progress) || 0));
    return `<article class="download-row"><div class="download-top">${appIcon(app)}<div class="download-info"><h3>${esc(appName(app))}</h3><small>${error ? esc(progress.error || 'Загрузка не завершена') : 'Сохраняем APK на устройстве…'}</small></div></div>${error ? `<div class="download-actions"><button class="button small secondary" data-download="${esc(progress.id)}">Повторить загрузку</button></div>` : `<div class="progress-track"><i style="width:${value}%"></i></div><div class="progress-copy"><span>Не закрывайте приложение</span><span>${Math.round(value)}%</span></div>`}</article>`;
  }
  function showDetail(id) {
    const app = getApp(id);
    if (!app) return;
    state.detailId = id;
    const stored = downloaded(id);
    $('#app-detail').innerHTML = `<div class="sheet-top"><span class="eyebrow">CHITS MARKET</span><button class="icon-button" data-action="close-detail" aria-label="Закрыть">${icon('close')}</button></div><div class="detail-heading">${appIcon(app)}<div><h2>${esc(appName(app))}</h2><p>${esc(app.category || 'Приложения')}<br>${esc(app.packageName || 'Android-приложение')}</p></div></div><div class="detail-stats"><div><strong>${esc(app.versionName || 'APK')}</strong><small>ВЕРСИЯ</small></div><div><strong>${size(app.size)}</strong><small>РАЗМЕР APK</small></div><div><strong>${stored ? 'На устройстве' : 'Из каталога'}</strong><small>ДОСТУПНОСТЬ</small></div></div><h3>Об этом приложении</h3><p class="detail-description">${esc(app.description || 'APK-файл сохранён на устройстве. Устанавливайте только приложения, которым доверяете.')}</p>${app.sha256 ? `<p class="eyebrow">КОНТРОЛЬНАЯ СУММА · SHA-256</p><p class="detail-hash">${esc(app.sha256)}</p>` : ''}<div class="inline-note">${stored ? 'APK уже сохранён. Можно установить без подключения к интернету.' : app.bundled || ['orbita-v6','breach-v2'].includes(app.id) ? 'Приложение входит в Chits Market. Можно восстановить APK без интернета.' : 'Для первого скачивания нужен интернет. После сохранения APK установка будет работать офлайн.'}</div><button class="button full" data-${stored ? 'install' : 'download'}="${esc(id)}">${icon(stored ? 'check' : 'download')}${stored ? 'Установить приложение' : app.bundled || ['orbita-v6','breach-v2'].includes(app.id) ? 'Сохранить встроенный APK' : 'Скачать APK · ' + size(app.size)}</button>`;
    const dialog = $('#app-dialog');
    if (!dialog.open) dialog.showModal();
    document.body.classList.add('dialog-open');
  }
  function closeDetail() { $('#app-dialog').close(); state.detailId = null; document.body.classList.remove('dialog-open'); }
  function confirmAction(title, text, action) {
    $('#confirm-title').textContent = title;
    $('#confirm-text').textContent = text;
    state.confirm = action;
    $('#confirm-dialog').showModal();
    document.body.classList.add('dialog-open');
  }
  function closeConfirm() { $('#confirm-dialog').close(); state.confirm = null; document.body.classList.remove('dialog-open'); }
  function install(id) {
    if (!native || typeof native.installApp !== 'function') { toast('Установка APK доступна в Android-приложении Chits Market'); return; }
    if (!downloaded(id)) { toast('Сначала скачайте APK'); return; }
    try { native.installApp(downloaded(id).id); } catch (_) { toast('Не удалось открыть установщик Android'); }
  }
  function download(id) {
    const app = getApp(id);
    if (!app) return;
    if (downloaded(id)) { install(id); return; }
    if (!native || typeof native.downloadApp !== 'function') { toast('Сохранение APK доступно в Android-приложении Chits Market'); return; }
    const url = app.downloadUrl || app.url;
    const bundled = app.bundled || ['orbita-v6','breach-v2'].includes(app.id);
    if (!url && !bundled) { toast('У приложения нет адреса для скачивания. Обновите каталог.'); return; }
    try {
      const data = {...app};
      if (!bundled) {
      const absolute = new URL(url, state.serverUrl ? state.serverUrl + '/' : location.href);
      if (!['https:', 'http:'].includes(absolute.protocol)) throw new Error();
      data.downloadUrl = absolute.href;
      }
      state.progress[id] = {id, status: 'downloading', progress: 0, app: data};
      native.downloadApp(JSON.stringify(data));
      if ($('#app-dialog').open) closeDetail();
      navigate('downloads');
    } catch (_) { delete state.progress[id]; toast('Не удалось начать загрузку. Проверьте адрес сервера.'); }
  }
  function importApk() {
    if (!native || typeof native.openApkPicker !== 'function') { toast('Импорт APK доступен в Android-приложении Chits Market'); return; }
    try { native.openApkPicker(); } catch (_) { toast('Не удалось открыть выбор файла'); }
  }
  function validateServer(raw) {
    const value = raw.trim().replace(/\/+$/, '');
    if (!value) return '';
    let url;
    try { url = new URL(value); } catch (_) { throw new Error('Введите полный адрес: https://market.example.com'); }
    if (url.username || url.password || url.search || url.hash || !['','/'].includes(url.pathname)) throw new Error('Укажите адрес сервера без пути, пароля, параметров и #.');
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1','localhost','10.0.2.2'].includes(url.hostname))) throw new Error('Для удалённого сервера нужен HTTPS. HTTP разрешён только для localhost.');
    return value;
  }
  function localApiEnabled() {
    if(!native||typeof native.requestLocalApi!=='function')return false;
    try{const url=new URL(state.serverUrl);return url.protocol==='http:'&&['127.0.0.1','localhost','10.0.2.2'].includes(url.hostname);}catch(_){return false;}
  }
  function responseError(status,data) {
    if(status===401&&state.token){state.token='';state.admin='';}
    const error=new Error(data?.error||data?.detail||`Сервер ответил: ${status}`);
    error.status=status;return error;
  }
  function requestLocal(path,options,timeout) {
    const id='local-'+Date.now().toString(36)+'-'+(++localApiSequence);
    const value={id,method:options.method||'GET',path,headers:{}};
    if(options.headers?.Authorization)value.headers.Authorization=options.headers.Authorization;
    if(options.body instanceof FormData){
      value.multipart=true;value.fields={};
      for(const key of ['title','description','category'])value.fields[key]=String(options.body.get(key)||'');
      // File bytes stay in Android. The native chooser already granted its URI.
    }else if(typeof options.body==='string')value.body=options.body;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{localApiPending.delete(id);reject(new Error('Локальный сервер не ответил вовремя. Проверьте, что сервер запущен в Termux.'));},timeout);
      localApiPending.set(id,{resolve,reject,timer});
      try{native.requestLocalApi(JSON.stringify(value));}
      catch(_){clearTimeout(timer);localApiPending.delete(id);reject(new Error('Не удалось обратиться к локальному серверу через Android.'));}
    });
  }
  function settleLocalApi(event) {
    const pending=localApiPending.get(event.id);if(!pending)return;
    clearTimeout(pending.timer);localApiPending.delete(event.id);
    let body=event.body;
    if(typeof body==='string'){try{body=JSON.parse(body);}catch(_){body={error:'Локальный сервер вернул неизвестный формат ответа'};}}
    if(!body||typeof body!=='object')body={};
    const status=Number(event.statusCode)||0;
    if(status>=200&&status<300)pending.resolve(body);
    else pending.reject(responseError(status,body.error?body:{error:event.error||'Не удалось связаться с сервером Termux. Проверьте, что он запущен.'}));
  }
  async function request(path, options = {}, timeout = 20000) {
    if (firebaseMode()) {
      if (path === '/api/apps' && (!options.method || options.method === 'GET')) return firebase().apps();
      if (path === '/api/auth/login') return firebase().login(JSON.parse(options.body));
      if (path === '/api/auth/logout') { firebase().logout(); return {ok:true}; }
      if (path === '/api/apps' && options.method === 'POST') return firebase().upload(options.body);
      if (path.startsWith('/api/apps/') && options.method === 'DELETE') return firebase().delete(decodeURIComponent(path.slice('/api/apps/'.length)));
      throw new Error('Неизвестная операция Firebase');
    }
    if (!state.serverUrl) throw new Error('Сначала подключите сервер в настройках.');
    if(localApiEnabled())return requestLocal(path,options,timeout);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(state.serverUrl + path, {...options, signal: controller.signal, credentials: 'omit', cache: 'no-store'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw responseError(response.status,data);
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Сервер не ответил вовремя. Проверьте подключение.');
      if (error instanceof TypeError) throw new Error('Нет связи с сервером. Проверьте адрес и соединение.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function refreshCatalog(silent = false) {
    if (!state.serverUrl && !firebaseMode()) {
      state.connected = false;
      state.catalogError = '';
      if (!silent) toast('Подключите сервер в настройках для новых приложений');
      updateBadge();
      return;
    }
    const generation = state.serverGeneration;
    state.loading = true;
    state.catalogError = '';
    if (state.page === 'catalog') render();
    try {
      const data = await request('/api/apps');
      if (generation !== state.serverGeneration) return;
      if (!Array.isArray(data.apps)) throw new Error('Сервер вернул неизвестный формат каталога.');
      state.apps = cleanApps(data.apps);
      storage.set('catalog', {serverUrl: catalogKey(), apps: state.apps});
      state.connected = true;
      if (!silent) toast('Каталог обновлён');
    } catch (error) {
      if (generation !== state.serverGeneration) return;
      state.connected = false;
      state.catalogError = error.message;
      if (!silent) toast(error.message);
    } finally {
      if (generation === state.serverGeneration) { state.loading = false; render(); }
    }
  }
  function renderSettings() {
    const fb = firebaseMode();
    const config = firebase()?.getConfig() || {enabled:false};
    main.innerHTML = `<div class="page-heading"><div><div class="eyebrow">ВАШ МАРКЕТ, ВАШИ ПРАВИЛА</div><h1>Настройки.</h1></div></div><div class="settings-layout"><div>
      <section class="settings-section"><div class="section-heading"><h2>Firebase</h2>${icon('shield')}</div>
        <p class="muted">Ваш облачный каталог, APK и вход администратора. Переписка и фотографии помощника остаются на телефоне.</p>
        ${fb ? `<div class="inline-note"><strong>${esc(config.projectId)}</strong><br>${esc(config.storageBucket)}<br>${state.connected ? 'Каталог доступен' : 'Офлайн · используем сохранённый каталог'}</div>` : '<div class="inline-note">Проект ещё не подключён. Создайте Firebase по инструкции и вставьте публичную конфигурацию ниже.</div>'}
        <form id="firebase-form" style="margin-top:18px"><label class="field"><span>Публичная конфигурация Firebase · JSON</span><textarea id="firebase-config" name="config" rows="7" spellcheck="false" placeholder='{"enabled":true,"apiKey":"…","projectId":"…","storageBucket":"…","appId":"…","adminEmail":"…"}'>${esc(config.enabled ? JSON.stringify(config,null,2) : '')}</textarea><small>Вставьте web-конфигурацию вашего проекта и adminEmail. Это публичные параметры; пароль и serviceAccount сюда не вводят.</small></label><button class="button full" type="submit">${icon('check')}Подключить Firebase</button><p id="firebase-error" class="form-error" role="alert" hidden></p></form>
        ${fb ? '<button class="text-link" data-action="disable-firebase" style="margin-top:17px">Отключить Firebase на устройстве</button>' : ''}
      </section>
      ${!fb ? `<section class="settings-section"><div class="section-heading"><h2>Свой сервер · альтернатива</h2>${icon('settings')}</div><p class="muted">Можно использовать собственный сервер Chits Market вместо Firebase. Сохранённые APK доступны без него.</p><form id="server-form" style="margin-top:18px"><label class="field"><span>Адрес сервера</span><input id="server-url" name="url" type="url" inputmode="url" autocomplete="url" placeholder="https://market.example.com" value="${esc(state.serverUrl)}"><small>Удалённый доступ — через HTTPS. Домашний localhost работает только на самом устройстве.</small></label><button class="button full" type="submit">Сохранить и проверить ${icon('arrow')}</button><p id="server-error" class="form-error" role="alert" hidden></p></form><div class="status-line">${icon(state.connected ? 'check' : 'lock')}${state.connected ? 'Сервер отвечает · каталог актуален' : 'Локальный режим · сохранённые APK доступны'}</div></section>` : ''}
      <section class="settings-section"><div class="section-heading"><h2>На этом устройстве</h2>${icon('shield')}</div><p class="muted">${native ? 'Установкой управляет Android. Приложения не устанавливаются скрыто.' : 'Открыта браузерная панель маркета. Установка и хранение APK доступны в Android-приложении.'}</p><button class="button secondary full" data-action="import" style="margin-top:16px">${icon('upload')}Добавить локальный APK</button></section>
      </div><section class="settings-section" id="admin-section"><div class="section-heading"><h2>Для администратора</h2>${icon('lock')}</div>${state.token ? adminPanel() : loginPanel()}</section></div><div class="build-label"><span>Chits Market · 1.2</span><span>Сделан, чтобы быть рядом</span></div>`;
  }
  async function saveFirebase(form) {
    try {
      const raw = JSON.parse(String(new FormData(form).get('config') || '{}'));
      if (!firebase()) throw new Error('Модуль Firebase не загрузился');
      const config = firebase().configure({...raw,enabled:true});
      state.serverGeneration++; state.token=''; state.admin=''; state.connected=false;
      storage.set('firebaseConfig', config);
      if(native && typeof native.setFirebaseConfig==='function') native.setFirebaseConfig(JSON.stringify(config));
      const cached=storage.get('catalog',{});
      state.apps=cached.serverUrl===catalogKey()?cleanApps(cached.apps):[];
      render();
      await refreshCatalog();
    } catch(error) {setFormError('#firebase-error', error instanceof SyntaxError ? 'Нужен JSON-объект: проверьте кавычки и запятые.' : error.message);}
  }
  function disableFirebase() {
    firebase()?.configure({enabled:false});
    storage.set('firebaseConfig',{enabled:false});
    if(native && typeof native.setFirebaseConfig==='function')native.setFirebaseConfig(JSON.stringify({enabled:false}));
    state.serverGeneration++;state.token='';state.admin='';state.connected=false;
    const cached=storage.get('catalog',{});state.apps=cached.serverUrl===catalogKey()?cleanApps(cached.apps):[];
    render();if(state.serverUrl)refreshCatalog(true);
  }
  function loginPanel() {
    return `<p class="muted">Войдите, чтобы публиковать APK в каталоге с телефона или другого устройства.</p><form id="login-form" style="margin-top:18px"><label class="field"><span>Никнейм</span><input name="username" id="admin-username" autocomplete="username" value="Leon_Salimov" maxlength="80" required></label><label class="field"><span>Пароль</span><input name="password" id="admin-password" type="password" autocomplete="current-password" maxlength="200" placeholder="Ваш пароль" required></label><button class="button full" type="submit" ${!state.serverUrl && !firebaseMode() ? 'disabled' : ''}>Войти в панель ${icon('arrow')}</button><p id="login-error" class="form-error" role="alert" hidden></p></form><p class="hint">${!state.serverUrl && !firebaseMode() ? 'Сначала подключите Firebase или укажите адрес сервера.' : 'Вход требует соединения с сервером. Пароль не сохраняется в приложении.'}</p>`;
  }
  function adminPanel() {
    return `<div class="admin-welcome"><div><strong>${esc(state.admin)}</strong><small>АДМИНИСТРАТОР МАРКЕТА</small></div><button class="text-link" data-action="logout">Выйти</button></div><form id="upload-form"><label class="field"><span>Название приложения</span><input name="title" maxlength="80" placeholder="Например, Моя игра" required></label><label class="field"><span>Описание</span><textarea name="description" maxlength="3000" placeholder="Для чего приложение и что в нём интересного" required></textarea></label><label class="field"><span>Категория</span><select name="category"><option>Игры</option><option>Инструменты</option><option>Обучение</option><option>Общение</option><option>Приложения</option></select></label><label class="field"><span>APK-файл</span><input name="file" type="file" accept=".apk,application/vnd.android.package-archive" required><small>Загружайте только APK, которыми имеете право делиться. Для пользователей будет рассчитана контрольная сумма.</small></label><button class="button full" type="submit" ${state.uploadBusy ? 'disabled' : ''}>${icon('upload')}${state.uploadBusy ? 'Публикуем приложение…' : 'Опубликовать в маркете'}</button><p id="upload-error" class="form-error" role="alert" hidden></p></form><div class="admin-apps"><div class="section-heading"><h3>В вашем каталоге</h3><small>${state.apps.length}</small></div>${state.apps.map(app => `<div class="admin-item"><div><strong>${esc(appName(app))}</strong><small>${esc(app.versionName || 'APK')} · ${size(app.size)}</small></div><button class="icon-button" data-delete-remote="${esc(app.id)}" aria-label="Удалить из каталога ${esc(appName(app))}">${icon('trash')}</button></div>`).join('') || '<p class="muted">Здесь появятся опубликованные приложения.</p>'}</div>`;
  }
  function setFormError(selector, error) {
    const el = $(selector);
    if (el) { el.textContent = error; el.hidden = !error; }
  }
  async function saveServer(form) {
    try {
      const url = validateServer(new FormData(form).get('url') || '');
      if (url !== state.serverUrl) { state.token = ''; state.admin = ''; state.serverGeneration++; }
      state.serverUrl = url;
      storage.set('serverUrl', url);
      if (native && typeof native.setServerUrl === 'function') native.setServerUrl(url);
      state.connected = false;
      state.catalogError = '';
      const cache = storage.get('catalog', {});
      if (cache.serverUrl === url && Array.isArray(cache.apps)) state.apps = cleanApps(cache.apps); else state.apps = [];
      render();
      if (url) await refreshCatalog(); else toast('Сервер отключён. Сохранённые APK доступны офлайн.');
    } catch (error) { setFormError('#server-error', error.message || 'Не удалось сохранить адрес'); }
  }
  async function login(form) {
    const button = $('button[type=submit]', form);
    button.disabled = true;
    setFormError('#login-error', '');
    try {
      const fields = new FormData(form);
      const data = await request('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:fields.get('username'),password:fields.get('password')})});
      if (!data.token) throw new Error('Сервер не выдал ключ доступа.');
      state.token = data.token;
      state.admin = data.username || fields.get('username');
      form.reset();
      renderSettings();
      toast('Добро пожаловать, ' + state.admin);
    } catch (error) { setFormError('#login-error', error.message); } finally { if (button.isConnected) button.disabled = false; }
  }
  async function upload(form) {
    if (state.uploadBusy) return;
    const fields = new FormData(form);
    const file = fields.get('file');
    if (!file || !file.size || !file.name.toLowerCase().endsWith('.apk')) { setFormError('#upload-error', 'Выберите APK-файл приложения.'); return; }
    if (file.size > 200 * 1024 ** 2) { setFormError('#upload-error', 'APK больше 200 МБ. Выберите файл меньшего размера.'); return; }
    const button = $('button[type=submit]', form);
    state.uploadBusy = true;
    button.disabled = true;
    button.textContent = 'Публикуем приложение…';
    setFormError('#upload-error', '');
    try {
      await request('/api/apps', {method:'POST', headers:{Authorization:'Bearer '+state.token}, body:fields}, 600000);
      form.reset();
      await refreshCatalog(true);
      toast('Приложение опубликовано в маркете');
    } catch (error) { setFormError('#upload-error', error.message); }
    finally { state.uploadBusy = false; if (state.page === 'settings' && (!state.token || !$('#upload-error')?.textContent)) renderSettings(); else if (button.isConnected) {button.disabled = false; button.innerHTML = icon('upload')+'Опубликовать в маркете';} }
  }
  function renderAssistant() {
    const mode=state.ai.mode==='vision'?'vision':'text';
    const profile=(mode==='vision'?state.ai.photo:state.ai.fast)||{};
    const available = Boolean(mode==='vision'?state.ai.vision:state.ai.available);
    const installed = Boolean(mode==='vision'?state.ai.vision:state.ai.installed);
    const vision = Boolean(state.ai.vision);
    const downloading = Boolean(profile.downloading ?? state.ai.downloading);
    const waitingWifi = (profile.status||state.ai.status)==='waiting-wifi';
    const progress = Math.max(0, Math.min(100, Number(profile.progress ?? state.ai.progress) || 0));
    const byteCount=profile.totalBytes||(mode==='vision'?state.ai.totalBytes:0);
    const modelSize=byteCount?size(byteCount):mode==='vision'?'около 3–4 ГБ':'около 400 МБ';
    const embedded=profile.embedded ?? state.ai.embedded;
    const targetModel=profile.model||state.ai.model||(mode==='vision'?'Qwen2.5-VL 3B Instruct':'Qwen2.5 0.5B Instruct');
    const needsFast=mode==='text'&&state.ai.fast&&!state.ai.fast.available;
    const backgroundPhoto=mode==='text'&&state.ai.photo?.downloading;
    const photoBlocked = state.photo && !vision;
    const canSend = available && !state.assistantBusy && !photoBlocked;
    main.innerHTML = `<div class="assistant-layout"><div class="page-heading"><div><div class="eyebrow">ПРИВАТНО. ПРЯМО НА ТЕЛЕФОНЕ.</div><h1>На вашей стороне.</h1></div></div>
      <section class="assistant-intro"><div class="assistant-symbol" aria-hidden="true">${icon('assistant')}</div><div class="eyebrow">CHITS ASSISTANT</div><h2>${mode==='vision'?'Посмотрим фото.<br>Обсудим детали.':'Ваш помощник.<br>Сразу к делу.'}</h2><p>${mode==='vision'?'Фотография обрабатывается на телефоне после подготовки визуальной модели.':'Лёгкая модель для коротких ответов без интернета. Фотографии вынесены в отдельный режим.'} Вопросы и снимки не отправляются в Firebase или на сервер.</p><div class="assistant-status"><i></i>${mode==='vision'?(vision?'Фото готовы · офлайн':'Подготовка визуальной модели'):(available?'Текст готов · офлайн':'Подготовка текстовой модели')}</div></section>
      <div class="ai-mode-control" role="group" aria-label="Режим помощника"><button type="button" data-ai-mode="text" class="ai-mode-tab ${mode==='text'?'selected':''}" aria-pressed="${mode==='text'}" ${state.assistantBusy?'disabled':''}>${icon('assistant')}<span><strong>Быстрый текст</strong><small>Короткие ответы</small></span></button><button type="button" data-ai-mode="vision" class="ai-mode-tab ${mode==='vision'?'selected':''}" aria-pressed="${mode==='vision'}" ${state.assistantBusy?'disabled':''}>${icon('photo')}<span><strong>Фото</strong><small>Отдельная модель</small></span></button></div>${backgroundPhoto?`<div class="background-model-note">${icon('download')}Модель для фото загружается в фоне · ${Math.round(state.ai.photo.progress||0)}%. Текстом можно пользоваться.</div>`:''}<details class="model-details" ${!available||downloading||needsFast?'open':''}><summary><span>${mode==='text'?'Текстовая модель':'Модель для фото'}<small>${embedded?'Включена в APK':available&&!needsFast?'Установлена на телефоне':'Подготовка модели'}</small></span><strong>${downloading?Math.round(progress)+'%':available&&!needsFast?'Готова':'Настроить'}</strong>${icon('arrow')}</summary><section class="settings-section" id="ai-model-panel"><div class="section-heading"><h2>Модель на устройстве</h2>${icon('shield')}</div><div class="model-packaging ${embedded?'embedded':''}">${icon(embedded?'check':'download')}<span>${embedded?'Модель включена в APK':esc(profile.embeddingReason||state.ai.embeddingReason||'Веса этой модели устанавливаются отдельно от APK')}</span></div><p class="muted">${esc(targetModel)} · ${esc(modelSize)}. ${mode==='vision'?'Визуальная модель для вопросов по фотографии.':'Лёгкая модель для коротких текстовых ответов.'} Может ошибаться; скорость зависит от телефона.</p>${needsFast&&available?`<div class="inline-note">Сейчас используется ${esc(state.ai.model||'резервная модель')}. Подготовьте компактную текстовую модель для более быстрых ответов.</div>`:''}
      ${downloading ? `<div class="progress-track"><i style="width:${progress}%"></i></div><div class="progress-copy"><span>${esc(profile.component || state.ai.component || state.ai.file || state.ai.currentFile || 'Загружаем модель…')}</span><span>${Math.round(progress)}%</span></div><p class="hint">Оставьте приложение открытым. ${embedded?'Модель извлекается из APK в память телефона. Интернет не нужен.':mode==='vision'?'Языковая модель и визуальный модуль скачиваются в память телефона.':'Текстовая модель скачивается в память телефона.'}</p>` : installed && !needsFast ? '<div class="status-line">'+icon('check')+(mode==='vision'?'Фотографии готовы к анализу без интернета':'Текстовые ответы готовы без интернета')+'</div>' : `<div class="inline-note">${embedded?'Файлы уже включены в APK. Подготовьте модель для работы; понадобится свободное место.':`${waitingWifi?'Ждём Wi-Fi: загрузка начнётся автоматически при подключении.':mode==='vision'?'Подготовьте модель для фотографий отдельно, когда она понадобится.':'При первом запуске текстовая модель загружается автоматически через Wi-Fi.'} Понадобится ${esc(modelSize)} трафика и свободное место.`}</div><button class="button full" data-action="download-model" style="margin-top:16px">${icon('download')}${waitingWifi ? 'Скачать сейчас · разрешить мобильную сеть' : mode==='vision'?'Подготовить модель для фотографий':'Подготовить быструю текстовую модель'}</button><p class="hint">${embedded?'Подготовка использует файлы из APK без сети.':'Ручной запуск разрешает мобильную сеть.'} После подготовки этой модели интернет для ${mode==='vision'?'анализа фото':'текстовых ответов'} не нужен.</p>`}
      ${mode==='text'?`<button class="button secondary full" data-action="import-model" style="margin-top:12px" ${downloading || state.assistantBusy ? 'disabled' : ''}>${icon('upload')}Импорт текстовой GGUF-модели</button>`:'<p class="hint">Для фотографий нужны языковая модель и визуальный модуль. Кнопка подготовки установит оба файла.</p>'}${state.ai.error ? `<p class="form-error" role="alert">${esc(state.ai.error)}</p>` : ''}${state.ai.reason && (!vision || mode==='text') ? `<p class="hint">${esc(state.ai.reason)}</p>` : ''}</section></details>
      <div class="suggestions" aria-label="Быстрая справка"><button class="suggestion" data-help="offline">Как установить без сети?</button><button class="suggestion" data-help="upload">Как добавить приложение?</button><button class="suggestion" data-help="model">Как работают фото и ИИ?</button></div>
      <div id="chat-messages" class="chat-messages" role="log" aria-label="Переписка с помощником">${state.messages.map(messageHTML).join('')}${state.assistantBusy ? `<div class="message assistant pending-message" id="assistant-pending"><div class="message-label">CHITS ASSISTANT · НА УСТРОЙСТВЕ</div><p id="assistant-live">${esc(state.assistantPartial || 'Готовлю ответ…')}</p><span class="live-cursor" aria-hidden="true"></span><button class="stop-response" data-action="cancel-assistant" ${state.cancelling?'disabled':''}>${icon('close')}${state.cancelling?'Останавливаю…':'Остановить ответ'}</button></div>` : ''}</div>
      <div class="chat-composer">${state.photo ? `<div class="photo-attachment" id="photo-attachment">${state.photo.preview ? `<img src="${esc(state.photo.preview)}" alt="Предпросмотр выбранной фотографии">` : `<span class="photo-placeholder">${icon('photo')}</span>`}<div><strong>${esc(state.photo.name)}</strong><small>${state.photo.width && state.photo.height ? state.photo.width+' × '+state.photo.height+' · ' : ''}Только на устройстве</small></div><button type="button" class="icon-button" data-action="clear-photo" aria-label="Убрать фотографию" ${state.assistantBusy ? 'disabled' : ''}>${icon('close')}</button></div>${photoBlocked ? '<p class="photo-warning" role="status">Фото прикреплено. Для его анализа дождитесь загрузки визуальной модели или уберите фото, чтобы отправить текст.</p>' : ''}` : ''}
      <form id="chat-form" class="chat-form"><button type="button" class="photo-button" data-action="pick-photo" aria-label="Прикрепить фотографию" ${state.assistantBusy ? 'disabled' : ''}>${icon('photo')}</button><input id="chat-input" name="prompt" maxlength="2000" placeholder="${available ? state.photo ? 'Что вы хотите узнать о фото?' : 'Спросите что-нибудь…' : 'Сначала подготовьте модель'}" aria-label="Сообщение помощнику" autocomplete="off" value="${esc(state.chatDraft)}" ${!available ? 'disabled' : ''}><button type="submit" aria-label="Отправить сообщение" ${canSend ? '' : 'disabled'}>${icon('arrow')}</button></form></div>
      <p class="chat-footnote">Фото обрабатывается локально и не попадает в Firebase. Ответы могут быть неточными; проверяйте важные факты.</p></div>`;
  }
  function messageHTML(message) {
    const label = message.role === 'user' ? 'ВЫ' : message.role === 'help' ? 'СПРАВКА МАРКЕТА · БЕЗ ИИ' : 'CHITS ASSISTANT · НА УСТРОЙСТВЕ';
    return `<article class="message ${message.role === 'user' ? 'user' : 'assistant'}"><div class="message-label">${label}</div>${message.photoName ? `<div class="message-photo-label">${icon('photo')}Фото: ${esc(message.photoName)}</div>` : ''}<p>${esc(message.text)}</p></article>`;
  }
  function addMessage(role, text, photoName) {
    state.messages.push({role, text:String(text).slice(0,16000), ...(photoName ? {photoName:String(photoName).slice(0,120)} : {})});
    state.messages = state.messages.slice(-24);
    // Only conversation text and attachment names persist. Never persist image data.
    storage.set('messages', state.messages.map(({role,text,photoName})=>({role,text,...(photoName?{photoName}:{} )})));
    if (state.page === 'assistant') {
      renderAssistant();
      const target = $('#chat-messages');
      if (target) target.scrollIntoView({block:'end',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    }
  }
  function setAssistantMode(mode) {
    if(!['text','vision'].includes(mode)||state.assistantBusy)return;
    try {
      if(mode==='text'&&state.photo&&native&&typeof native.clearAssistantPhoto==='function')native.clearAssistantPhoto();
      if(native&&typeof native.setAssistantMode==='function'){native.setAssistantMode(mode);readNativeState();}
      else {
        const profile=(mode==='vision'?state.ai.photo:state.ai.fast)||{};
        state.ai={...state.ai,...profile,mode,available:mode==='vision'?Boolean(state.ai.vision):Boolean(profile.available),installed:mode==='vision'?Boolean(state.ai.vision):Boolean(profile.installed)};
      }
      state.ai.mode=mode;
      if(state.page==='assistant')renderAssistant();
    } catch(_){toast('Не удалось переключить режим помощника');}
  }
  function pickPhoto() {
    if (state.assistantBusy) return;
    setAssistantMode('vision');
    if (!native || typeof native.pickAssistantPhoto !== 'function') {toast('Выбор фото доступен в Android-приложении. Снимок не загружается на сервер.');return;}
    try {native.pickAssistantPhoto();}catch(_){toast('Не удалось открыть выбор фотографии');}
  }
  function clearPhoto() {
    if (state.assistantBusy) return;
    if (!native || typeof native.clearAssistantPhoto !== 'function') {toast('Управление фото доступно в Android-приложении');return;}
    try {native.clearAssistantPhoto();}catch(_){toast('Не удалось убрать фотографию');}
  }
  function help(kind) {
    const entries = {
      offline:'1. Откройте «Загрузки». Встроенные приложения уже сохранены; другие сначала нужно скачать из каталога.\n2. Нажмите «Установить».\n3. Если Android попросит, разрешите установку из Chits Market в системных настройках, вернитесь и подтвердите её.\nИнтернет для сохранённого APK не нужен. Если приложение само требует сеть, маркет не может сделать его офлайн.',
      upload:'Подключите Firebase в «Настройках» (или свой сервер). Войдите в панель администратора, заполните название, описание и категорию, выберите APK и нажмите «Опубликовать».\nСервер должен быть доступен по интернету для загрузки с другого устройства. Для подключения и входа используйте HTTPS. Публикация потребует соединения; сохранённые APK устанавливаются без него.',
      model:'После первоначальной загрузки Qwen2.5-VL работает прямо на телефоне: отвечает на вопросы и разбирает выбранную фотографию без интернета. Фото и переписка не отправляются в Firebase. Для фото нужны оба файла — модель и визуальный модуль.\nЭто небольшая модель, а не полная офлайн-копия ChatGPT: ответы могут быть неточными, скорость зависит от телефона. Интернет нужен только для первого получения модели. Кнопки справки работают без модели и не генерируют ответы.'
    };
    addMessage('help', entries[kind] || entries.offline);
  }
  function askAssistant(form) {
    const prompt = String(new FormData(form).get('prompt') || '').trim();
    if (!prompt || state.assistantBusy) return;
    if (!native || typeof native.askAssistant !== 'function') { toast('Локальная модель доступна в Android-приложении'); return; }
    if (!state.ai.available) { toast('Сначала подготовьте модель помощника'); return; }
    if (state.photo && !state.ai.vision) {toast('Для фотографии нужна готовая визуальная модель');return;}
    state.chatDraft = '';
    state.assistantBusy = true;
    state.assistantPartial='';state.cancelling=false;
    addMessage('user', prompt, state.photo?.name);
    try { native.askAssistant(prompt); } catch (_) { state.assistantBusy = false; addMessage('help','Не удалось запустить модель на устройстве. Попробуйте ещё раз.'); }
  }
  function cancelAssistant() {
    if(!state.assistantBusy||state.cancelling)return;
    if(!native||typeof native.cancelAssistant!=='function'){toast('Остановка ответа недоступна в этой версии Android-приложения');return;}
    state.cancelling=true;
    const stop=$('[data-action="cancel-assistant"]');if(stop){stop.disabled=true;stop.textContent='Останавливаю…';}
    try{native.cancelAssistant();}catch(_){state.cancelling=false;if(stop){stop.disabled=false;stop.textContent='Остановить ответ';}toast('Не удалось остановить ответ');}
  }
  function downloadModel() {
    if (!native || typeof native.downloadAiModel !== 'function') { toast('Загрузка локальной модели доступна в Android-приложении'); return; }
    if (state.ai.downloading) return;
    state.ai.downloading = true;
    const activeKey=state.ai.mode==='vision'?'photo':'fast';
    if(state.ai[activeKey])state.ai[activeKey]={...state.ai[activeKey],downloading:true,status:'downloading',progress:0};
    state.ai.waitingWifi = false;
    state.ai.error = '';
    state.ai.progress = 0;
    renderAssistant();
    try { if(state.ai.mode==='vision'){if(typeof native.downloadVisionModel!=='function')throw new Error();native.downloadVisionModel();}else native.downloadAiModel(); } catch (_) { state.ai.downloading=false;state.ai.error='Не удалось начать подготовку модели';if(state.ai[activeKey])state.ai[activeKey]={...state.ai[activeKey],downloading:false,status:'error',error:state.ai.error};renderAssistant(); }
  }
  function onNativeEvent(raw) {
    let event;
    try { event = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (_) { return; }
    if (!event || typeof event !== 'object') return;
    if(event.type==='local-api'){settleLocalApi(event);return;}
    if (event.type === 'download') {
      const id = event.id || event.app?.id;
      if (!id) {
        if (event.status === 'error') toast(event.error || 'Не удалось импортировать APK');
        else if (event.status === 'importing') toast('Добавляем APK с устройства…');
        return;
      }
      if (event.status === 'deleted') {
        delete state.progress[id];
        readNativeState();
        render();
        toast('Сохранённый APK удалён');
        return;
      }
      state.progress[id] = {...state.progress[id], ...event, id};
      if (['done','complete','ready','completed','success'].includes(event.status)) {
        delete state.progress[id];
        readNativeState();
        if (event.app && !downloaded(id)) state.downloads.push(event.app);
        toast('APK сохранён. Можно устанавливать без интернета.');
      } else if (event.status === 'error') toast(event.error || 'Не удалось скачать APK');
      if (['downloads','catalog','search'].includes(state.page)) render();
      if (state.detailId) showDetail(state.detailId);
    } else if (event.type === 'install') {
      if (event.status === 'error' || event.status === 'permission') toast(event.error || 'Подтвердите разрешение на установку в Android');
      else if (event.status === 'opened') toast('Подтвердите установку в окне Android');
    } else if (event.type === 'settings') {
      if (event.status === 'saved') { readNativeState(); storage.set('serverUrl', state.serverUrl); }
      if (event.status === 'error') {
        state.serverGeneration++;
        readNativeState();
        storage.set('serverUrl', state.serverUrl);
        state.connected = false;
        if (state.page === 'settings') renderSettings();
        setFormError('#server-error', event.error || 'Не удалось сохранить адрес сервера');
        toast(event.error || 'Адрес не сохранён');
      }
    } else if (['import','imported','downloads-changed','state'].includes(event.type)) {
      readNativeState();
      if (event.app && !downloaded(event.app.id)) state.downloads.push(event.app);
      render();
      if (event.error) toast(event.error); else if (event.type !== 'state') toast('Данные устройства обновлены');
    } else if (event.type === 'photo') {
      if (event.status === 'ready') {if(state.ai.mode!=='vision')setAssistantMode('vision');state.photo=safePhoto(event); if(state.page==='assistant')renderAssistant();}
      else if(event.status==='cleared'){state.photo=null;if(state.page==='assistant')renderAssistant();}
      else if(event.status==='error')toast(event.error||'Не удалось прочитать фотографию');
    } else if(event.type === 'firebase-config') {
      if(event.status==='error') {state.serverGeneration++;readNativeState();state.connected=false;setFormError('#firebase-error',event.error||'Не удалось сохранить Firebase');toast(event.error||'Firebase не настроен');}
      else if(event.status==='saved') toast('Публичная конфигурация Firebase сохранена');
    } else if(event.type==='assistant-mode') {
      if(event.status==='error')toast(event.error||'Не удалось переключить режим');
      else {readNativeState();if(state.page==='assistant')renderAssistant();}
    } else if (event.type === 'ai-model') {
      const eventMode=event.mode||state.ai.mode||'text';
      const profileKey=eventMode==='vision'?'photo':'fast';
      if(state.ai[profileKey])state.ai[profileKey]={...state.ai[profileKey],...event,downloading:event.status==='downloading'};
      if(eventMode!==(state.ai.mode||'text')){readNativeState();if(state.page==='assistant')renderAssistant();updateBadge();return;}
      state.ai.status=event.status;
      state.ai.waitingWifi=event.status==='waiting-wifi';
      state.ai.file=event.file||event.currentFile||'';
      state.ai.component=event.component||'';
      if(event.downloadedBytes)state.ai.downloadedBytes=event.downloadedBytes;
      if(event.totalBytes)state.ai.totalBytes=event.totalBytes;
      if(event.model)state.ai.model=event.model;
      state.ai.downloading = event.status === 'downloading';
      state.ai.progress = event.progress || 0;
      state.ai.error = event.status === 'error' ? event.error || 'Не удалось загрузить модель' : '';
      if (event.status === 'ready') { state.ai.available = true; state.ai.installed = true; readNativeState(); toast('Модель готова. Теперь помощник работает офлайн.'); }
      if (state.page === 'assistant') renderAssistant();
    } else if (event.type === 'assistant') {
      if(event.status==='generating') {
        state.assistantBusy=true;
      } else if(event.status==='partial') {
        state.assistantPartial=String(event.text||'');
        const live=$('#assistant-live');
        if(live)live.textContent=state.assistantPartial;
      } else if(event.status==='done') {
        const answer=event.text||state.assistantPartial||'Модель не вернула ответ. Попробуйте другой вопрос.';
        state.assistantBusy=false;state.assistantPartial='';state.cancelling=false;
        addMessage('assistant',answer);
      } else if(event.status==='cancelled') {
        const answer=event.text||state.assistantPartial;
        state.assistantBusy=false;state.assistantPartial='';state.cancelling=false;
        if(answer)addMessage('assistant',answer+'\n\nОтвет остановлен.');
        else {if(state.page==='assistant')renderAssistant();toast('Ответ остановлен');}
      } else if(event.status==='error') {
        state.assistantBusy=false;state.assistantPartial='';state.cancelling=false;
        addMessage('help',event.error||'Ошибка локальной модели. Попробуйте короткий вопрос.');
      }
    } else if (event.type === 'error') toast(event.error || event.message || 'Не удалось выполнить действие');
    else if (event.type === 'installed' || event.type === 'deleted') { readNativeState(); render(); }
    updateBadge();
  }
  function goBack() {
    if ($('#confirm-dialog').open) { closeConfirm(); return true; }
    if ($('#app-dialog').open) { closeDetail(); return true; }
    if (state.page !== 'catalog') { navigate('catalog'); return true; }
    return false;
  }
  window.Chits = {onNativeEvent, navigate, goBack, refresh:() => refreshCatalog(), getState:() => ({page:state.page,apps:allApps(),downloads:state.downloads,connected:state.connected,ai:state.ai,photo:state.photo,firebase:firebaseMode()})};

  document.addEventListener('input', event => {
    if (event.target.id === 'app-search') { state.query = event.target.value; renderCatalogResults(); }
    if (event.target.id === 'chat-input') state.chatDraft = event.target.value;
  });
  document.addEventListener('submit', event => {
    const id = event.target.id;
    if (!['server-form','firebase-form','login-form','upload-form','chat-form'].includes(id)) return;
    event.preventDefault();
    if (id === 'server-form') saveServer(event.target);
    if (id === 'firebase-form') saveFirebase(event.target);
    if (id === 'login-form') login(event.target);
    if (id === 'upload-form') upload(event.target);
    if (id === 'chat-form') askAssistant(event.target);
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('button,a.brand');
    if (!button || button.disabled) return;
    if (button.matches('a.brand')) { event.preventDefault(); navigate('catalog'); return; }
    if (button.dataset.page) { navigate(button.dataset.page); return; }
    if (button.dataset.aiMode) {setAssistantMode(button.dataset.aiMode);return;}
    if (button.dataset.category) { state.category = button.dataset.category; state.page==='search'?renderSearch():renderCatalog(); return; }
    if (button.dataset.detail) { showDetail(button.dataset.detail); return; }
    if (button.dataset.install) { install(button.dataset.install); return; }
    if (button.dataset.download) { download(button.dataset.download); return; }
    if (button.dataset.help) { help(button.dataset.help); return; }
    if (button.dataset.deleteDownload) {
      const id = button.dataset.deleteDownload;
      confirmAction('Удалить сохранённый APK?', 'Установленное приложение останется на телефоне. Чтобы установить его снова без сети, APK придётся сохранить заново.', () => {
        if (!native || typeof native.deleteDownload !== 'function') {toast('Удаление APK доступно в Android-приложении'); return;}
        try { native.deleteDownload(id); } catch (_) {toast('Не удалось удалить APK');}
      }); return;
    }
    if (button.dataset.deleteRemote) {
      const id = button.dataset.deleteRemote;
      confirmAction('Убрать из маркета?', 'Новые пользователи больше не увидят это приложение. Ранее скачанные копии останутся на их устройствах.', async () => {
        try { await request('/api/apps/'+encodeURIComponent(id), {method:'DELETE',headers:{Authorization:'Bearer '+state.token}}); await refreshCatalog(true); toast('Приложение удалено из каталога'); } catch (error) {toast(error.message); if (state.page === 'settings' && !state.token) renderSettings();}
      }); return;
    }
    if (button.id === 'confirm-button') { const action = state.confirm; closeConfirm(); if (action) action(); return; }
    const action = button.dataset.action;
    if (['catalog','search','downloads','assistant','settings'].includes(action)) navigate(action);
    if (action === 'refresh') refreshCatalog();
    if (action === 'cancel-assistant') cancelAssistant();
    if (action === 'import') importApk();
    if (action === 'close-detail') closeDetail();
    if (action === 'close-confirm') closeConfirm();
    if (action === 'logout') {const token=state.token;state.token='';state.admin='';if(token && (state.serverUrl || firebaseMode()))request('/api/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+token}}).catch(()=>{});renderSettings();toast('Вы вышли из панели администратора');}
    if (action === 'download-model') downloadModel();
    if (action === 'pick-photo') pickPhoto();
    if (action === 'clear-photo') clearPhoto();
    if (action === 'disable-firebase') disableFirebase();
    if (action === 'import-model') {if(native && typeof native.importAiModel==='function')native.importAiModel();else toast('Импорт GGUF доступен в Android-приложении');}
  });
  ['app-dialog','confirm-dialog'].forEach(id => {
    const dialog = $('#'+id);
    dialog.addEventListener('click', event => { if (event.target === dialog) {const bounds=dialog.getBoundingClientRect();if(event.clientY<bounds.top || event.clientY>bounds.bottom || event.clientX<bounds.left || event.clientX>bounds.right) dialog.close();} });
    dialog.addEventListener('close', () => {document.body.classList.remove('dialog-open');if(id==='app-dialog')state.detailId=null;else state.confirm=null;});
  });
  window.addEventListener('offline', () => {state.connected=false;updateBadge();});
  window.addEventListener('online', () => {if(state.serverUrl||firebaseMode())refreshCatalog(true);});
  window.addEventListener('focus', () => {readNativeState();updateBadge();if(state.page==='downloads')renderDownloads();else if(state.page==='assistant')renderAssistant();});

  state.serverUrl = storage.get('serverUrl', '') || (!native && ['http:','https:'].includes(location.protocol) ? location.origin : '');
  const storedFirebase=storage.get('firebaseConfig',null);
  if(storedFirebase && firebase()){try{firebase().configure(storedFirebase);}catch(_) {}}
  readNativeState();
  if(firebaseMode() && native && typeof native.setFirebaseConfig==='function')native.setFirebaseConfig(JSON.stringify(firebase().getConfig()));
  const cached = storage.get('catalog', {});
  if (!state.apps.length && cached.serverUrl === catalogKey()) state.apps = cleanApps(cached.apps);
  navigate('catalog');
  if (state.serverUrl || firebaseMode()) refreshCatalog(true);
})();
