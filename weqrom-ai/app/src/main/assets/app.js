/* Weqrom AI: local UI. Model answers come only from the native generator. */
(function () {
  'use strict';
  // Android's updatable WebView can be older than the installed OS image.
  if (!Element.prototype.replaceChildren) Element.prototype.replaceChildren = function () {
    while (this.firstChild) this.removeChild(this.firstChild);
    for (var n = 0; n < arguments.length; n += 1) this.appendChild(arguments[n]);
  };
  var byId = function (id) { return document.getElementById(id); };
  var HISTORY_KEY = 'weqrom-ai-chat-v1';
  var MAX_MESSAGES = 20;
  var MAX_TEXT = 12000;
  var CONTEXT_CHARACTERS = 1800;
  var FENCE = String.fromCharCode(96, 96, 96);
  var history = [];
  var active = null;
  var requestCounter = 0;
  var model = {state: 'loading', model: 'Weqrom Seed', parameters: null};
  var statusTimer = null;
  var toastTimer = null;
  var renderFrame = null;
  var modalReturnFocus = null;
  var gameFilename = 'weqrom-game.html';
  var mathRunId = 0;
  var contextNotice = document.createElement('p');
  contextNotice.id = 'context-notice';
  contextNotice.className = 'quiet-note';
  contextNotice.setAttribute('role', 'status');
  contextNotice.hidden = true;
  byId('model-error').parentNode.insertBefore(contextNotice, byId('model-error'));

  function nativeBridge() { return window.WeqromNative || null; }
  function decode(value) {
    if (typeof value === 'string') return JSON.parse(value);
    if (value && typeof value === 'object') return value;
    throw new Error('Некорректное состояние модели');
  }
  function limitedText(value, maximum) { return typeof value === 'string' ? value.slice(0, maximum) : ''; }
  function id(prefix) { requestCounter += 1; return prefix + '-' + Date.now() + '-' + requestCounter; }
  function announce(value) { byId('announcer').textContent = value; }
  function toast(value) {
    if (toastTimer) window.clearTimeout(toastTimer);
    byId('toast').textContent = value;
    byId('toast').hidden = false;
    toastTimer = window.setTimeout(function () { byId('toast').hidden = true; }, 2600);
  }
  function saveHistory() {
    var saved = history.slice(-MAX_MESSAGES).map(function (message) {
      return {id: message.id, role: message.role, text: message.text.slice(0, MAX_TEXT),
        status: message.status === 'generating' || message.status === 'stopping' ? 'interrupted' : message.status,
        error: limitedText(message.error, 500), source: message.source === 'math' ? 'math' : 'model',
        finishReason: ['eos', 'length', 'cancelled'].indexOf(message.finishReason) >= 0 ? message.finishReason : '',
        steps: Array.isArray(message.steps) ? message.steps.slice(0, 12).map(function (step) { return String(step).slice(0, 1000); }) : []};
    });
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(saved)); }
    catch (error) { toast('Историю не удалось сохранить на телефоне'); }
  }
  function loadHistory() {
    try {
      var stored = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      if (!Array.isArray(stored)) return [];
      return stored.filter(function (item) {
        return item && (item.role === 'user' || item.role === 'assistant') && typeof item.text === 'string';
      }).slice(-MAX_MESSAGES).map(function (item) {
        var status = ['complete', 'cancelled', 'error', 'interrupted'].indexOf(item.status) >= 0 ? item.status : 'interrupted';
        return {id: typeof item.id === 'string' ? item.id.slice(0, 100) : id('stored'), role: item.role,
          text: item.text.slice(0, MAX_TEXT), status: item.role === 'user' ? 'complete' : status,
          error: limitedText(item.error, 500), source: item.source === 'math' ? 'math' : 'model',
          finishReason: ['eos', 'length', 'cancelled'].indexOf(item.finishReason) >= 0 ? item.finishReason : '',
          steps: Array.isArray(item.steps) ? item.steps.slice(0, 12).map(function (step) { return String(step).slice(0, 1000); }) : []};
      });
    } catch (error) { return []; }
  }
  function trimHistory() {
    if (history.length > MAX_MESSAGES) history = history.slice(-MAX_MESSAGES);
  }
  function contextMessages() {
    // At most six user/assistant turns. A short context fits the small native
    // model; stored history and inference context are deliberately separate.
    // Reserve the row for the new assistant answer so notice counts correspond
    // to the bounded history the user will actually see after sending.
    var retained = history.slice(-(MAX_MESSAGES - 1));
    var current = retained[retained.length - 1];
    var answer = [{role: 'user', content: current.text}];
    var remaining = CONTEXT_CHARACTERS - current.text.length;
    var pairs = [];
    for (var i = retained.length - 2; i > 0; i -= 1) {
      var assistant = retained[i]; var user = retained[i - 1];
      // Cancelled/failed replies do not leave orphan user turns in ChatML.
      if (assistant.role !== 'assistant' || assistant.status !== 'complete' || !assistant.text.trim() || user.role !== 'user' || !user.text.trim()) continue;
      pairs.push({user: user.text, assistant: assistant.text}); i -= 1;
    }
    var included = 0;
    for (var pair = 0; pair < pairs.length && included < 5; pair += 1) {
      var question = pairs[pair].user; var reply = pairs[pair].assistant;
      if (question.length + reply.length > remaining) break;
      answer.unshift({role: 'assistant', content: reply});
      answer.unshift({role: 'user', content: question});
      remaining -= question.length + reply.length; included += 1;
    }
    contextNotice.hidden = included === pairs.length;
    if (!contextNotice.hidden) {
      var latestCode = pairs.length && (pairs[0].assistant.indexOf(FENCE) >= 0 || /<\/?(?:html|script|style)\b|<!doctype\s+html/i.test(pairs[0].assistant));
      contextNotice.textContent = included === 0
        ? (latestCode ? 'Предыдущий код не передан модели: он не помещается в короткий контекст. Полный код остаётся в истории и доступен для сохранения.'
          : 'В этот ответ передан только новый вопрос: предыдущий диалог не помещается в короткий контекст. Остальные сообщения доступны в сохранённой истории.')
        : 'Для ответа переданы последние полные пары сообщений: ' + included + ' из ' + pairs.length + '. Остальные сохранённые сообщения не переданы модели.';
    }
    return answer;
  }
  function scrollToMessage() {
    var last = byId('messages').lastElementChild;
    if (last) last.scrollIntoView({block: 'nearest', behavior: 'auto'});
  }
  function copyText(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      return navigator.clipboard.writeText(text).then(function () { toast('Скопировано'); }, function () { legacyCopy(text); });
    }
    legacyCopy(text);
    return Promise.resolve();
  }
  function legacyCopy(text) {
    var field = document.createElement('textarea');
    field.value = text;
    field.className = 'sr-only';
    field.setAttribute('readonly', '');
    document.body.appendChild(field);
    field.select();
    try {
      if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
      toast('Скопировано');
    } catch (error) { toast('Не удалось скопировать. Выделите текст вручную.'); }
    field.remove();
  }
  function previewHtml(html) {
    var bridge = nativeBridge();
    if (!bridge || typeof bridge.previewHtml !== 'function') { toast('Предпросмотр доступен в APK Weqrom AI'); return; }
    if (!html.trim()) { toast('Сначала добавьте HTML-код'); return; }
    try { bridge.previewHtml(html); }
    catch (error) { toast('Не удалось открыть предпросмотр'); }
  }
  function exportHtml(html, filename) {
    var bridge = nativeBridge();
    if (!bridge || typeof bridge.exportHtml !== 'function') { toast('Сохранение HTML доступно в APK Weqrom AI'); return; }
    if (!html.trim()) { toast('Сначала добавьте HTML-код'); return; }
    try { bridge.exportHtml(html, filename); }
    catch (error) { toast('Не удалось сохранить HTML'); }
  }
  function makeButton(label, action) {
    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', action);
    return button;
  }
  function renderAnswer(body, value) {
    body.replaceChildren();
    var expression = new RegExp('^' + FENCE + '([^\\n]*)\\n([\\s\\S]*?)^' + FENCE + '[ \\t]*$', 'gm');
    var match;
    var end = 0;
    while ((match = expression.exec(value)) !== null) {
      if (match.index > end) body.appendChild(document.createTextNode(value.slice(end, match.index)));
      var language = match[1].trim().toLowerCase();
      var code = match[2].replace(/\n$/, '');
      var block = document.createElement('div'); block.className = 'code-block';
      var head = document.createElement('div'); head.className = 'code-block-head';
      var name = document.createElement('span'); name.textContent = language.slice(0, 24) || 'Код';
      head.appendChild(name);
      head.appendChild(makeButton('Копировать', (function (source) { return function () { copyText(source); }; }(code))));
      var pre = document.createElement('pre'); var content = document.createElement('code');
      content.textContent = code; pre.appendChild(content);
      block.appendChild(head); block.appendChild(pre);
      if (language === 'html' || (!language && /^\s*(?:<!doctype\s+html|<html[\s>])/i.test(code))) {
        var actions = document.createElement('div'); actions.className = 'code-block-actions';
        actions.appendChild(makeButton('Открыть HTML', (function (source) { return function () { previewHtml(source); }; }(code))));
        actions.appendChild(makeButton('Сохранить .html', (function (source) { return function () { exportHtml(source, 'weqrom-answer.html'); }; }(code))));
        block.appendChild(actions);
      }
      body.appendChild(block); end = expression.lastIndex;
    }
    if (end < value.length) body.appendChild(document.createTextNode(value.slice(end)));
  }
  function renderMessage(message) {
    var article = document.createElement('article');
    article.className = 'message ' + message.role + (message.source === 'math' ? ' tool' : '');
    article.dataset.id = message.id;
    var meta = document.createElement('div'); meta.className = 'message-meta';
    meta.textContent = message.role === 'user' ? 'Вы' : message.source === 'math' ? 'Офлайн-вычисление' : 'Weqrom Seed';
    var body = document.createElement('div'); body.className = 'message-body';
    article.appendChild(meta); article.appendChild(body);
    if (message.role === 'assistant' && message.status !== 'generating' && message.status !== 'stopping') renderAnswer(body, message.text);
    else body.textContent = message.text;
    if (!message.text && message.role === 'assistant' && (message.status === 'generating' || message.status === 'stopping')) {
      body.textContent = message.source === 'math' ? 'Считаем…' : message.status === 'stopping' ? 'Останавливаем ответ…' : 'Модель пишет…';
      body.classList.add('thinking');
    }
    if (message.error) {
      var error = document.createElement('p'); error.className = 'message-error'; error.textContent = message.error;
      article.appendChild(error);
    }
    if (message.source === 'math' && Array.isArray(message.steps) && message.steps.length) {
      var steps = document.createElement('ol'); steps.className = 'chat-math-steps';
      message.steps.forEach(function (step) { var line = document.createElement('li'); line.textContent = step; steps.appendChild(line); });
      article.appendChild(steps);
    }
    if (message.role === 'assistant') {
      var footer = document.createElement('div'); footer.className = 'message-footer';
      var status = document.createElement('span');
      status.textContent = message.status === 'cancelled' ? 'Ответ остановлен' : message.status === 'interrupted' ? 'Ответ прерван при закрытии приложения' : message.status === 'error' ? 'Не удалось завершить ответ' : message.finishReason === 'length' ? 'Достигнут лимит длины; ответ может быть незавершён' : '';
      footer.appendChild(status);
      if (message.text) {
        var copy = makeButton('Копировать', function () { copyText(message.text); });
        copy.className = 'copy-answer'; footer.appendChild(copy);
      }
      article.appendChild(footer);
    }
    return article;
  }
  function renderMessages() {
    var list = byId('messages'); list.replaceChildren();
    history.forEach(function (message) { list.appendChild(renderMessage(message)); });
    byId('chat-empty').hidden = history.length > 0;
    updateComposer();
  }
  function queueStreamRender() {
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(function () {
      renderFrame = null;
      if (!active) return;
      var article = byId('messages').lastElementChild;
      if (!article || article.dataset.id !== active.message.id) return;
      var body = article.querySelector('.message-body');
      body.classList.remove('thinking'); body.textContent = active.message.text;
      scrollToMessage();
    });
  }
  function updateComposer() {
    var busy = !!active;
    var math = classifyMath(byId('chat-input').value.trim());
    var mathAvailable = math && window.WeqromMath && typeof window.WeqromMath.calculate === 'function';
    byId('send-message').disabled = busy || (!mathAvailable && model.state !== 'ready') || !byId('chat-input').value.trim();
    byId('chat-input').disabled = busy;
    byId('clear-history').disabled = busy || history.length === 0;
    byId('stop-generation').hidden = !busy || active.kind === 'math';
    byId('stop-generation').disabled = busy && active.stopping;
    byId('stop-generation').textContent = busy && active.stopping ? 'Остановка…' : 'Остановить';
    byId('composer-hint').textContent = busy ? (active.kind === 'math' ? 'Офлайн-вычисление' : active.stopping ? 'Ожидаем остановки модели' : 'Ответ создаётся на телефоне') : mathAvailable ? 'Офлайн-вычисление · без модели' : model.state === 'ready' ? 'Короткий вопрос · до 1200 знаков' : model.state === 'error' ? 'Модель недоступна' : 'Модель готовится к работе';
  }
  function applyStatus(value) {
    var status;
    try { status = decode(value); }
    catch (error) { status = {state: 'error', error: 'Не удалось получить состояние модели.'}; }
    if (['loading', 'ready', 'error'].indexOf(status.state) < 0) return;
    model = {state: status.state, model: limitedText(status.model, 100) || 'Weqrom Seed', parameters: status.parameters || null};
    var chip = byId('model-status'); chip.className = 'status-chip ' + model.state;
    byId('model-status-text').textContent = model.state === 'ready' ? 'Модель готова' : model.state === 'error' ? 'Модель недоступна' : 'Загрузка модели…';
    byId('model-error').hidden = model.state !== 'error';
    byId('model-error').textContent = model.state === 'error' ? limitedText(status.error || status.message, 600) || 'Не удалось загрузить модель. Закройте и снова откройте приложение.' : '';
    if (statusTimer) { window.clearTimeout(statusTimer); statusTimer = null; }
    if (model.state === 'loading' && !document.hidden) statusTimer = window.setTimeout(refreshStatus, 700);
    updateComposer();
  }
  function refreshStatus() {
    var bridge = nativeBridge();
    if (!bridge || typeof bridge.status !== 'function') {
      applyStatus({state: 'error', error: 'Откройте APK Weqrom AI: локальная модель запускается внутри приложения. Математика и конструктор доступны отдельно.'});
      return;
    }
    try { applyStatus(bridge.status()); }
    catch (error) { applyStatus({state: 'error', error: 'Не удалось связаться с локальной моделью.'}); }
  }
  function finalize(event) {
    if (!active || event.id !== active.id) return;
    var current = active;
    if (['eos', 'length', 'cancelled'].indexOf(event.finishReason) >= 0) current.message.finishReason = event.finishReason;
    if (typeof event.text === 'string' && !(event.cancelled && !event.text && current.message.text)) current.message.text = event.text.slice(0, MAX_TEXT);
    if (event.type === 'error') {
      current.message.status = 'error';
      current.message.error = limitedText(event.message || event.error, 600) || 'Не удалось получить ответ от модели.';
    } else if (!event.cancelled && !current.message.text.trim()) {
      current.message.status = 'error';
      current.message.error = 'Модель завершила ответ без текста. Попробуйте короткий вопрос.';
    } else current.message.status = event.cancelled ? 'cancelled' : 'complete';
    active = null;
    if (renderFrame !== null) { window.cancelAnimationFrame(renderFrame); renderFrame = null; }
    trimHistory(); saveHistory(); renderMessages(); scrollToMessage();
    announce(current.message.status === 'cancelled' ? 'Ответ остановлен' : current.message.status === 'error' ? current.message.error : current.message.finishReason === 'length' ? 'Достигнут лимит длины ответа' : 'Ответ готов');
  }
  function handleEvent(raw) {
    var event;
    try { event = decode(raw); } catch (error) { return; }
    if (event.type === 'state' && !event.id) { applyStatus(event); return; }
    if (!active || event.id !== active.id) return;
    if (event.type === 'partial' && typeof event.text === 'string') {
      active.message.text = event.text.slice(0, MAX_TEXT); queueStreamRender();
    } else if (event.type === 'done' || event.type === 'error') finalize(event);
  }
  function sendMessage() {
    var bridge = nativeBridge();
    var input = byId('chat-input');
    var text = input.value.trim();
    if (active || !text) return;
    var mathRequest = classifyMath(text);
    if (mathRequest && window.WeqromMath && typeof window.WeqromMath.calculate === 'function') {
      sendMath(text, mathRequest); return;
    }
    if (model.state !== 'ready' || !bridge || typeof bridge.generate !== 'function') return;
    var requestId = id('answer');
    history.push({id: id('user'), role: 'user', text: text.slice(0, 1200), status: 'complete'});
    var messages = contextMessages();
    var assistant = {id: requestId, role: 'assistant', text: '', status: 'generating'};
    history.push(assistant); trimHistory();
    active = {id: requestId, message: assistant, stopping: false, kind: 'model'};
    input.value = ''; saveHistory(); renderMessages(); scrollToMessage();
    // The larger allowance is only for an explicit code request; it does not
    // turn this narrow experimental model into the deterministic game builder.
    var codeRequest = /код|программ|(?:напиши|создай|сделай|покажи|write|create|generate).{0,80}(?:\bhtml\b|\bjavascript\b|\bpython\b|\bcode\b|игр)/i.test(text);
    try { bridge.generate(JSON.stringify({id: requestId, messages: messages, maxTokens: codeRequest ? 512 : 256})); }
    catch (error) { finalize({id: requestId, type: 'error', message: 'Не удалось запустить ответ. Попробуйте снова.'}); }
  }
  function classifyMath(text) {
    if (!text || text.length > 1200) return null;
    var expression = text.trim(); var mode = 'calculate'; var explicit = false; var variable = 'x';
    var derivative = /^найди\s+производную\s*:?\s*(?:от\s+)?(.+?)(?:\s+по\s+([a-df-hj-z]))?\s*[?？]*$/i.exec(expression);
    var equation = /^реши\s+уравнение\s*:?\s*(.+)$/i.exec(expression);
    var arithmetic = /^(?:вычисли|посчитай|сколько\s+будет)\s*:?\s*(.+)$/i.exec(expression);
    if (derivative) { explicit = true; mode = 'derivative'; expression = derivative[1]; variable = derivative[2] || 'x'; }
    else if (equation) { explicit = true; mode = 'equation'; expression = equation[1]; }
    else if (arithmetic) { explicit = true; expression = arithmetic[1]; }
    expression = expression.replace(/[?？]+\s*$/, '').trim();
    expression = expression.replace(/плюс/gi, '+').replace(/минус/gi, '-');
    // Only a mathematical alphabet reaches the tool. HTML, code and ordinary
    // questions retain the real model route. The worker then validates syntax,
    // domain and bounds; unsupported recognized maths never uses a model guess.
    if (!/^[0-9A-Za-z_+\-*/^().=,\s×·÷−–π²³]+$/.test(expression)) return null;
    if (!explicit && /\b(?:html|javascript|python|function|return|const|let|var|console|print|alert|import|class|def)\b/i.test(expression)) return null;
    if (!explicit && !/[0-9=+\-*/^()×·÷π²³]/.test(expression)) return null;
    if (!explicit) {
      var withoutNumbers = expression.replace(/(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?/g, '0');
      var ordinaryWords = withoutNumbers.match(/[A-Za-z_][A-Za-z_0-9]*/g) || [];
      var functions = ['sin','cos','tan','asin','acos','atan','sqrt','abs','log','exp','pi','e'];
      for (var word = 0; word < ordinaryWords.length; word += 1) {
        var name = ordinaryWords[word];
        if (name.length === 1 || functions.indexOf(name) >= 0) continue;
        if (!new RegExp('\\b' + name + '\\s*\\(').test(expression)) return null;
      }
    }
    if (mode !== 'derivative' && expression.indexOf('=') >= 0) mode = 'equation';
    if (mode !== 'derivative') {
      var names = expression.match(/[A-Za-z_][A-Za-z_0-9]*/g) || [];
      var candidates = names.filter(function (name) { return /^[a-df-hj-z]$/.test(name); });
      if (candidates.length && candidates.every(function (name) { return name === candidates[0]; })) variable = candidates[0];
    }
    return {mode: mode, expression: expression, variable: variable};
  }
  function sendMath(text, request) {
    contextNotice.hidden = true;
    var requestId = id('math');
    history.push({id: id('user'), role: 'user', text: text.slice(0, 1200), status: 'complete'});
    var answer = {id: requestId, role: 'assistant', source: 'math', text: '', status: 'generating', steps: []};
    history.push(answer); trimHistory(); active = {id: requestId, message: answer, stopping: false, kind: 'math'};
    byId('chat-input').value = ''; saveHistory(); renderMessages(); scrollToMessage();
    var computation;
    try { computation = window.WeqromMath.calculate(request); }
    catch (error) { computation = Promise.resolve({error: error.message || 'Вычисление не удалось.'}); }
    Promise.resolve(computation).then(function (result) {
      if (!active || active.id !== requestId) return;
      if (!result || result.error) {
        finalize({id: requestId, type: 'error', message: result && result.error || 'Нет результата вычисления.'}); return;
      }
      answer.steps = Array.isArray(result.steps) ? result.steps.slice(0, 12).map(function (step) { return String(step).slice(0, 1000); }) : [];
      finalize({id: requestId, type: 'done', text: limitedText(result.text, MAX_TEXT)});
    }, function (error) {
      finalize({id: requestId, type: 'error', message: limitedText(error && error.message, 600) || 'Вычисление не удалось.'});
    });
  }
  function stopGeneration() {
    if (!active || active.stopping) return;
    active.stopping = true; active.message.status = 'stopping'; updateComposer();
    try {
      var bridge = nativeBridge();
      if (!bridge || typeof bridge.cancel !== 'function') throw new Error('Cancel unavailable');
      bridge.cancel();
    } catch (error) {
      active.stopping = false; active.message.status = 'generating'; updateComposer();
      toast('Не удалось передать остановку модели');
    }
  }
  function openModal(title, paragraphs, actions) {
    modalReturnFocus = document.activeElement;
    byId('modal-title').textContent = title;
    var content = byId('modal-content'); content.replaceChildren();
    paragraphs.forEach(function (value) { var paragraph = document.createElement('p'); paragraph.textContent = value; content.appendChild(paragraph); });
    var footer = byId('modal-actions'); footer.replaceChildren();
    actions.forEach(function (action) {
      var button = makeButton(action.label, action.run);
      button.className = action.primary ? 'primary-button' : 'secondary-button'; footer.appendChild(button);
    });
    byId('modal-backdrop').hidden = false;
    var first = footer.querySelector('button'); if (first) first.focus();
  }
  function closeModal() {
    byId('modal-backdrop').hidden = true;
    if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') modalReturnFocus.focus();
  }
  function switchTab(name) {
    ['chat', 'math', 'games'].forEach(function (value) {
      byId('panel-' + value).hidden = value !== name;
      byId('tab-' + value).classList.toggle('selected', value === name);
      byId('tab-' + value).setAttribute('aria-selected', String(value === name));
    });
    window.scrollTo({top: 0, behavior: 'auto'});
  }
  function updateMathMode() {
    var mode = byId('math-mode').value;
    byId('math-variable-field').hidden = mode === 'calculate';
    byId('math-run').firstChild.textContent = mode === 'equation' ? 'Решить уравнение ' : mode === 'derivative' ? 'Найти производную ' : 'Вычислить ';
    byId('math-input').placeholder = mode === 'equation' ? 'Например, x^2 - 5*x + 6 = 0' : mode === 'derivative' ? 'Например, x^3 + sin(x)' : 'Например, (12 + 8) / 4';
    var samples = mode === 'equation' ? ['2*x + 5 = 17', 'x^2 - 5*x + 6 = 0'] : mode === 'derivative' ? ['x^3 + sin(x)', 'exp(x)'] : ['(12 + 8) / 4', 'sqrt(144)', '2^10'];
    byId('math-examples').replaceChildren();
    samples.forEach(function (sample) {
      var button = makeButton(sample, function () { byId('math-input').value = sample; });
      button.dataset.math = sample; byId('math-examples').appendChild(button);
    });
  }
  function calculate() {
    var expression = byId('math-input').value.trim();
    if (!expression) { byId('math-input').focus(); return; }
    var run = ++mathRunId;
    var result = byId('math-result'); result.hidden = false; result.classList.remove('error');
    byId('math-answer').textContent = 'Считаем…'; byId('math-exact').hidden = true; byId('math-steps').hidden = true;
    byId('math-copy').disabled = true; byId('math-run').disabled = true;
    var tool = window.WeqromMath;
    var operation;
    try {
      if (!tool || typeof tool.calculate !== 'function') throw new Error('Вычислительный инструмент не загрузился.');
      operation = tool.calculate({mode: byId('math-mode').value, expression: expression, variable: byId('math-variable').value.trim() || 'x'});
    } catch (error) { operation = Promise.resolve({error: error.message || 'Вычисление недоступно.'}); }
    Promise.resolve(operation).then(function (answer) {
      if (run !== mathRunId) return;
      answer = answer || {error: 'Инструмент не вернул результат.'};
      result.classList.toggle('error', !!answer.error);
      byId('math-answer').textContent = limitedText(answer.error || answer.text, 12000) || 'Нет результата';
      byId('math-copy').disabled = !!answer.error;
      byId('math-exact').hidden = !answer.exact || answer.exact === answer.text;
      byId('math-exact').textContent = typeof answer.exact === 'string' ? 'Точный вид: ' + answer.exact.slice(0, 3000) : answer.exact === true ? 'Точный результат' : '';
      var steps = byId('math-steps'); steps.replaceChildren();
      if (Array.isArray(answer.steps)) answer.steps.slice(0, 20).forEach(function (step) {
        var item = document.createElement('li'); item.textContent = String(step).slice(0, 1500); steps.appendChild(item);
      });
      steps.hidden = !steps.childElementCount;
    }, function (error) {
      if (run !== mathRunId) return;
      result.classList.add('error'); byId('math-answer').textContent = limitedText(error && error.message, 600) || 'Не удалось выполнить вычисление.';
    }).finally(function () { if (run === mathRunId) byId('math-run').disabled = false; });
  }
  function gameCodeSize() {
    var count = byId('game-code').value.length;
    byId('game-code-size').textContent = Math.max(1, Math.round(count / 1024)) + ' КБ текста';
  }
  function buildGame() {
    byId('game-error').hidden = true;
    try {
      if (!window.WeqromGames || typeof window.WeqromGames.build !== 'function') throw new Error('Конструктор не загрузился.');
      var title = byId('game-title').value.trim() || 'Моя игра';
      var result = window.WeqromGames.build({genre: byId('game-genre').value, title: title, theme: byId('game-theme').value, difficulty: byId('game-difficulty').value});
      if (!result || typeof result.html !== 'string') throw new Error('Конструктор не вернул HTML.');
      byId('game-code').value = result.html;
      byId('game-description').textContent = limitedText(result.description, 1000);
      byId('game-output-title').textContent = title;
      gameFilename = typeof result.filename === 'string' ? result.filename : 'weqrom-game.html';
      byId('game-output').hidden = false; gameCodeSize();
      announce('Игра собрана. Доступны запуск и сохранение HTML.');
    } catch (error) {
      byId('game-error').textContent = limitedText(error.message, 600) || 'Не удалось собрать игру.';
      byId('game-error').hidden = false;
    }
  }

  window.WeqromEvents = {onEvent: handleEvent};
  history = loadHistory(); renderMessages();
  ['chat', 'math', 'games'].forEach(function (name) { byId('tab-' + name).addEventListener('click', function () { switchTab(name); }); });
  byId('chat-input').addEventListener('input', updateComposer);
  byId('chat-input').addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); }
  });
  byId('send-message').addEventListener('click', sendMessage);
  byId('stop-generation').addEventListener('click', stopGeneration);
  document.querySelectorAll('[data-prompt]').forEach(function (button) {
    button.addEventListener('click', function () { byId('chat-input').value = button.dataset.prompt; updateComposer(); byId('chat-input').focus(); });
  });
  byId('clear-history').addEventListener('click', function () {
    if (active) return;
    openModal('Очистить историю?', ['Все сообщения этого чата будут удалены с телефона.'], [
      {label: 'Отмена', run: closeModal},
      {label: 'Очистить', primary: true, run: function () { history = []; contextNotice.hidden = true; saveHistory(); renderMessages(); closeModal(); announce('История очищена'); }}
    ]);
  });
  byId('about-open').addEventListener('click', function () {
    var parameterLabel = typeof model.parameters === 'number' ? new Intl.NumberFormat('ru-RU').format(model.parameters) + ' параметров.' : 'Небольшая экспериментальная модель.';
    openModal('Weqrom Seed', [
      'Weqrom Seed · обучена с нуля. ' + parameterLabel,
      'Модель работает на телефоне и предназначена для коротких простых ответов. Она может ошибаться, повторяться и не справляться со сложными запросами.',
      'Математика — отдельный вычислительный инструмент. Игры собираются конструктором HTML-шаблонов. Их результаты не выдаются за ответы нейросети.',
      'Модель входит в APK и загружается автоматически. Чат сохраняется локально; в контекст ответа передаётся только недавняя часть истории.'
    ], [{label: 'Понятно', primary: true, run: closeModal}]);
  });
  byId('modal-backdrop').addEventListener('click', function (event) { if (event.target === byId('modal-backdrop')) closeModal(); });
  document.addEventListener('keydown', function (event) {
    if (byId('modal-backdrop').hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeModal(); }
    if (event.key === 'Tab') {
      var buttons = byId('modal').querySelectorAll('button');
      if (!buttons.length) return;
      var first = buttons[0]; var last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  byId('math-mode').addEventListener('change', updateMathMode);
  byId('math-run').addEventListener('click', calculate);
  byId('math-input').addEventListener('keydown', function (event) { if (event.key === 'Enter') calculate(); });
  byId('math-copy').addEventListener('click', function () { copyText(byId('math-answer').textContent); });
  updateMathMode();
  byId('game-build').addEventListener('click', buildGame);
  byId('game-code').addEventListener('input', gameCodeSize);
  byId('game-preview').addEventListener('click', function () { previewHtml(byId('game-code').value); });
  byId('game-export').addEventListener('click', function () { exportHtml(byId('game-code').value, gameFilename); });
  byId('game-copy').addEventListener('click', function () { copyText(byId('game-code').value); });
  if (window.WeqromGames && Array.isArray(window.WeqromGames.presets)) {
    byId('game-genre').replaceChildren();
    window.WeqromGames.presets.forEach(function (preset) {
      var option = document.createElement('option'); option.value = preset.id; option.textContent = preset.label; byId('game-genre').appendChild(option);
    });
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden && statusTimer) { window.clearTimeout(statusTimer); statusTimer = null; }
    else if (!document.hidden && model.state === 'loading') refreshStatus();
  });
  window.addEventListener('pagehide', saveHistory);
  refreshStatus();
}());
