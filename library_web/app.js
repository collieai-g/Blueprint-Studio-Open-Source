'use strict';

(() => {
  const CATEGORIES = {
    general: '一般資料', chat: '對話／客服', web: '網站／UI', finance: '金融／財務',
    document: '文件', calendar: '日程', automation: '自動化', research: '研究／學習'
  };
  const MAX_FILE_SIZE = 20 * 1024 * 1024;
  const PAGE_SIZE = 100;
  const $ = (id) => document.getElementById(id);
  const state = { kind: 'file', collections: [], offset: 0, total: 0, detail: null, request: 0, detailRequest: 0, detailSaved: '', busy: false };
  let searchTimer;

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, options = {}) {
    const request = { credentials: 'same-origin', ...options };
    if (options.body !== undefined) {
      request.headers = { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1', ...options.headers };
      request.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    let data;
    try { data = await response.json(); }
    catch { throw new ApiError(response.ok ? '伺服器回應格式不正確。' : `無法完成要求（HTTP ${response.status}）。`, response.status); }
    if (!response.ok) {
      const error = data.error;
      const message = typeof error === 'string' ? error : error && typeof error.message === 'string' ? error.message : typeof data.message === 'string' ? data.message : `無法完成要求（HTTP ${response.status}）。`;
      throw new ApiError(message, response.status);
    }
    return data;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function setError(id, error) {
    const node = $(id);
    node.textContent = error ? error.message || String(error) : '';
    node.hidden = !error;
  }

  function notice(message, error = false) {
    const node = $('notice');
    node.textContent = message;
    node.classList.toggle('error', error);
    node.hidden = !message;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
    } catch { return null; }
  }

  function externalLink(value, label) {
    const href = safeUrl(value);
    if (!href) return element('span', 'muted', '網址無法開啟');
    const link = element('a', '', label || value);
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  }

  function categoryKey(key) { return Object.hasOwn(CATEGORIES, key) ? key : 'general'; }
  function categoryName(key) { return CATEGORIES[categoryKey(key)]; }
  function itemTags(item) { return Array.isArray(item.tags) ? item.tags.filter((tag) => typeof tag === 'string') : []; }
  function dateLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-HK', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  function formatBytes(value) {
    const size = Number(value) || 0;
    return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : size >= 1024 ? `${(size / 1024).toFixed(1)} KB` : `${size} B`;
  }
  function collectionName(id) { return state.collections.find((collection) => collection.id === id)?.name || '未分配收藏集'; }
  function classificationSignal(signal) {
    const explanations = {
      'source:trusted-youtube-host': '網址來自 YouTube。',
      'source:webpage-mime': '內容格式為網頁。',
      'source:image-mime': '檔案格式為圖片。',
      'source:video-mime': '檔案格式為影片。',
      'source:code-mime': '檔案格式為程式碼。',
      'source:document-mime': '檔案格式為文件。',
      'source:text-mime': '檔案格式為文字文件。',
      'source:http-url': '已提供網站連結。',
      'category:source-default': '按資料格式選用預設分類。',
      'topic:ui': '名稱或內容提到介面設計（UI）。',
      'topic:ux': '名稱或內容提到使用者體驗（UX）。',
      'topic:web': '名稱或內容提到網站設計。',
      'topic:learning': '名稱或內容提到學習或教學。',
      'topic:research': '名稱或內容提到研究或參考資料。',
      'topic:finance': '名稱或內容提到金融或財務。',
      'topic:chat': '名稱或內容提到客服或對話。',
      'topic:calendar': '名稱或內容提到預約或行程。',
      'topic:automation': '名稱或內容提到自動化或工作流程。',
      'topic:ai': '名稱或內容提到人工智能。',
      'topic:design-host': '網址來自設計參考平台。'
    };
    if (Object.hasOwn(explanations, signal)) return explanations[signal];
    if (typeof signal === 'string' && /^source:extension:\.[a-z0-9]{1,12}$/.test(signal)) return `按檔名的 ${signal.slice('source:extension:'.length)} 副檔名辨認格式。`;
    return '按名稱、格式及已提供內容判斷。';
  }
  function option(select, value, label) { const node = element('option', '', label); node.value = value; select.append(node); }
  function populateCategories(id, auto = false, filter = false) {
    const select = $(id);
    select.replaceChildren();
    if (auto || filter) option(select, '', auto ? '自動分類（規則）' : '所有分類');
    Object.entries(CATEGORIES).forEach(([key, label]) => option(select, key, label));
  }

  function populateCollectionSelects() {
    ['collection-filter', 'link-collection', 'upload-collection', 'detail-collection'].forEach((id) => {
      const select = $(id);
      const previous = select.value;
      select.replaceChildren();
      option(select, '', id === 'collection-filter' ? '所有收藏集' : '未分配收藏集');
      state.collections.forEach((collection) => option(select, collection.id, collection.name));
      select.value = [...select.options].some((entry) => entry.value === previous) ? previous : '';
    });
  }

  async function loadCollections() {
    const data = await api('/api/collections');
    state.collections = Array.isArray(data.collections) ? data.collections : [];
    populateCollectionSelects();
  }

  function makeCard(item) {
    const card = element('article', 'item-card');
    const top = element('div', 'item-top');
    const image = element('img', 'item-icon');
    image.src = `/icons/${categoryKey(item.category)}.svg`;
    image.alt = categoryName(item.category);
    image.width = 49; image.height = 49;
    const group = element('div', 'item-title-group');
    const title = element('button', 'item-title', item.title || item.file_name || '未命名資料');
    title.type = 'button';
    title.addEventListener('click', () => openDetail(item.id));
    group.append(title);
    let source = item.kind === 'file' ? `${item.file_name || '檔案'} · ${formatBytes(item.size)}` : item.source_type === 'youtube' ? 'YouTube' : '網站連結';
    if (item.kind === 'link' && safeUrl(item.url)) source += ` · ${new URL(item.url).hostname}`;
    group.append(element('p', 'item-source', source));
    top.append(image, group);
    card.append(top, element('p', 'item-notes', item.notes || '尚未加入筆記'));
    const badges = element('div', 'badges');
    badges.append(element('span', 'badge', categoryName(item.category)));
    if (item.source_type === 'youtube') badges.append(element('span', 'badge source', 'YouTube'));
    const tags = itemTags(item).filter((tag) => tag !== categoryName(item.category));
    tags.slice(0, 5).forEach((tag) => badges.append(element('span', 'badge', tag)));
    if (tags.length > 5) badges.append(element('span', 'badge', `＋${tags.length - 5}`));
    card.append(badges);
    const bottom = element('div', 'card-bottom');
    bottom.append(element('span', '', collectionName(item.collection_id)), element('span', '', dateLabel(item.updated_at)));
    card.append(bottom);
    return card;
  }

  async function loadItems() {
    const request = ++state.request;
    const params = new URLSearchParams({ kind: state.kind, archived: $('archived-filter').checked ? '1' : '0', limit: String(PAGE_SIZE), offset: String(state.offset) });
    [['q', $('search').value.trim()], ['category', $('category-filter').value], ['collection_id', $('collection-filter').value], ['source_type', state.kind === 'link' ? $('source-filter').value : '']].forEach(([key, value]) => { if (value) params.set(key, value); });
    $('item-grid').setAttribute('aria-busy', 'true');
    try {
      const data = await api(`/api/items?${params}`);
      if (request !== state.request) return;
      const items = Array.isArray(data.items) ? data.items : [];
      state.total = Number(data.total) || 0;
      $('item-grid').replaceChildren(...items.map(makeCard));
      $('result-count').textContent = `共 ${state.total} 項${$('archived-filter').checked ? '已封存資料' : '資料'}`;
      $('empty-state').hidden = items.length > 0;
      const filtered = Boolean($('search').value.trim() || $('category-filter').value || $('collection-filter').value || (state.kind === 'link' && $('source-filter').value));
      $('empty-title').textContent = $('archived-filter').checked ? '暫無已封存資料' : filtered ? '未找到相關資料' : state.kind === 'file' ? '加入第一份文件' : '保存第一個參考連結';
      $('empty-copy').textContent = filtered ? '試試其他關鍵字，或調整分類與收藏集。' : $('archived-filter').checked ? '封存後的資料會保留在這裏，並可隨時還原。' : state.kind === 'file' ? '上傳文件後，系統會按檔名、格式及可讀取的文字加入分類與標籤。' : '收藏網站、UI 設計或 YouTube 連結，再加入你的觀察與筆記。';
      $('empty-add').hidden = filtered || $('archived-filter').checked;
      $('previous-page').hidden = state.offset === 0;
      $('next-page').hidden = state.offset + PAGE_SIZE >= state.total;
      $('page-label').textContent = state.total > PAGE_SIZE ? `${state.offset + 1}–${Math.min(state.offset + PAGE_SIZE, state.total)} / ${state.total}` : '';
    } catch (error) {
      if (request !== state.request) return;
      $('result-count').textContent = '讀取失敗';
      notice(error.message, true);
    } finally { if (request === state.request) $('item-grid').setAttribute('aria-busy', 'false'); }
  }

  function refresh(resetPage = true) { if (resetPage) state.offset = 0; return loadItems(); }
  async function refreshAfterWrite() { await loadCollections(); await refresh(); }

  function setKind(kind) {
    state.kind = kind;
    document.querySelectorAll('[data-kind]').forEach((tab) => {
      const active = tab.dataset.kind === kind;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    $('library-content').setAttribute('aria-labelledby', kind === 'file' ? 'files-tab' : 'links-tab');
    $('source-filter-label').hidden = kind !== 'link';
    $('add-item').textContent = $('empty-add').textContent = kind === 'file' ? '加入檔案' : '收藏連結';
    refresh();
  }

  function openDialog(id) {
    const dialog = $(id);
    if (!dialog.open) dialog.showModal();
    const first = dialog.querySelector('input:not([type=hidden]), textarea, select');
    first?.focus();
  }

  function openAdd() {
    notice('');
    if (state.kind === 'file') {
      $('upload-form').reset();
      $('upload-results').replaceChildren();
      setError('upload-error', null);
      $('upload-collection').value = $('collection-filter').value;
      openDialog('upload-dialog');
    } else {
      $('link-form').reset();
      setError('link-error', null);
      $('link-collection').value = $('collection-filter').value;
      $('link-category').value = '';
      openDialog('link-dialog');
    }
  }

  async function submitForm(form, errorId, operation) {
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach((button) => { button.disabled = true; });
    setError(errorId, null);
    try { await operation(); }
    catch (error) { setError(errorId, error); }
    finally { buttons.forEach((button) => { button.disabled = false; }); }
  }

  function splitTags(value) { return [...new Set(value.split(/[,，\n]/).map((tag) => tag.trim()).filter(Boolean))]; }

  $('collection-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, 'collection-error', async () => {
      const data = await api('/api/collections', { method: 'POST', body: { name: $('collection-name').value.trim(), description: $('collection-description').value.trim(), icon: $('collection-icon').value } });
      await loadCollections();
      if (data.collection?.id) $('collection-filter').value = data.collection.id;
      $('collection-dialog').close();
      await refresh();
      notice('收藏集已建立。');
    });
  });

  $('link-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, 'link-error', async () => {
      const url = safeUrl($('link-url').value.trim());
      if (!url) throw new Error('請輸入以 https:// 或 http:// 開頭的有效網址。');
      const body = { title: $('link-title').value.trim(), url, notes: $('link-notes').value, collection_id: $('link-collection').value || null };
      if ($('link-category').value) body.category = $('link-category').value;
      if ($('link-tags').value.trim()) body.tags = splitTags($('link-tags').value);
      await api('/api/items', { method: 'POST', body });
      $('link-dialog').close();
      await refreshAfterWrite();
      notice('連結已收藏，分類與標籤可在詳情內修改。');
    });
  });

  function fileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('無法讀取這個檔案。'));
      reader.onabort = () => reject(new Error('檔案讀取已中止。'));
      reader.readAsDataURL(file);
    });
  }

  $('upload-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const files = [...$('upload-files').files];
    if (!files.length) return;
    submitForm(event.currentTarget, 'upload-error', async () => {
      state.busy = true;
      $('upload-files').disabled = true;
      const collection = $('upload-collection').value || null;
      const notes = $('upload-notes').value;
      let succeeded = 0;
      try {
        for (const file of files) {
          const row = element('li', '', `${file.name} · 正在上傳…`);
          $('upload-results').append(row);
          try {
            if (file.size > MAX_FILE_SIZE) throw new Error('超過每個檔案 20 MB 的上限。');
            const content = await fileBase64(file);
            await api('/api/files', { method: 'POST', body: { name: file.name, mime: file.type || 'application/octet-stream', content_base64: content, collection_id: collection, notes } });
            row.textContent = `${file.name} · 已保存`;
            succeeded += 1;
          } catch (error) { row.textContent = `${file.name} · ${error.message}`; row.classList.add('failed'); }
        }
        $('upload-files').value = '';
        if (succeeded) {
          await refreshAfterWrite();
          notice(`已保存 ${succeeded} 個檔案${succeeded < files.length ? '；其餘檔案未成功，請查看上傳結果。' : '。'}`);
        }
      } finally { state.busy = false; $('upload-files').disabled = false; }
    });
  });

  function fillDetail(item) {
    state.detail = item;
    setError('detail-error', null);
    $('reload-detail').hidden = true;
    $('detail-title').value = item.title || '';
    $('detail-notes').value = item.notes || '';
    $('detail-collection').value = item.collection_id || '';
    $('detail-category').value = categoryKey(item.category);
    $('detail-tags').value = itemTags(item).join(', ');
    state.detailSaved = JSON.stringify(detailFields());
    $('detail-kind').textContent = item.kind === 'file' ? '檔案' : item.source_type === 'youtube' ? 'YouTube 收藏' : '網站收藏';
    $('detail-version').textContent = `版本 ${item.version}`;
    $('detail-archived').hidden = !item.archived;
    $('archive-item').textContent = item.archived ? '還原資料' : '封存資料';
    $('detail-dates').textContent = `加入：${dateLabel(item.created_at)}　更新：${dateLabel(item.updated_at)}`;
    const resource = $('detail-resource');
    resource.replaceChildren();
    if (item.kind === 'file') {
      resource.append(element('p', '', `${item.file_name || '檔案'} · ${formatBytes(item.size)} · ${item.mime || '未知格式'}`));
      const download = element('a', '', '下載原始檔案');
      download.href = `/api/files/${encodeURIComponent(item.id)}`;
      download.download = item.file_name || '';
      resource.append(download);
    } else resource.append(externalLink(item.url, item.url));
    const classification = item.classification || {};
    const overrides = classification.overrides || {};
    const manual = [overrides.category ? '分類' : '', overrides.tags ? '標籤' : ''].filter(Boolean);
    $('classification-method').textContent = classification.method === 'rules' ? `自動分類（規則）：根據名稱、格式、網址及可讀取的文字作出判斷，並非 AI 模型分析。${manual.length ? `目前${manual.join('及')}已由你調整。` : ''}` : classification.method === 'manual' ? '目前分類已經人手設定。' : '分類與標籤可由你手動調整；重新分類會按現有規則更新。';
    const signals = Array.isArray(classification.signals) ? classification.signals : [];
    const explanations = [...new Set(signals.map(classificationSignal))];
    if (!explanations.length) explanations.push('按名稱、格式及已提供內容判斷。');
    $('classification-signals').replaceChildren(...explanations.map((explanation) => element('li', '', explanation)));
  }

  async function openDetail(id) {
    const request = ++state.detailRequest;
    try {
      const data = await api(`/api/items/${encodeURIComponent(id)}`);
      if (request !== state.detailRequest) return;
      fillDetail(data.item);
      openDialog('detail-dialog');
    } catch (error) { if (request === state.detailRequest) notice(error.message, true); }
  }

  function detailFields() {
    return { title: $('detail-title').value.trim(), notes: $('detail-notes').value, tags: splitTags($('detail-tags').value), category: $('detail-category').value, collection_id: $('detail-collection').value || null };
  }

  function detailUpdateFields() {
    const fields = detailFields();
    const saved = JSON.parse(state.detailSaved);
    if (fields.category === saved.category) delete fields.category;
    if (JSON.stringify(fields.tags) === JSON.stringify(saved.tags)) delete fields.tags;
    return fields;
  }

  function requireSavedDetail() {
    if (JSON.stringify(detailFields()) !== state.detailSaved) throw new Error('請先儲存目前修改，再進行這個操作。');
  }

  async function patchDetail(body, successMessage) {
    const item = state.detail;
    if (!item) return;
    try {
      const data = await api(`/api/items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { expected_version: item.version, ...body } });
      fillDetail(data.item);
      await refreshAfterWrite();
      notice(successMessage);
    } catch (error) {
      if (error.status === 409) $('reload-detail').hidden = false;
      throw error;
    }
  }

  $('detail-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, 'detail-error', () => patchDetail(detailUpdateFields(), '修改已保存。'));
  });
  $('archive-item').addEventListener('click', () => submitForm($('detail-form'), 'detail-error', async () => {
    requireSavedDetail();
    const archived = !state.detail.archived;
    await patchDetail({ archived }, archived ? '資料已封存，可在「查看已封存」內還原。' : '資料已還原。');
  }));
  $('reclassify').addEventListener('click', () => submitForm($('detail-form'), 'detail-error', async () => { requireSavedDetail(); await patchDetail({ reclassify: true }, '已重新按規則分類。'); }));
  $('reload-detail').addEventListener('click', () => submitForm($('detail-form'), 'detail-error', async () => {
    const data = await api(`/api/items/${encodeURIComponent(state.detail.id)}`);
    fillDetail(data.item);
  }));

  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => {
    if (!state.busy) button.closest('dialog').close();
  }));
  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('cancel', (event) => { if (state.busy) event.preventDefault(); });
    dialog.addEventListener('click', (event) => {
      if (event.target !== dialog || state.busy) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
  });
  document.querySelectorAll('[data-kind]').forEach((tab) => {
    tab.addEventListener('click', () => setKind(tab.dataset.kind));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const kind = event.key === 'Home' ? 'file' : event.key === 'End' ? 'link' : state.kind === 'file' ? 'link' : 'file';
      setKind(kind);
      $(kind === 'file' ? 'files-tab' : 'links-tab').focus();
    });
  });
  $('add-item').addEventListener('click', openAdd);
  $('empty-add').addEventListener('click', openAdd);
  $('new-collection').addEventListener('click', () => {
    $('collection-form').reset(); setError('collection-error', null); openDialog('collection-dialog');
  });
  $('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => refresh(), 250); });
  ['category-filter', 'collection-filter', 'source-filter', 'archived-filter'].forEach((id) => $(id).addEventListener('change', () => refresh()));
  $('previous-page').addEventListener('click', () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadItems(); });
  $('next-page').addEventListener('click', () => { state.offset += PAGE_SIZE; loadItems(); });

  async function init() {
    populateCategories('category-filter', false, true);
    populateCategories('link-category', true);
    populateCategories('detail-category');
    populateCategories('collection-icon');
    try {
      const info = await api('/api/info');
      $('company-name').textContent = info.company_name || '藍圖專員工作室';
      document.title = `${info.company_name || '藍圖專員工作室'} · 公司資料庫`;
      $('version').textContent = info.version ? `Blueprint Folder Agent ${info.version}` : '';
      await loadCollections();
      await loadItems();
    } catch (error) {
      $('item-grid').setAttribute('aria-busy', 'false');
      $('result-count').textContent = '未能連接資料庫';
      notice(error.message, true);
    }
  }
  init();
})();
