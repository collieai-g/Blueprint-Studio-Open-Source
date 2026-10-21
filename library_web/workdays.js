'use strict';

(() => {
  const TYPES = { outcome: '成果', decision: '決定', reference: '參考', todo: '待辦' };
  const TOP_FIELDS = new Set(['date', 'source', 'source_key', 'title', 'summary', 'entries']);
  const ENTRY_FIELDS = new Set(['type', 'title', 'content', 'url', 'tags', 'due_date']);
  const REVISION_ENTRY_FIELDS = new Set([...ENTRY_FIELDS, 'id']);
  const REVISION_CONFLICT = '這份紀錄已有更新，沒有覆寫，草稿仍然保留。按「重新讀取最新紀錄」會以伺服器最新內容取代目前草稿，請重新檢查後再保存。';
  const state = {
    view: '', offset: 0, total: 0, request: 0, dashboardRequest: 0, reportRequest: 0,
    items: [], dashboardTodos: [], report: null, draft: null, draftReady: false,
    saving: false, reloading: false, pending: new Set(), instructions: null, instructionsDate: '', fileRequest: 0,
    mode: 'import', revision: null, importBackup: null, historyRequest: 0, instructionsGeneration: 0, skipCloseCapture: false
  };
  let deps;
  let searchTimer;
  const $ = (id) => document.getElementById(id);
  const node = (tag, className, text) => deps.element(tag, className, text);
  const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const message = (error) => error?.message || '未能完成，請稍後再試。';

  function errorAt(id, error) {
    const target = $(id);
    target.textContent = error ? message(error) : '';
    target.hidden = !error;
  }
  function localDate() {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  }
  function validDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [year, month, day] = value.split('-').map(Number);
    if (year < 1 || year > 9999) return false;
    const date = new Date(0);
    date.setFullYear(year, month - 1, day);
    date.setHours(12, 0, 0, 0);
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
  }
  function safeUrl(value) {
    if (!value) return '';
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
    catch { return ''; }
  }
  function button(text, action, className = 'button secondary') {
    const result = node('button', className, text);
    result.type = 'button';
    result.addEventListener('click', action);
    return result;
  }
  function unknownFields(value, fields, label) {
    const unknown = Object.keys(value).filter((key) => !fields.has(key));
    if (unknown.length) throw new Error(`${label}有未支援的欄位「${unknown.join('、')}」。請移除後再預覽，或使用下載範本。`);
  }
  function textValue(value, label, max, required = false) {
    if (value === undefined && !required) return '';
    if (typeof value !== 'string') throw new Error(`${label}需要填寫文字。`);
    if (required && !value.trim()) throw new Error(`請填寫${label}。`);
    if (value.length > max) throw new Error(`${label}最多可填寫 ${max} 個字元。`);
    return value;
  }
  function checkPayloadSize(payload) {
    if (new Blob([JSON.stringify(payload)]).size > 512 * 1024) throw new Error('這份紀錄的 JSON 超過 512 KiB，請刪減內容或分開保存；中文等文字會佔用較多儲存空間。');
  }
  function validateDraft(value, revision = false) {
    if (!isObject(value)) throw new Error('請貼上包含日期、標題及項目的 JSON 物件。');
    unknownFields(value, TOP_FIELDS, '工作紀錄');
    if (!validDate(value.date)) throw new Error('日期需要使用有效的 YYYY-MM-DD 格式。');
    const source = textValue(value.source === undefined ? 'AI' : value.source, '來源', 80, true).trim();
    const sourceKey = textValue(value.source_key, '來源識別碼', 200, true).trim();
    const title = textValue(value.title, '工作紀錄標題', 240).trim();
    const summary = textValue(value.summary, '摘要', 8000);
    if (!Array.isArray(value.entries) || !value.entries.length) throw new Error('請加入至少一個成果、決定、參考或待辦。');
    if (value.entries.length > 80) throw new Error('每份工作紀錄最多可加入 80 個項目，請分開匯入。');
    const entries = value.entries.map((entry, index) => {
      const label = `第 ${index + 1} 項`;
      if (!isObject(entry)) throw new Error(`${label}需要是一個 JSON 物件。`);
      unknownFields(entry, revision ? REVISION_ENTRY_FIELDS : ENTRY_FIELDS, label);
      if (!Object.hasOwn(TYPES, entry.type)) throw new Error(`${label}的類型需要是 outcome、decision、reference 或 todo。`);
      const entryTitle = textValue(entry.title, `${label}標題`, 240, true).trim();
      const content = textValue(entry.content, `${label}內容`, 8000);
      const url = textValue(entry.url, `${label}網址`, 8192).trim();
      if (url && !safeUrl(url)) throw new Error(`${label}的網址需要以 https:// 或 http:// 開頭。`);
      const tags = entry.tags === undefined ? [] : entry.tags;
      if (!Array.isArray(tags) || tags.length > 16 || tags.some((tag) => typeof tag !== 'string' || !tag.trim() || tag.length > 100)) {
        throw new Error(`${label}的標籤需要是文字陣列，最多 16 個，每個最多 100 字元，不能留空。`);
      }
      const result = { type: entry.type, title: entryTitle, content, url, tags: [...new Set(tags.map((tag) => tag.trim()))] };
      if (revision && entry.id !== undefined) result.id = textValue(entry.id, `${label}識別碼`, 200, true);
      if (entry.due_date !== undefined && entry.due_date !== null && entry.due_date !== '') {
        if (entry.type !== 'todo') throw new Error(`${label}只有待辦可以填寫到期日。`);
        if (!validDate(entry.due_date)) throw new Error(`${label}的到期日需要使用有效的 YYYY-MM-DD 格式。`);
        result.due_date = entry.due_date;
      }
      return result;
    });
    const payload = { date: value.date, source, source_key: sourceKey, title, summary, entries };
    const normalized = { ...payload, title: title || `${value.date} 工作紀錄`, entries: entries.map(({ id, ...entry }) => ({ ...entry, due_date: entry.due_date || null })) };
    if ([...JSON.stringify(normalized)].length > 200000) throw new Error('每份工作紀錄最多 200,000 字元，請刪減內容或分開保存。');
    if (!revision) checkPayloadSize(payload);
    return payload;
  }
  function parseDraft(text) {
    if (typeof text !== 'string' || text.length > 1024 * 1024) throw new Error('JSON 內容過大，請將工作紀錄分開匯入。');
    let json = text.trim();
    if (/^```(?:json)?\s*\n/i.test(json) && /\n```\s*$/.test(json)) json = json.replace(/^```(?:json)?\s*\n/i, '').replace(/\n```\s*$/, '').trim();
    let value;
    try { value = JSON.parse(json); }
    catch { throw new Error('JSON 格式有誤。請只貼上完整 JSON，移除前後解說文字，再試一次。'); }
    return validateDraft(value);
  }
  function freshDraft() {
    const date = localDate();
    const suffix = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return { date, source: '自行整理', source_key: `manual-${date}-${suffix}`, title: '', summary: '', entries: [{ type: 'outcome', title: '', content: '', url: '', tags: [] }] };
  }
  function field(label, control) {
    const wrapper = node('label', 'work-entry-field');
    wrapper.append(node('span', '', label), control);
    return wrapper;
  }
  function input(type, value, key, max) {
    const result = node('input');
    result.type = type;
    result.value = value || '';
    result.dataset.draftField = key;
    if (max) result.maxLength = max;
    return result;
  }
  function renderDraftEntries() {
    const container = $('draft-entries');
    container.replaceChildren();
    state.draft.entries.forEach((entry, index) => {
      const card = node('fieldset', 'work-draft-entry');
      card.dataset.entryIndex = String(index);
      if (state.mode === 'revision' && entry.id) card.dataset.entryId = entry.id;
      card.append(node('legend', '', `項目 ${index + 1}`));
      const type = node('select'); type.dataset.draftField = 'type';
      Object.entries(TYPES).forEach(([value, label]) => { const choice = node('option', '', label); choice.value = value; type.append(choice); });
      type.value = entry.type;
      const title = input('text', entry.title, 'title', 240); title.required = true;
      const content = node('textarea'); content.value = entry.content || ''; content.maxLength = 8000; content.rows = 3; content.dataset.draftField = 'content';
      const url = input('url', entry.url, 'url', 8192); url.placeholder = 'https://';
      const tags = node('textarea'); tags.value = (entry.tags || []).join('\n'); tags.rows = 2; tags.dataset.draftField = 'tags'; tags.placeholder = '每行一個標籤，最多 16 個';
      const due = input('date', entry.due_date, 'due_date');
      const dueField = field('到期日（選填）', due); dueField.hidden = entry.type !== 'todo';
      type.addEventListener('change', () => { dueField.hidden = type.value !== 'todo'; if (type.value !== 'todo') due.value = ''; });
      const remove = button('移除此項', () => {
        captureDraft();
        state.draft.entries.splice(index, 1);
        renderDraftEntries();
        const next = $('draft-entries').querySelectorAll('fieldset')[Math.min(index, state.draft.entries.length - 1)];
        (next?.querySelector('select') || $('draft-add')).focus();
      }, 'button subtle');
      card.append(field('類型', type), field('標題', title), field('內容（選填）', content), field('網址（選填）', url), field('標籤（選填）', tags), dueField, remove);
      container.append(card);
    });
    $('draft-add').disabled = state.saving || state.draft.entries.length >= 80;
  }
  function captureDraft() {
    if (!state.draft) return null;
    state.draft = {
      date: $('draft-date').value, source: $('draft-source').value, source_key: $('draft-source-key').value,
      title: $('draft-title').value, summary: $('draft-summary').value,
      entries: [...$('draft-entries').querySelectorAll('[data-entry-index]')].map((card) => {
        const get = (key) => card.querySelector(`[data-draft-field="${key}"]`).value;
        const entry = { type: get('type'), title: get('title'), content: get('content'), url: get('url'), tags: get('tags').split(/\r?\n/).map((value) => value.trim()).filter(Boolean) };
        if (state.mode === 'revision' && card.dataset.entryId) entry.id = card.dataset.entryId;
        if (entry.type === 'todo' && get('due_date')) entry.due_date = get('due_date');
        return entry;
      })
    };
    return state.draft;
  }
  function editorChrome() {
    const revision = state.mode === 'revision';
    $('work-import-heading').textContent = revision ? '修整工作紀錄' : '今日收工';
    $('draft-date').readOnly = revision;
    $('draft-source').readOnly = revision;
    $('draft-source-key').readOnly = revision;
    $('draft-back').textContent = revision ? '取消修整' : '返回 JSON';
    $('draft-save').textContent = revision ? '保存修整' : '確認保存';
    $('draft-reload').hidden = true;
  }
  function showDraft(draft) {
    state.draft = draft;
    state.draftReady = true;
    $('draft-date').value = draft.date;
    $('draft-source').value = draft.source;
    $('draft-source-key').value = draft.source_key;
    $('draft-title').value = draft.title;
    $('draft-summary').value = draft.summary;
    $('work-import-start').hidden = true;
    $('work-preview-panel').hidden = false;
    editorChrome();
    renderDraftEntries();
    errorAt('work-import-error', null);
    $('work-import-status').textContent = state.mode === 'revision' ? '日期、來源及識別碼保持原樣。保存後會建立修整版本，原有待辦狀態會保留。' : '請檢查內容，儲存後才會加入公司工作紀錄。';
    if (state.mode === 'revision' && state.revision?.conflict) {
      errorAt('work-import-error', new Error(REVISION_CONFLICT));
      $('draft-reload').hidden = false;
    }
    $('draft-title').focus();
  }
  function openImport() {
    if ($('work-import-dialog').open && state.draftReady) captureDraft();
    errorAt('work-import-error', null);
    deps.openDialog('work-import-dialog');
    if (state.draftReady) {
      showDraft(state.draft);
    } else {
      $('work-import-start').hidden = false; $('work-preview-panel').hidden = true;
      $('work-json-input').focus();
    }
    if (state.mode === 'import') instructions().catch((error) => errorAt('work-import-error', error));
  }
  function reportDraft(report) {
    return {
      date: report.date, source: report.source, source_key: report.source_key, title: report.title || '', summary: report.summary || '',
      entries: report.entries.map((entry) => {
        const result = { id: entry.id, type: entry.type, title: entry.title, content: entry.content || '', url: entry.url || '', tags: [...(entry.tags || [])] };
        if (entry.type === 'todo' && entry.due_date) result.due_date = entry.due_date;
        return result;
      })
    };
  }
  function restoreImport() {
    const backup = state.importBackup;
    state.mode = 'import'; state.revision = null; state.importBackup = null;
    state.draft = backup?.draft || null; state.draftReady = !!backup?.ready;
    $('work-json-input').value = backup?.json || '';
    editorChrome();
  }
  function beginRevision() {
    if (state.saving || state.reloading || !state.report) return;
    if (state.mode === 'revision') {
      if ($('work-report-dialog').open) $('work-report-dialog').close();
      deps.openDialog('work-import-dialog');
      showDraft(state.draft);
      if (state.revision.id !== state.report.id) $('work-import-status').textContent = '已恢復另一份未儲存的修整，請先完成或取消。';
      return;
    }
    if (!Number.isInteger(state.report.version) || state.report.version < 1) {
      errorAt('work-report-error', new Error('請重新讀取最新紀錄，再開始修整。'));
      $('work-report-reload').hidden = false;
      return;
    }
    state.importBackup = { draft: state.draft, ready: state.draftReady, json: $('work-json-input').value };
    state.mode = 'revision';
    state.revision = { id: state.report.id, version: state.report.version };
    if ($('work-report-dialog').open) $('work-report-dialog').close();
    deps.openDialog('work-import-dialog');
    showDraft(reportDraft(state.report));
  }
  async function cancelRevision() {
    if (state.saving || state.reloading) return;
    const id = state.revision?.id;
    restoreImport();
    state.skipCloseCapture = true;
    $('work-import-dialog').close();
    if (id) await openReport(id);
  }
  async function reloadRevision() {
    if (state.saving || state.reloading || !state.revision) return;
    captureDraft();
    const revision = state.revision;
    state.reloading = true;
    const controls = [...$('work-preview-form').elements];
    const disabled = controls.map((control) => control.disabled);
    controls.forEach((control) => { control.disabled = true; });
    errorAt('work-import-error', null);
    $('work-import-status').textContent = '正在重新讀取最新紀錄…';
    try {
      const data = await deps.api(`/api/workdays/${encodeURIComponent(revision.id)}`);
      if (!data.workday || !Array.isArray(data.workday.entries) || !Number.isInteger(data.workday.version)) throw new Error('未能取得可修整的最新紀錄，草稿仍然保留。');
      state.revision = { id: data.workday.id, version: data.workday.version };
      state.report = data.workday; resetHistory(); renderReport();
      showDraft(reportDraft(data.workday));
      $('work-import-status').textContent = '已讀取最新版本，取代先前草稿。請重新檢查後保存。';
    } catch (error) {
      errorAt('work-import-error', error);
      $('draft-reload').hidden = false;
      $('work-import-status').textContent = '未能重新讀取，原有草稿仍然保留。';
    } finally {
      state.reloading = false;
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
      $('draft-add').disabled = state.draft.entries.length >= 80;
      $('draft-title').focus();
    }
  }
  async function instructions() {
    const date = localDate();
    if (state.instructionsDate !== date) { invalidateInstructions(); state.instructionsDate = date; }
    if (!state.instructions) {
      const generation = state.instructionsGeneration;
      const pending = deps.api(`/api/workday-instructions?date=${date}`).then((data) => {
        if (generation !== state.instructionsGeneration) throw new Error('公司資料或日期已有更新，請重新取得整理指令。');
        if (typeof data.instruction !== 'string' || !isObject(data.format)) throw new Error('未能取得匯入指引，請重新載入後再試。');
        $('work-instruction-text').value = data.instruction;
        return data;
      }).catch((error) => { if (state.instructions === pending) state.instructions = null; throw error; });
      state.instructions = pending;
    }
    return state.instructions;
  }
  function invalidateInstructions() {
    state.instructions = null; state.instructionsDate = ''; ++state.instructionsGeneration;
    if ($('work-instruction-text')) $('work-instruction-text').value = '';
  }
  async function copyInstruction() {
    const control = $('copy-work-instruction');
    control.disabled = true; errorAt('work-import-error', null);
    try {
      const data = await instructions();
      let copied = false;
      try {
        if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(data.instruction); copied = true; }
      } catch { /* A visible, selectable copy remains available below. */ }
      if (!copied) {
        const text = $('work-instruction-text');
        text.closest('details').open = true; text.focus(); text.select();
        try { copied = document.execCommand('copy'); } catch { /* Manual copy is available. */ }
        if (!copied) { $('work-import-status').textContent = '指令已展開及選取，請使用瀏覽器的複製功能，再貼到目前的 AI 對話。'; return; }
      }
      $('work-import-status').textContent = '已複製。請貼到你正在使用的 AI 對話，整理後將 JSON 貼回這裡。';
    } catch (error) { errorAt('work-import-error', error); }
    finally { control.disabled = false; }
  }
  async function downloadTemplate() {
    const control = $('download-work-template'); control.disabled = true; errorAt('work-import-error', null);
    try {
      const data = await instructions();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data.format, null, 2) + '\n'], { type: 'application/json;charset=utf-8' }));
      const link = node('a'); link.href = url; link.download = 'blueprint-workday-template.json'; link.hidden = true;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('work-import-status').textContent = '已下載 JSON 範本。填寫實際工作內容，再貼上或選取檔案。';
    } catch (error) { errorAt('work-import-error', error); }
    finally { control.disabled = false; }
  }
  async function readFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const request = ++state.fileRequest;
    errorAt('work-import-error', null);
    try {
      if (file.size > 1024 * 1024) throw new Error('JSON 檔案最多 1 MB，請分開匯入。');
      const text = await file.text();
      if (request !== state.fileRequest) return;
      $('work-json-input').value = text;
      $('work-import-status').textContent = '已讀取檔案，請按「預覽」檢查內容。';
      $('work-preview').focus();
    } catch (error) { if (request === state.fileRequest) errorAt('work-import-error', error); }
    finally { if (request === state.fileRequest) event.target.value = ''; }
  }
  async function refreshRelated() {
    const tasks = [refreshDashboard()];
    if (['workdays', 'todos'].includes(state.view)) tasks.push(loadList());
    if (deps.refreshInfo) tasks.push(Promise.resolve().then(deps.refreshInfo));
    if (deps.refreshLibrary) tasks.push(Promise.resolve().then(deps.refreshLibrary));
    await Promise.allSettled(tasks);
  }
  async function saveDraft(event) {
    event.preventDefault();
    if (state.saving || state.reloading) return;
    errorAt('work-import-error', null);
    const revision = state.mode === 'revision' ? { ...state.revision } : null;
    let payload;
    try {
      const draft = validateDraft(captureDraft(), !!revision);
      payload = revision ? { expected_version: revision.version, title: draft.title, summary: draft.summary, entries: draft.entries } : draft;
      checkPayloadSize(payload);
    }
    catch (error) { errorAt('work-import-error', error); return; }
    state.saving = true;
    const controls = [...$('work-preview-form').elements];
    const disabled = controls.map((control) => control.disabled);
    controls.forEach((control) => { control.disabled = true; });
    $('work-import-status').textContent = '正在儲存工作紀錄…';
    try {
      const data = await deps.api(revision ? `/api/workdays/${encodeURIComponent(revision.id)}` : '/api/workdays', { method: revision ? 'PATCH' : 'POST', body: payload });
      if (!data.workday?.id || !Array.isArray(data.workday.entries)) throw new Error('未能確認儲存結果，草稿仍然保留。請重新讀取紀錄後再試。');
      if (revision) restoreImport();
      else {
        state.draft = null; state.draftReady = false;
        $('work-json-input').value = '';
      }
      state.skipCloseCapture = true;
      $('work-import-dialog').close();
      deps.notice(revision ? (data.updated === false ? '內容沒有改動，已保留目前版本。' : '已保存修整，版本紀錄已更新。') : data.duplicate ? '這份工作紀錄已存在，沒有重複加入。' : '已儲存工作紀錄。');
      await refreshRelated();
      if (data.workday?.id) await openReport(data.workday.id);
    } catch (error) {
      errorAt('work-import-error', error.status === 409 ? new Error(revision ? REVISION_CONFLICT : '這個來源識別碼已有另一份內容。草稿已保留；請先查看原有工作紀錄，確認是另一份工作後才使用新的識別碼。') : error);
      if (revision && error.status === 409) { state.revision.conflict = true; $('draft-reload').hidden = false; }
      $('work-import-status').textContent = '尚未儲存，草稿仍然保留。';
    } finally {
      state.saving = false;
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
      $('draft-add').disabled = !!state.draft && state.draft.entries.length >= 80;
      if ($('work-report-dialog').open && state.report) $('work-report-edit').disabled = !Number.isInteger(state.report.version) || state.pending.size > 0 || state.reloading;
    }
  }
  function statsLine(workday) {
    const counts = workday.counts || {};
    return Object.entries(TYPES).map(([key, label]) => `${label} ${Math.max(0, Number(counts[key]) || 0)}`).join(' · ');
  }
  function workdayCard(workday, compact = false) {
    const card = node('article', compact ? 'work-card compact' : 'work-card');
    const open = button(workday.title || '工作紀錄', () => openReport(workday.id), 'work-title-button');
    card.append(node('p', 'work-meta', `${workday.date || ''} · ${workday.source || 'AI'}`), open);
    if (workday.summary && !compact) card.append(node('p', 'work-summary', workday.summary));
    card.append(node('p', 'work-meta', statsLine(workday)));
    if (Number(workday.open_todos) > 0) card.append(node('span', 'work-open-count', `${workday.open_todos} 項待辦未完成`));
    return card;
  }
  function todoKey(todo, context) { return `work-todo-${context}-${todo.id}`; }
  function todoRow(todo, context, compact = false) {
    const row = node('article', compact ? 'work-todo compact' : 'work-todo');
    row.classList.toggle('is-done', !!todo.done);
    const label = node('label', 'work-todo-label');
    const check = node('input'); check.type = 'checkbox'; check.id = todoKey(todo, context); check.checked = !!todo.done; check.disabled = state.pending.has(todo.id);
    label.append(check, node('span', 'work-todo-title', todo.title)); row.append(label);
    check.addEventListener('change', () => toggleTodo(todo, check, row, context));
    const detail = node('div', 'work-todo-detail');
    const metadata = [todo.date, todo.due_date ? `到期：${todo.due_date}` : ''].filter(Boolean).join(' · ');
    if (metadata) detail.append(node('p', 'work-meta', metadata));
    if (!compact && todo.content) detail.append(node('p', 'work-entry-content', todo.content));
    if (todo.workday_id && context !== 'report') detail.append(button(todo.workday_title || '查看工作紀錄', () => openReport(todo.workday_id), 'work-inline-button'));
    row.append(detail);
    return row;
  }
  function replaceTodo(todo) {
    const replace = (items) => items.map((item) => item.id === todo.id ? { ...item, ...todo } : item);
    if (state.view === 'todos') state.items = replace(state.items);
    state.dashboardTodos = replace(state.dashboardTodos);
    if (state.report?.entries) state.report.entries = replace(state.report.entries);
  }
  async function toggleTodo(todo, checkbox, row, context) {
    if (state.pending.has(todo.id)) { checkbox.checked = !!todo.done; return; }
    const done = checkbox.checked;
    const focused = document.activeElement === checkbox;
    let changed = false;
    state.pending.add(todo.id); checkbox.disabled = true;
    row.querySelector('.work-todo-error')?.remove();
    try {
      const data = await deps.api(`/api/todos/${encodeURIComponent(todo.id)}`, { method: 'PATCH', body: { expected_version: todo.version, done } });
      if (!data.todo) throw new Error('未能確認待辦更新，請重新載入。');
      replaceTodo(data.todo);
      const reportId = state.report?.entries.some((entry) => entry.id === todo.id) ? state.report.id : null;
      if (reportId) renderReport();
      await refreshRelated();
      if (reportId && state.report?.id === reportId && $('work-report-dialog').open) await openReport(reportId, true);
      changed = true;
    } catch (error) {
      checkbox.checked = !!todo.done;
      const box = node('div', 'work-todo-error'); box.setAttribute('role', 'alert');
      if (error.status === 409) {
        box.append(node('p', 'error-text', '這項待辦已被其他地方更新。請重新載入，再決定是否修改。'));
        if (context === 'report' && state.report?.entries.some((entry) => entry.id === todo.id)) {
          $('work-report-reload').hidden = false;
          errorAt('work-report-error', new Error('待辦已有更新，請重新載入這份紀錄。'));
        } else box.append(button('重新載入', async () => {
          if (context === 'dashboard') await refreshDashboard(); else await loadList();
        }, 'button secondary small'));
      } else box.append(node('p', 'error-text', message(error)));
      row.append(box);
      if (!row.isConnected) deps.notice(error.status === 409 ? '這項待辦已有更新，請重新載入後再修改。' : message(error), true);
    } finally {
      state.pending.delete(todo.id); checkbox.disabled = false;
      if (state.report) $('work-report-edit').disabled = !Number.isInteger(state.report.version) || state.pending.size > 0 || state.saving || state.reloading;
      document.querySelectorAll('input[type="checkbox"]').forEach((control) => { if ([todoKey(todo, 'list'), todoKey(todo, 'dashboard'), todoKey(todo, 'report')].includes(control.id)) control.disabled = false; });
      const sameContext = context === 'list' ? state.view === 'todos' : context === 'dashboard' ? state.view === 'dashboard' : $('work-report-dialog').open && state.report?.entries.some((entry) => entry.id === todo.id);
      if (changed && focused && sameContext && (document.activeElement === checkbox || document.activeElement === document.body || !document.activeElement?.isConnected)) {
        let target = document.getElementById(todoKey(todo, context));
        if (!target) {
          const scope = context === 'dashboard' ? $('dashboard-todos') : context === 'report' ? $('work-report-entries') : $('work-list');
          target = scope.querySelector('input[type="checkbox"]');
          if (!target) {
            target = context === 'dashboard' ? $('open-todos-heading') : context === 'report' ? $('work-report-title') : $('work-panel-title');
            target.tabIndex = -1;
          }
        }
        target.focus();
      }
    }
  }
  async function loadList(background = false) {
    if (!['workdays', 'todos'].includes(state.view)) return;
    const view = state.view;
    const request = ++state.request;
    const limit = view === 'todos' ? 100 : 30;
    const params = new URLSearchParams({ q: $('work-search').value.trim(), date: $('work-date').value, limit: String(limit), offset: String(state.offset) });
    if (view === 'todos') params.set('done', $('todo-status').value);
    $('work-list').setAttribute('aria-busy', 'true');
    $('work-empty').hidden = true;
    $('work-prev').disabled = true; $('work-next').disabled = true;
    try {
      const data = await deps.api(`/api/${view}?${params}`);
      if (request !== state.request || state.view !== view) return;
      if (background === true && (document.querySelector('dialog[open]') || state.pending.size || state.saving || state.reloading)) return false;
      const items = view === 'todos' ? data.todos : data.workdays;
      if (!Array.isArray(items)) throw new Error('未能讀取工作清單，請重新載入。');
      state.items = items; state.total = Math.max(0, Number(data.total) || 0);
      $('work-list').replaceChildren(...items.map((item) => view === 'todos' ? todoRow(item, 'list') : workdayCard(item)));
      $('work-result-count').textContent = `共 ${state.total} ${view === 'todos' ? '項待辦' : '份紀錄'}`;
      const emptyText = $('work-search').value || $('work-date').value ? '找不到符合條件的內容，試試其他關鍵字或日期。' : view === 'todos' ? ($('todo-status').value === '0' ? '目前沒有未完成待辦。' : '目前沒有符合狀態的待辦。') : '在左側請 AI 整理並保存今天的成果，這裡會同步顯示。';
      ($('work-empty').querySelector('p') || $('work-empty')).textContent = emptyText;
      const emptyHeading = $('work-empty').querySelector('h3');
      if (emptyHeading) emptyHeading.textContent = view === 'todos' ? '下一步，清楚有序。' : '先留下今天值得保存的工作。';
      $('work-empty').hidden = items.length !== 0;
      $('work-page').textContent = state.total ? `${state.offset + 1}–${Math.min(state.offset + items.length, state.total)} / ${state.total}` : '0 / 0';
      $('work-prev').disabled = state.offset === 0;
      $('work-next').disabled = state.offset + items.length >= state.total;
      $('work-prev').hidden = state.offset === 0;
      $('work-next').hidden = state.offset + items.length >= state.total;
      return true;
    } catch (error) {
      if (request !== state.request || state.view !== view) return;
      $('work-list').replaceChildren(node('p', 'error-text', message(error)), button('重新載入', loadList));
      $('work-result-count').textContent = '未能載入'; $('work-page').textContent = '';
      $('work-empty').hidden = true;
      if (background === true) throw error;
    } finally { if (request === state.request) $('work-list').removeAttribute('aria-busy'); }
  }
  function setView(view) {
    clearTimeout(searchTimer); ++state.request; state.view = view;
    $('work-list').removeAttribute('aria-busy');
    if (!['workdays', 'todos'].includes(view)) return;
    state.offset = 0; state.items = [];
    $('work-search').value = ''; $('work-date').value = ''; $('todo-status').value = '0';
    $('todo-status-field').hidden = view !== 'todos';
    $('work-search').placeholder = view === 'todos' ? '搜尋待辦標題或內容' : '搜尋工作紀錄';
    $('work-list').replaceChildren(); $('work-result-count').textContent = '正在載入…';
    loadList();
  }
  async function refreshDashboard(background = false) {
    const request = ++state.dashboardRequest;
    const results = await Promise.allSettled([
      deps.api('/api/workdays?limit=3&offset=0'),
      deps.api('/api/todos?done=0&limit=4&offset=0')
    ]);
    if (request !== state.dashboardRequest) return;
    if (background === true && (document.querySelector('dialog[open]') || state.pending.size || state.saving || state.reloading)) return false;
    const targets = [$('dashboard-workdays'), $('dashboard-todos')];
    results.forEach((result, index) => {
      const target = targets[index];
      if (result.status === 'rejected') {
        target.replaceChildren(node('p', 'error-text', message(result.reason)), button('重新載入', refreshDashboard)); return;
      }
      const items = index === 0 ? result.value.workdays : result.value.todos;
      if (!Array.isArray(items)) { target.replaceChildren(node('p', 'error-text', '未能讀取內容，請重新載入。')); return; }
      if (index === 1) state.dashboardTodos = items;
      target.replaceChildren(...items.map((item) => index === 0 ? workdayCard(item, true) : todoRow(item, 'dashboard', true)));
      if (!items.length) target.append(node('p', 'muted', index === 0 ? '請左側 AI 保存今天的成果與下一步。' : '目前沒有未完成待辦。'));
    });
    if (background === true && results.some((result, index) => result.status === 'rejected' || !Array.isArray(index === 0 ? result.value.workdays : result.value.todos))) throw new Error('工作紀錄暫時未能同步。');
    return true;
  }
  function renderReport() {
    const report = state.report;
    if (!report) return;
    $('work-report-title').textContent = report.title || '工作紀錄';
    $('work-report-meta').textContent = [report.date, report.source || 'AI', Number.isInteger(report.version) ? `版本 ${report.version}` : ''].filter(Boolean).join(' · ');
    $('work-report-edit').disabled = !Number.isInteger(report.version) || state.pending.size > 0 || state.saving || state.reloading;
    $('work-report-history').disabled = false;
    $('work-report-summary').textContent = report.summary || '';
    $('work-report-summary').hidden = !report.summary;
    const container = $('work-report-entries'); container.replaceChildren();
    Object.entries(TYPES).forEach(([type, label]) => {
      const entries = (report.entries || []).filter((entry) => entry.type === type);
      if (!entries.length) return;
      const section = node('section', 'work-report-section'); section.append(node('h3', '', `${label} · ${entries.length}`));
      entries.forEach((entry) => {
        let card;
        if (type === 'todo') card = todoRow({ ...entry, date: report.date, workday_id: report.id }, 'report');
        else {
          card = node('article', 'work-report-entry'); card.append(node('h4', '', entry.title));
          if (entry.content) card.append(node('p', 'work-entry-content', entry.content));
        }
        const href = safeUrl(entry.url);
        if (href) { const link = node('a', 'work-reference-link', entry.url); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); }
        if (Array.isArray(entry.tags) && entry.tags.length) {
          const tags = node('div', 'tags'); entry.tags.filter((tag) => typeof tag === 'string').forEach((tag) => tags.append(node('span', 'tag', tag))); card.append(tags);
        }
        section.append(card);
      });
      container.append(section);
    });
  }
  function resetHistory() {
    ++state.historyRequest;
    $('work-report-history-list').hidden = true;
    $('work-report-history-list').replaceChildren();
    $('work-report-history').setAttribute('aria-expanded', 'false');
  }
  function historyTime(value) {
    if (typeof value !== 'string' || !value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-HK', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  async function toggleHistory() {
    if (!state.report) return;
    const target = $('work-report-history-list');
    if (!target.hidden) { resetHistory(); return; }
    const request = ++state.historyRequest;
    const reportId = state.report.id;
    target.hidden = false;
    $('work-report-history').setAttribute('aria-expanded', 'true');
    target.replaceChildren(node('p', 'muted', '正在讀取版本紀錄…'));
    try {
      const data = await deps.api(`/api/workdays/${encodeURIComponent(reportId)}/history`);
      if (request !== state.historyRequest || state.report?.id !== reportId || !$('work-report-dialog').open) return;
      if (!Array.isArray(data.versions)) throw new Error('未能讀取版本紀錄，請重新載入。');
      const actions = { create: '首次保存', update: '修整內容', todo: '更新待辦', legacy: '原有紀錄' };
      const cards = data.versions.map((version) => {
        const snapshot = isObject(version.snapshot) ? version.snapshot : {};
        const card = node('article', 'work-history-entry');
        card.append(node('h4', '', `版本 ${version.version}`));
        const date = historyTime(version.recorded_at);
        card.append(node('p', 'work-meta', [actions[version.action] || '內容更新', date, version.actor].filter((value) => typeof value === 'string' && value).join(' · ')));
        if (snapshot.title) card.append(node('p', 'work-history-title', snapshot.title));
        if (snapshot.summary) card.append(node('p', 'work-summary', snapshot.summary));
        const counts = Object.fromEntries(Object.keys(TYPES).map((type) => [type, (Array.isArray(snapshot.entries) ? snapshot.entries : []).filter((entry) => entry.type === type).length]));
        card.append(node('p', 'work-meta', statsLine({ counts })));
        return card;
      });
      target.replaceChildren(...cards);
      if (!cards.length) target.append(node('p', 'muted', '目前沒有版本紀錄。'));
    } catch (error) {
      if (request !== state.historyRequest || state.report?.id !== reportId) return;
      target.replaceChildren(node('p', 'error-text', message(error)), button('重新載入', () => { target.hidden = true; toggleHistory(); }));
    }
  }
  async function openReport(id, reload = false) {
    const request = ++state.reportRequest;
    const previous = state.report;
    resetHistory();
    $('work-report-edit').disabled = true; $('work-report-history').disabled = true;
    if (!reload) {
      state.report = null; $('work-report-title').textContent = '正在載入工作紀錄…';
      $('work-report-meta').textContent = ''; $('work-report-summary').textContent = ''; $('work-report-entries').replaceChildren();
      deps.openDialog('work-report-dialog');
    }
    errorAt('work-report-error', null); $('work-report-reload').hidden = true;
    $('work-report-entries').setAttribute('aria-busy', 'true');
    try {
      const data = await deps.api(`/api/workdays/${encodeURIComponent(id)}`);
      if (request !== state.reportRequest) return;
      if (!data.workday || !Array.isArray(data.workday.entries)) throw new Error('未能讀取工作紀錄，請重新載入。');
      state.report = data.workday; renderReport();
    } catch (error) {
      if (request !== state.reportRequest) return;
      if (reload) state.report = previous;
      errorAt('work-report-error', error); $('work-report-reload').hidden = false;
      $('work-report-reload').dataset.workdayId = id;
    } finally {
      if (request === state.reportRequest) {
        $('work-report-entries').removeAttribute('aria-busy');
        $('work-report-reload').dataset.workdayId = id;
      }
    }
  }
  function init(dependencies) {
    deps = dependencies;
    $('work-import').addEventListener('click', openImport);
    $('copy-work-instruction').addEventListener('click', copyInstruction);
    $('download-work-template').addEventListener('click', downloadTemplate);
    $('work-json-file').addEventListener('change', readFile);
    $('work-preview').addEventListener('click', () => {
      errorAt('work-import-error', null);
      try { showDraft(parseDraft($('work-json-input').value)); }
      catch (error) { errorAt('work-import-error', error); }
    });
    $('work-manual').addEventListener('click', () => showDraft(freshDraft()));
    $('draft-add').addEventListener('click', () => {
      captureDraft();
      if (state.draft.entries.length >= 80) return;
      state.draft.entries.push({ type: 'outcome', title: '', content: '', url: '', tags: [] });
      renderDraftEntries(); $('draft-entries').lastElementChild.querySelector('select').focus();
    });
    $('draft-back').addEventListener('click', () => {
      if (state.mode === 'revision') { cancelRevision(); return; }
      captureDraft(); state.draftReady = false;
      $('work-json-input').value = JSON.stringify(state.draft, null, 2);
      $('work-import-start').hidden = false; $('work-preview-panel').hidden = true;
      errorAt('work-import-error', null); $('work-import-status').textContent = '內容已放回文字欄，可修改後重新預覽。'; $('work-json-input').focus();
    });
    $('work-preview-form').addEventListener('submit', saveDraft);
    $('draft-reload').addEventListener('click', reloadRevision);
    $('work-report-edit').addEventListener('click', beginRevision);
    $('work-report-history').addEventListener('click', toggleHistory);
    $('work-search').addEventListener('input', () => {
      clearTimeout(searchTimer); ++state.request; state.offset = 0;
      searchTimer = setTimeout(loadList, 220);
    });
    ['work-date', 'todo-status'].forEach((id) => $(id).addEventListener('change', () => {
      clearTimeout(searchTimer); state.offset = 0; loadList();
    }));
    $('work-prev').addEventListener('click', () => { state.offset = Math.max(0, state.offset - (state.view === 'todos' ? 100 : 30)); loadList(); });
    $('work-next').addEventListener('click', () => { state.offset += state.view === 'todos' ? 100 : 30; loadList(); });
    $('work-report-reload').addEventListener('click', () => {
      const id = $('work-report-reload').dataset.workdayId || state.report?.id;
      if (id) openReport(id, true);
    });
    $('work-report-dialog').addEventListener('close', () => { ++state.reportRequest; ++state.historyRequest; });
    $('work-import-dialog').addEventListener('cancel', (event) => { if (state.saving || state.reloading) event.preventDefault(); });
    $('work-import-dialog').addEventListener('close', () => {
      if (state.skipCloseCapture) { state.skipCloseCapture = false; return; }
      if (!state.saving && !state.reloading && state.draftReady) captureDraft();
    });
  }

  window.BlueprintWorkdays = { init, setView, refreshDashboard, refreshCurrent: loadList, openImport, openReport, invalidateInstructions, isBusy: () => state.saving || state.reloading || state.pending.size > 0 };
})();
