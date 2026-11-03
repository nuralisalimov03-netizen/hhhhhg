/* Local COPY/INSERT reconstruction of an already-signed APK. No networking. */
(() => {
  'use strict';
  const MAX_FILE_SIZE = 0xffffffff;
  const MAX_INSERT_SIZE = 64 * 1024 * 1024;
  const MAX_OPS = 150000;
  const HASH = /^[0-9a-f]{64}$/;
  const byId = id => document.getElementById(id);
  const input = byId('source-file');
  const button = byId('build-button');
  const status = byId('status');
  const message = byId('message');
  const progress = byId('progress');
  const link = byId('download-link');
  const dot = byId('status-dot');
  let busy = false;
  let objectUrl = null;
  let patch = null;
  let valid = false;

  function size(bytes) {
    return new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(bytes / 1024 / 1024) + ' МБ';
  }
  function integer(value, minimum, maximum) {
    return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
  }
  function fail(text) { throw new Error(text); }
  function validatePatch(value) {
    if (!value || typeof value !== 'object' || value.schema !== 1) fail('Комплект сборки отсутствует или повреждён. Распакуйте весь ZIP и откройте index.html.');
    for (const key of ['source','target']) {
      const file = value[key];
      if (!file || !integer(file.size, 1, MAX_FILE_SIZE) || !HASH.test(file.sha256 || '')) fail('В комплекте некорректные сведения об APK. Скачайте архив сборки заново.');
      if (typeof file.name !== 'string' || !file.name || file.name.length > 200) fail('В комплекте некорректное имя APK.');
    }
    if (!Array.isArray(value.ops) || value.ops.length < 1 || value.ops.length > MAX_OPS || typeof value.data !== 'string') fail('Комплект сборки повреждён. Скачайте ZIP заново.');
    if (value.data.length > Math.ceil(MAX_INSERT_SIZE / 3) * 4 + 4 || value.data.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(value.data)) fail('Данные сборки повреждены или слишком велики.');
    const padding = value.data.endsWith('==') ? 2 : value.data.endsWith('=') ? 1 : 0;
    const equalsAt = value.data.indexOf('=');
    if (equalsAt !== -1 && equalsAt !== value.data.length - padding) fail('Данные сборки повреждены.');
    const dataSize = value.data.length / 4 * 3 - padding;
    if (!integer(dataSize, 0, MAX_INSERT_SIZE)) fail('Недопустимый размер данных сборки.');
    let resultSize = 0;
    for (const op of value.ops) {
      if (!Array.isArray(op) || op.length !== 3 || !['copy','insert'].includes(op[0]) || !integer(op[1],0,MAX_FILE_SIZE) || !integer(op[2],1,MAX_FILE_SIZE)) fail('Некорректная команда сборки APK.');
      const limit = op[0] === 'copy' ? value.source.size : dataSize;
      if (op[1] > limit || op[2] > limit - op[1]) fail('Данные сборки выходят за границы файла.');
      resultSize += op[2];
      if (!Number.isSafeInteger(resultSize) || resultSize > value.target.size) fail('Размер результата сборки не совпадает.');
    }
    if (resultSize !== value.target.size) fail('Размер результата сборки не совпадает.');
    return {patch:value, dataSize};
  }
  function setStatus(text, step, state) {
    status.textContent = text;
    progress.value = step;
    dot.className = 'status-dot' + (state ? ' ' + state : '');
  }
  function setError(text) {
    message.textContent = text;
    message.hidden = false;
    setStatus('Сборка не завершена', Number(progress.value) || 0, 'error');
  }
  function clearDownload() {
    link.hidden = true;
    link.removeAttribute('href');
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
  }
  function updateButton() { button.disabled = busy || !valid || !input.files || input.files.length !== 1; }
  function setBusy(value) {
    busy = value;
    input.disabled = value;
    document.body.classList.toggle('is-busy',value);
    updateButton();
    button.firstElementChild.textContent = value ? 'Подготавливаем APK…' : 'Собрать Weqrom';
  }
  async function hash(blob) {
    let bytes = await blob.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    bytes = null;
    return Array.from(new Uint8Array(digest), x=>x.toString(16).padStart(2,'0')).join('');
  }
  async function nextPaint() { await new Promise(resolve => setTimeout(resolve, 0)); }
  function decodeData(encoded) {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i=0; i<binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  async function build() {
    if (busy || !valid) return;
    const source = input.files && input.files[0];
    if (!source) {setError('Сначала выберите скачанный исходный APK.');return;}
    clearDownload();
    message.hidden = true;
    message.textContent = '';
    setBusy(true);
    try {
      setStatus('Шаг 1 из 4 · Проверяем размер исходного APK…',10,'busy');
      await nextPaint();
      if (source.size !== patch.source.size) fail('Выбран другой APK. Нужен файл ' + patch.source.name + ' (' + size(patch.source.size) + ').');
      setStatus('Шаг 1 из 4 · Проверяем SHA-256 исходного APK…',10,'busy');
      if (await hash(source) !== patch.source.sha256) fail('Исходный APK не совпадает с проверенной сборкой. Выберите именно ' + patch.source.name + '.');
      setStatus('Шаг 2 из 4 · Подготавливаем данные Weqrom…',35,'busy');
      await nextPaint();
      const insertData = decodeData(patch.data);
      const chunks = [];
      for (const op of patch.ops) chunks.push(op[0] === 'copy' ? source.slice(op[1],op[1]+op[2]) : insertData.subarray(op[1],op[1]+op[2]));
      const result = new Blob(chunks, {type:'application/vnd.android.package-archive'});
      if (result.size !== patch.target.size) fail('Не совпал итоговый размер APK. Файл не будет предложен для установки.');
      setStatus('Шаг 3 из 4 · Проверяем готовый APK…',70,'busy');
      await nextPaint();
      if (await hash(result) !== patch.target.sha256) fail('Контрольная сумма готового APK не совпала. Результат не сохранён; скачайте комплект заново.');
      objectUrl = URL.createObjectURL(result);
      link.href = objectUrl;
      link.download = patch.target.name;
      byId('target-size').textContent = size(result.size) + ' · контрольная сумма проверена';
      link.hidden = false;
      setStatus('Шаг 4 из 4 · Weqrom готов. Нажмите кнопку скачивания.',100,'ready');
      link.focus({preventScroll:true});
    } catch (error) {
      clearDownload();
      const text = error instanceof RangeError || /memory|allocation|buffer|out of/i.test(String(error && error.message))
        ? 'Браузеру не хватило памяти. Закройте лишние вкладки и попробуйте на компьютере с большим объёмом свободной памяти.'
        : error && error.message ? error.message : 'Не удалось подготовить APK. Попробуйте закрыть лишние вкладки и повторить сборку.';
      setError(text);
    } finally { setBusy(false); }
  }
  input.addEventListener('change', () => {
    if (busy) return;
    clearDownload();
    message.hidden = true;
    message.textContent = '';
    const file = input.files && input.files[0];
    byId('file-name').textContent = file ? file.name + ' · ' + size(file.size) : 'Файл ещё не выбран';
    setStatus(file ? 'Файл выбран. Можно подготовить Weqrom.' : 'Ожидает исходный APK',0,'');
    updateButton();
  });
  button.addEventListener('click',build);
  window.addEventListener('beforeunload',()=>{if(objectUrl)URL.revokeObjectURL(objectUrl);});
  window.WeqromBuilder = Object.freeze({validatePatch});
  try {
    if (!globalThis.crypto || !crypto.subtle || !globalThis.Blob || !Blob.prototype.arrayBuffer || !globalThis.File || !globalThis.URL || !URL.createObjectURL) fail('Этот браузер не поддерживает локальную подготовку APK. Откройте index.html в актуальном Яндекс Браузере, Firefox, Edge или другом современном браузере на компьютере.');
    patch = validatePatch(window.WEQROM_PATCH).patch;
    valid = true;
    byId('source-description').textContent = 'Нужен ' + patch.source.name + ' · ' + size(patch.source.size) + '.';
    setStatus('Ожидает исходный APK',0,'');
    updateButton();
  } catch(error) {valid=false;setError(error.message || 'Комплект сборки повреждён.');updateButton();}
})();
