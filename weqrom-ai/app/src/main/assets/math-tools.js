/* Deterministic offline mathematics, separate from the small language model. */
(() => {
  'use strict';
  const script = document.currentScript;
  const assetBase = new URL('.', script && script.src ? script.src : document.baseURI).href;
  const workerURL = new URL('math-worker.js', assetBase).href;
  let active = null;
  let sequence = 0;
  const failure = error => ({text:'',error});

  function calculate(input) {
    if (!input || typeof input !== 'object') return Promise.resolve(failure('Введите математическое выражение.'));
    const mode = input.mode || 'calculate';
    const expression = typeof input.expression === 'string' ? input.expression.trim() : '';
    const variable = input.variable || 'x';
    if (!['calculate','equation','derivative'].includes(mode)) return Promise.resolve(failure('Неизвестный режим расчёта.'));
    if (!expression) return Promise.resolve(failure('Введите математическое выражение.'));
    if (expression.length > 512) return Promise.resolve(failure('Выражение слишком длинное: максимум 512 символов.'));
    if (typeof variable !== 'string' || !/^[a-df-hj-z]$/.test(variable)) return Promise.resolve(failure('Переменная — одна латинская буква, кроме e и i.'));
    if (active) active.finish(failure('Предыдущий расчёт отменён.'));
    return new Promise(resolve => {
      let worker, blobURL, finished = false;
      const id = ++sequence;
      const timer = setTimeout(() => finish(failure('Расчёт превысил 1,5 секунды. Упростите выражение.')),1500);
      function finish(result) {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        if (worker) worker.terminate();
        if (blobURL) URL.revokeObjectURL(blobURL);
        if (active && active.id === id) active = null;
        resolve(result && typeof result === 'object' ? result : failure('Не удалось получить результат.'));
      }
      active = {id,finish};
      try {
        try { worker = new Worker(workerURL); }
        catch (_) {
          // Fixed trusted bootstrap only; no user expression becomes JS source.
          // A file-origin WebView may need worker-src blob: in its own CSP.
          const source = 'self.WEQROM_MATH_ASSET_BASE='+JSON.stringify(assetBase)+';importScripts('+JSON.stringify(workerURL)+');';
          blobURL = URL.createObjectURL(new Blob([source],{type:'application/javascript'}));
          worker = new Worker(blobURL);
        }
        worker.onmessage = event => {
          if (event.data && event.data.id === id) finish(event.data.result);
        };
        worker.onerror = event => {
          if (event.preventDefault) event.preventDefault();
          finish(failure('Математический модуль не загрузился. Проверьте файлы приложения и обновите Android System WebView.'));
        };
        worker.postMessage({id,request:{mode,expression,variable}});
      } catch (_) { finish(failure('В этой версии WebView недоступен отдельный поток для расчёта.')); }
    });
  }
  window.WeqromMath = Object.freeze({calculate});
})();
