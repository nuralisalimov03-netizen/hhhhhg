/* Chits Market Firebase REST adapter, bundled locally; no CDN or Firebase SDK.
 *
 * Public config: window.CHITS_FIREBASE (firebase-config.js or settings).
 * API: configured(), getConfig(), configure(config), apps(),
 *      login({username,password}), upload(FormData), delete(id), logout().
 * The administrator's Firebase Auth email maps to nickname Leon_Salimov.
 * Firebase must issue the exact custom claim chitsAdmin:true; the deployed
 * security rules, rather than this client-side check, enforce authorization.
 * ID and refresh tokens live only inside this closure and expire in memory.
 * Storage multipart metadata includes sha256 at object creation. Catalog URLs
 * use public Storage reads governed by rules, never privileged download keys.
 */
(() => {
  'use strict';
  const MAX_APK = 200 * 1024 * 1024;
  const ADMIN = 'Leon_Salimov';
  const CONFIG_KEYS = ['apiKey','authDomain','projectId','storageBucket','appId','adminEmail'];
  let session = null;
  let refreshPromise = null;
  let configurationRevision = 0;

  function copyConfig(input) {
    const source = input && typeof input === 'object' && !Array.isArray(input) ? input : {};
    const result = {enabled: source.enabled === true};
    CONFIG_KEYS.forEach(key => { result[key] = typeof source[key] === 'string' ? source[key].trim() : ''; });
    return result;
  }
  function validate(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Вставьте объект публичной web-конфигурации Firebase.');
    if (Object.keys(raw).some(key => /private.?key|service.?account|password|credential|token|client_email/i.test(key))) throw new Error('Нельзя сохранять секретные ключи, пароли или service-account JSON. Нужна только публичная web-конфигурация Firebase.');
    const cfg = copyConfig(raw);
    if (!cfg.enabled) return cfg;
    if (!/^[A-Za-z0-9_-]{6,200}$/.test(cfg.apiKey)) throw new Error('Укажите публичный apiKey из настроек Firebase web-приложения.');
    if (!/^[a-z][a-z0-9-]{4,61}[a-z0-9]$/.test(cfg.projectId)) throw new Error('Проверьте projectId Firebase.');
    if (!/^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$/.test(cfg.storageBucket) || cfg.storageBucket.includes('..')) throw new Error('Укажите storageBucket без gs://, пути и завершающего слеша.');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cfg.adminEmail) || cfg.adminEmail.length > 254) throw new Error('Укажите email пользователя Firebase Authentication для администратора Leon_Salimov.');
    if (cfg.authDomain && !/^[A-Za-z0-9.-]{1,253}$/.test(cfg.authDomain)) throw new Error('authDomain должен содержать имя домена без https:// и пути.');
    if (cfg.appId.length > 200) throw new Error('Проверьте appId Firebase.');
    return cfg;
  }
  function fingerprint(cfg) { return JSON.stringify(cfg); }
  function getConfig() { return copyConfig(window.CHITS_FIREBASE); }
  function configure(raw) {
    const next = validate(raw);
    if (fingerprint(next) !== fingerprint(getConfig())) {
      session = null;
      refreshPromise = null;
      configurationRevision++;
    }
    window.CHITS_FIREBASE = {...next};
    return {...next};
  }
  function config() {
    const cfg = validate(window.CHITS_FIREBASE || {});
    if (!cfg.enabled) throw new Error('Firebase ещё не подключён. Добавьте публичную конфигурацию проекта в настройках.');
    if (session && session.config !== fingerprint(cfg)) { session = null; configurationRevision++; }
    return cfg;
  }
  function configured() { try { return validate(window.CHITS_FIREBASE || {}).enabled; } catch (_) { return false; } }
  function docBase(cfg) { return 'https://firestore.googleapis.com/v1/projects/' + encodeURIComponent(cfg.projectId) + '/databases/(default)/documents/apps'; }
  function objectBase(cfg, id) { return 'https://firebasestorage.googleapis.com/v0/b/' + encodeURIComponent(cfg.storageBucket) + '/o/' + encodeURIComponent('apks/' + id + '.apk'); }
  function appId(value) { if (!/^[A-Za-z0-9_-]{1,100}$/.test(value || '')) throw new Error('Некорректный идентификатор приложения.'); return value; }
  function apiError(status, data) {
    const details = data && data.error;
    const code = typeof details === 'string' ? details : details && (details.message || details.status) || '';
    const upper = String(code).toUpperCase();
    let text = 'Не удалось выполнить запрос к Firebase. Проверьте настройки проекта и подключение.';
    if (/INVALID_LOGIN_CREDENTIALS|INVALID_PASSWORD|EMAIL_NOT_FOUND|INVALID_EMAIL/.test(upper)) text = 'Неверный никнейм или пароль администратора.';
    else if (/TOO_MANY_ATTEMPTS|TOO_MANY_REQUESTS/.test(upper) || status === 429) text = 'Слишком много попыток. Подождите и попробуйте снова.';
    else if (/USER_DISABLED/.test(upper)) text = 'Аккаунт администратора отключён в Firebase Authentication.';
    else if (/OPERATION_NOT_ALLOWED|PASSWORD_LOGIN_DISABLED/.test(upper)) text = 'Включите вход Email/Password в Firebase Authentication.';
    else if (/API_KEY_INVALID|API_KEY_SERVICE_BLOCKED|PROJECT_NOT_FOUND|CONFIGURATION_NOT_FOUND/.test(upper)) text = 'Проверьте публичную конфигурацию Firebase и включённые сервисы.';
    else if (/BILLING|QUOTA|RESOURCE_EXHAUSTED/.test(upper)) text = 'Firebase сообщил об ограничении квоты или тарифа. Проверьте проект в Firebase Console.';
    else if (status === 401 || /TOKEN_EXPIRED|INVALID_ID_TOKEN|UNAUTHENTICATED|CREDENTIAL_TOO_OLD/.test(upper)) text = 'Сессия администратора истекла. Войдите снова.';
    else if (status === 403 || /PERMISSION_DENIED|INSUFFICIENT_PERMISSION|UNAUTHORIZED/.test(upper)) text = 'Firebase отказал в доступе. Проверьте правила и custom claim chitsAdmin:true у администратора.';
    else if (status === 404) text = 'Ресурс Firebase не найден. Проверьте projectId, Storage bucket и создание базы Firestore.';
    const error = new Error(text);
    error.status = status;
    error.code = upper;
    return error;
  }
  async function jsonRequest(url, options = {}, timeout = 45000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, {...options, credentials:'omit', cache:'no-store', signal:controller.signal});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw apiError(response.status, data);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Firebase не ответил вовремя. Проверьте соединение.');
      if (error instanceof TypeError) throw new Error('Нет соединения с Firebase. Скачанные APK остаются доступны офлайн.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function claims(token) {
    try {
      const chunk = String(token).split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
      const padded = chunk + '='.repeat((4 - chunk.length % 4) % 4);
      return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(padded), c => c.charCodeAt(0))));
    } catch (_) { throw new Error('Firebase вернул некорректный токен входа.'); }
  }
  function requireAdmin(token) {
    if (claims(token).chitsAdmin !== true) throw new Error('У аккаунта нет прав администратора. Назначьте custom claim chitsAdmin:true, затем войдите снова.');
  }
  function logout() { session = null; refreshPromise = null; configurationRevision++; }
  async function login(input) {
    const cfg = config();
    logout();
    if (!input || input.username !== ADMIN) throw new Error('Неверный никнейм или пароль администратора.');
    if (typeof input.password !== 'string' || input.password.length < 1 || input.password.length > 1024) throw new Error('Введите пароль администратора.');
    const revision = configurationRevision;
    const data = await jsonRequest('https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=' + encodeURIComponent(cfg.apiKey), {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:cfg.adminEmail, password:input.password, returnSecureToken:true})
    });
    if (revision !== configurationRevision || fingerprint(cfg) !== fingerprint(getConfig())) throw new Error('Настройки изменились. Войдите снова.');
    if (!data.idToken || !data.refreshToken) throw new Error('Firebase не выдал токен входа.');
    requireAdmin(data.idToken);
    session = {idToken:data.idToken, refreshToken:data.refreshToken, expires:Date.now()+Math.max(60,Number(data.expiresIn)||3600)*1000, config:fingerprint(cfg)};
    return {token:data.idToken, username:ADMIN};
  }
  async function refresh(force = false) {
    const cfg = config();
    if (!session) throw new Error('Войдите как администратор.');
    if (!force && session.expires > Date.now() + 60000) return session.idToken;
    if (refreshPromise) return refreshPromise;
    const old = session;
    const revision = configurationRevision;
    refreshPromise = (async () => {
      try {
        const data = await jsonRequest('https://securetoken.googleapis.com/v1/token?key=' + encodeURIComponent(cfg.apiKey), {
          method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'},
          body:new URLSearchParams({grant_type:'refresh_token', refresh_token:old.refreshToken}).toString()
        });
        if (revision !== configurationRevision || session !== old) throw new Error('Сессия завершена. Войдите снова.');
        if (!data.id_token || !data.refresh_token) throw new Error('Не удалось обновить сессию Firebase.');
        requireAdmin(data.id_token);
        session = {idToken:data.id_token, refreshToken:data.refresh_token, expires:Date.now()+Math.max(60,Number(data.expires_in)||3600)*1000, config:fingerprint(cfg)};
        return session.idToken;
      } catch (error) { if (revision === configurationRevision) session = null; throw error; }
      finally { if (revision === configurationRevision) refreshPromise = null; }
    })();
    return refreshPromise;
  }
  async function authorized(url, options = {}, storage = false, timeout = 45000) {
    const send = token => jsonRequest(url, {...options, headers:{...options.headers, Authorization:(storage ? 'Firebase ' : 'Bearer ') + token}}, timeout);
    let token = await refresh();
    try { return await send(token); }
    catch (error) {
      if (error.status === 401 || /TOKEN_EXPIRED|INVALID_ID_TOKEN|UNAUTHENTICATED/.test(error.code || '')) {
        token = await refresh(true);
        return send(token);
      }
      throw error;
    }
  }
  function value(field, fallback = '') {
    if (!field || typeof field !== 'object') return fallback;
    if ('stringValue' in field) return field.stringValue;
    if ('integerValue' in field) return Number(field.integerValue);
    if ('doubleValue' in field) return Number(field.doubleValue);
    if ('timestampValue' in field) return field.timestampValue;
    if ('booleanValue' in field) return field.booleanValue;
    return fallback;
  }
  function fromDocument(doc, cfg) {
    const fields = doc.fields || {};
    const id = appId(String(doc.name || '').split('/').pop());
    const size = Number(value(fields.size, 0));
    const sha256 = String(value(fields.sha256));
    if (!Number.isInteger(size) || size < 1 || size > MAX_APK || !/^[a-f0-9]{64}$/.test(sha256) || value(fields.storagePath) !== 'apks/' + id + '.apk') throw new Error('В каталоге Firebase есть APK с некорректными метаданными. Проверьте документ ' + id + '.');
    return {id, title:String(value(fields.title,'Приложение')).slice(0,120), description:String(value(fields.description)).slice(0,5000), category:String(value(fields.category,'Приложения')).slice(0,60), versionName:String(value(fields.versionName,'APK')).slice(0,80), packageName:String(value(fields.packageName)).slice(0,200), size, sha256, storagePath:'apks/'+id+'.apk', fileName:String(value(fields.fileName,id+'.apk')), bucket:cfg.storageBucket, downloadUrl:objectBase(cfg,id)+'?alt=media', createdAt:String(value(fields.createdAt)), updatedAt:String(value(fields.updatedAt))};
  }
  async function apps() {
    const cfg = config();
    const result = [];
    let pageToken = '';
    for (let page = 0; page < 5; page++) {
      const params = new URLSearchParams({pageSize:'100'});
      if (pageToken) params.set('pageToken',pageToken);
      const data = await jsonRequest(docBase(cfg)+'?'+params.toString());
      if (Array.isArray(data.documents)) result.push(...data.documents.map(doc => fromDocument(doc,cfg)));
      pageToken = data.nextPageToken || '';
      if (!pageToken) break;
    }
    if (pageToken) throw new Error('В каталоге более 500 приложений. Для такого объёма требуется расширение пагинации маркета.');
    result.sort((a,b) => b.createdAt.localeCompare(a.createdAt));
    return {apps:result};
  }
  function randomId() { return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join(''); }
  function encodeFields(item) {
    const result = {};
    for (const [key,val] of Object.entries(item)) {
      if (key === 'createdAt' || key === 'updatedAt') result[key] = {timestampValue:val};
      else if (typeof val === 'number') result[key] = {integerValue:String(val)};
      else result[key] = {stringValue:String(val)};
    }
    return result;
  }
  function checkApk(bytes) {
    const view = new DataView(bytes);
    if (bytes.byteLength < 22 || view.getUint32(0,true) !== 0x04034b50) throw new Error('Выбранный файл не является APK-архивом.');
    let end = -1;
    for (let at=bytes.byteLength-22; at>=Math.max(0,bytes.byteLength-65557); at--) {
      if (view.getUint32(at,true) === 0x06054b50 && at+22+view.getUint16(at+20,true) === bytes.byteLength) {end=at;break;}
    }
    if (end < 0) throw new Error('APK-архив повреждён или использует неподдерживаемый ZIP64.');
    let offset=view.getUint32(end+16,true);
    const count=view.getUint16(end+10,true);
    const names=new Set();
    for(let i=0;i<count;i++) {
      if(offset+46>bytes.byteLength || view.getUint32(offset,true)!==0x02014b50) throw new Error('APK-архив повреждён.');
      const nameLength=view.getUint16(offset+28,true), extra=view.getUint16(offset+30,true), comment=view.getUint16(offset+32,true);
      if(offset+46+nameLength+extra+comment>bytes.byteLength) throw new Error('APK-архив повреждён.');
      names.add(new TextDecoder().decode(new Uint8Array(bytes,offset+46,nameLength)));
      offset+=46+nameLength+extra+comment;
    }
    if(!names.has('AndroidManifest.xml') || !names.has('classes.dex')) throw new Error('В архиве нет AndroidManifest.xml или classes.dex. Нужен APK приложения.');
  }
  async function upload(form) {
    const cfg=config();
    await refresh();
    const file=form.get('file');
    const title=String(form.get('title')||'').trim();
    const description=String(form.get('description')||'').trim();
    const category=String(form.get('category')||'Приложения').trim();
    if(!title || title.length>120 || description.length>5000 || !category || category.length>60) throw new Error('Проверьте название, описание и категорию приложения.');
    if(!file || typeof file.arrayBuffer!=='function' || !/\.apk$/i.test(file.name||'')) throw new Error('Выберите APK-файл приложения.');
    if(!file.size || file.size>MAX_APK) throw new Error('APK должен иметь размер от 1 байта до 200 МБ.');
    if(!crypto.subtle) throw new Error('Для расчёта SHA-256 нужен HTTPS или локальный Android-интерфейс маркета.');
    const bytes=await file.arrayBuffer();
    checkApk(bytes);
    const hash=await crypto.subtle.digest('SHA-256',bytes);
    const sha256=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
    const id=randomId();
    const storagePath='apks/'+id+'.apk';
    const contentType='application/vnd.android.package-archive';
    const metadata={name:storagePath,contentType,metadata:{sha256}};
    const boundary='chits-'+randomId();
    const body=new Blob([
      '--'+boundary+'\r\nContent-Type: application/json; charset=utf-8\r\n\r\n'+JSON.stringify(metadata)+'\r\n--'+boundary+'\r\nContent-Type: '+contentType+'\r\n\r\n',
      file, '\r\n--'+boundary+'--\r\n'
    ],{type:'multipart/related; boundary='+boundary});
    const endpoint='https://firebasestorage.googleapis.com/v0/b/'+encodeURIComponent(cfg.storageBucket)+'/o?name='+encodeURIComponent(storagePath);
    await authorized(endpoint,{method:'POST',headers:{'Content-Type':'multipart/related; boundary='+boundary,'X-Goog-Upload-Protocol':'multipart'},body},true,600000);
    const now=new Date().toISOString();
    const item={id,title,description,category,versionName:'APK',packageName:'',size:file.size,sha256,storagePath,fileName:file.name.slice(0,180),createdAt:now,updatedAt:now};
    try {
      await authorized(docBase(cfg)+'/'+id+'?currentDocument.exists=false',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({fields:encodeFields(item)})});
    } catch(error) {
      try {await authorized(objectBase(cfg,id),{method:'DELETE'},true);} catch (_) {throw new Error(error.message+' APK остался в Storage: '+storagePath+'. Удалите его через Firebase Console.');}
      throw error;
    }
    return {...item,bucket:cfg.storageBucket,downloadUrl:objectBase(cfg,id)+'?alt=media'};
  }
  async function remove(id) {
    const cfg=config();
    appId(id);
    // Object first: a failed document removal is safely retryable. A missing
    // object on retry counts as removed, so the dangling catalog doc can clear.
    try {await authorized(objectBase(cfg,id),{method:'DELETE'},true);} catch(error) {if(error.status!==404)throw error;}
    await authorized(docBase(cfg)+'/'+id,{method:'DELETE'});
    return {ok:true};
  }
  window.ChitsFirebase={configured,getConfig,configure,apps,login,upload,delete:remove,logout};
})();
