'use strict';

(() => {
  const CATEGORIES = {
    general: '一般資料', chat: '對話／客服', web: '網站／UI', finance: '金融／財務',
    document: '文件', calendar: '日程', automation: '自動化', research: '研究／學習'
  };
  const MAX_FILE_SIZE = 20 * 1024 * 1024;
  const PAGE_SIZE = 100;
  const $ = (id) => document.getElementById(id);
  const state = { view: 'dashboard', collectionId: '', newLinkPurpose: 'link', collections: [], items: [], selected: new Set(), contextIds: [], offset: 0, total: 0, detail: null, request: 0, commandRequest: 0, contextRequest: 0, detailRequest: 0, detailSaved: '', busy: false, profile: null, profileFormVersion: 0, profileRequest: 0, profileBusy: false, layout: 'grid', sort: 'updated' };
  let searchTimer;
  let commandTimer;
  let sidebarReturnFocus;
  let workdaysUI;

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

  const ICONS = {
    dashboard: ['M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'],
    calendar: ['M4 5h16v16H4Z M4 10h16 M8 3v4 M16 3v4 M8 14h2 M14 14h2 M8 18h2'],
    checklist: ['M9 5h12 M9 12h12 M9 19h12 M3 5l1 1 2-3 M3 12l1 1 2-3 M3 19l1 1 2-3'],
    folder: ['M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z'],
    bookmark: ['M6 3h12v18l-6-4-6 4Z'], play: ['M9 7l8 5-8 5Z', 'M3 5h18v14H3Z'],
    star: ['m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z'],
    inbox: ['M4 4h16l2 11v5H2v-5Z', 'M2 15h6l2 3h4l2-3h6'],
    archive: ['M3 3h18v5H3z M5 8v13h14V8 M10 12h4'],
    plus: ['M12 5v14 M5 12h14'], search: ['M21 21l-5-5', 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
    settings: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8', 'm9 3-1 3-3 1-2 3 2 2-1 3 2 3h3l2 3 3-1 1-3 3-1 2-3-2-2 1-3-2-3h-3l-2-3Z'],
    help: ['M9 8a3 3 0 1 1 5 2c-2 1-2 2-2 3 M12 17h.01', 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0'],
    menu: ['M4 6h16 M4 12h16 M4 18h16'], chevron: ['m8 10 4 4 4-4'],
    arrow: ['M5 12h14 m-5-5 5 5-5 5'], 'arrow-up-right': ['M6 18 18 6 M6 6h12v12'],
    upload: ['M12 16V3 m-5 5 5-5 5 5 M4 15v6h16v-6'], download: ['M12 3v13 m-5-5 5 5 5-5 M4 17v4h16v-4'],
    link: ['M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2'],
    'folder-plus': ['M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z M12 11v6 M9 14h6'],
    layout: ['M3 4h18v16H3Z M3 9h18 M9 9v11'], grid: ['M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'],
    list: ['M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01']
  };
  const VIEWS = {
    dashboard: ['工作台', '管理公司的日常工作，隨時接續下一步。', 'YOUR WORKSPACE'],
    workdays: ['每日工作', '留下每天的成果、決定、參考與下一步。', 'DAILY WORK'],
    todos: ['待辦事項', '從工作對話留下的下一步，逐項完成。', 'NEXT ACTIONS'],
    files: ['文件庫', '工作文件與程式碼，用清楚的圖案與標籤歸位。', 'FILE LIBRARY'],
    links: ['資料收藏', '收好網站、UI 靈感與每一個值得參考的連結。', 'YOUR REFERENCES'],
    youtube: ['YouTube 學習', '把影片中的啟發，變成下一步用得到的知識。', 'LEARN & APPLY'],
    pinned: ['星號收藏', '常用或值得再看的資料，放近一點。', 'KEEP IT CLOSE'],
    unfiled: ['待整理', '為這些資料找一個合適的收藏集。', 'A LITTLE ORGANIZING'],
    archived: ['已封存', '暫時收起，仍然可以隨時還原。', 'YOUR ARCHIVE']
  };

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.65');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true'); svg.classList.add('ui-icon');
    (ICONS[name] || ICONS.folder).forEach((d) => { const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); svg.append(path); });
    return svg;
  }
  function renderStaticIcons() { document.querySelectorAll('[data-icon]').forEach((node) => node.replaceChildren(icon(node.dataset.icon))); }

  function populateCollectionSelects() {
    ['collection-filter', 'link-collection', 'upload-collection', 'detail-collection'].forEach((id) => {
      const select = $(id), previous = select.value;
      select.replaceChildren(); option(select, '', id === 'collection-filter' ? '所有收藏集' : '未分配收藏集');
      state.collections.forEach((collection) => option(select, collection.id, collection.name));
      select.value = [...select.options].some((entry) => entry.value === previous) ? previous : '';
    });
    $('collection-nav').replaceChildren(...state.collections.map((collection) => {
      const button = element('button', 'nav-item'); button.type = 'button';
      const image = element('img', 'collection-icon'); image.src = `/icons/${categoryKey(collection.icon)}.svg`; image.alt = '';
      button.append(image, element('span', '', collection.name), element('span', 'nav-count', collection.count || 0));
      button.classList.toggle('active', state.view === 'collection' && state.collectionId === collection.id);
      if (button.classList.contains('active')) button.setAttribute('aria-current', 'page');
      button.addEventListener('click', () => setView('collection', collection.id));
      return button;
    }));
    $('no-collections').hidden = state.collections.length > 0;
  }

  async function loadCollections(background = false) {
    const data = await api('/api/collections');
    if (background && workspaceBusy()) return false;
    state.collections = Array.isArray(data.collections) ? data.collections : [];
    populateCollectionSelects();
  }
  async function loadInfo() {
    const info = await api('/api/info');
    document.title = info.product_title || 'Blueprint Studio Backend｜藍圖工作室（後台）';
    $('version').textContent = '工作台 v2.5.1';
    $('version').title = info.version ? `後台 API v${info.version}` : '';
    document.querySelectorAll('[data-count]').forEach((node) => { node.textContent = Number(info.stats?.[node.dataset.count]) || 0; });
    if (info.company_profile) applyCompanyProfile(info.company_profile);
  }

  function applyCompanyProfile(profile) {
    if (state.profile && state.profile.version !== profile.version) workdaysUI?.invalidateInstructions?.();
    state.profile = profile;
    const name = typeof profile.name === 'string' && profile.name.trim() ? profile.name : '我的公司';
    const industry = typeof profile.industry === 'string' && profile.industry.trim() ? profile.industry : '專屬管理後台';
    $('company-name').textContent = name; $('company-name').title = name;
    $('company-industry').textContent = industry; $('company-industry').title = industry;
    $('company-avatar').textContent = [...name][0] || '企';
    $('settings-company-summary').textContent = profile.configured ? `${name}${profile.industry ? ` · ${profile.industry}` : ''}` : '設定公司名稱、行業與目標。';
  }
  function fillCompanyProfile(profile) {
    state.profileFormVersion = Number(profile.version) || 0;
    $('profile-name').value = profile.name || ''; $('profile-industry').value = profile.industry || '';
    $('profile-description').value = profile.description || ''; $('profile-goals').value = profile.goals || '';
    $('company-profile-heading').textContent = profile.configured ? '公司資料' : '設定你公司的專屬後台';
    $('reload-company-profile').hidden = true; setError('company-profile-error', null);
  }
  async function reloadCompanyProfile() {
    const request = ++state.profileRequest;
    const controls = [...$('company-profile-form').elements].filter((control) => !control.hasAttribute('data-close'));
    controls.forEach((control) => { control.disabled = true; });
    try {
      const data = await api('/api/company-profile');
      if (request !== state.profileRequest || !$('company-profile-dialog').open) return;
      applyCompanyProfile(data.company_profile); fillCompanyProfile(data.company_profile);
      controls.forEach((control) => { control.disabled = false; }); $('profile-name').focus();
    } catch (error) {
      if (request !== state.profileRequest || !$('company-profile-dialog').open) return;
      setError('company-profile-error', error); $('reload-company-profile').hidden = false; $('reload-company-profile').disabled = false;
    }
  }
  function openCompanyProfile() {
    $('settings-dialog').close();
    fillCompanyProfile(state.profile || { version: 0 }); openDialog('company-profile-dialog'); reloadCompanyProfile();
  }
  async function saveCompanyProfile(event) {
    event.preventDefault(); if (state.profileBusy) return;
    setError('company-profile-error', null);
    const name = $('profile-name').value.trim();
    if (!name) { setError('company-profile-error', new Error('請填寫公司名稱。')); $('profile-name').focus(); return; }
    const body = { expected_version: state.profileFormVersion, name, industry: $('profile-industry').value.trim(), description: $('profile-description').value, goals: $('profile-goals').value };
    const controls = [...$('company-profile-form').elements]; controls.forEach((control) => { control.disabled = true; }); state.profileBusy = true;
    try {
      const data = await api('/api/company-profile', { method: 'PATCH', body });
      applyCompanyProfile(data.company_profile); $('company-profile-dialog').close(); notice('公司資料已保存，這是你公司的專屬後台。');
    } catch (error) {
      setError('company-profile-error', error);
      if (error.status === 409) $('reload-company-profile').hidden = false;
    } finally { state.profileBusy = false; controls.forEach((control) => { control.disabled = false; }); }
  }
  async function copyInstallInstruction() {
    const control = $('copy-install-instruction'); control.disabled = true;
    $('install-instruction-status').textContent = '正在準備安裝指令…';
    try {
      const data = await api('/api/install-instructions');
      if (typeof data.instruction !== 'string' || !data.instruction.trim()) throw new Error('未能取得安裝指令，請稍後再試。');
      const text = $('install-instruction-text'); text.value = data.instruction;
      let copied = false;
      try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(data.instruction); copied = true; } } catch { /* Visible manual copy is available below. */ }
      if (!copied) { text.closest('details').open = true; text.focus(); text.select(); try { copied = document.execCommand('copy'); } catch { /* Browser can use native text selection. */ } }
      $('install-instruction-status').textContent = copied ? '已複製，貼到具備工具執行能力的 AI 即可開始設定。' : '指令已展開及選取，請用瀏覽器的複製功能，貼到你使用的 AI。';
    } catch (error) { $('install-instruction-status').textContent = error.message; }
    finally { control.disabled = false; }
  }

  function makeCard(item) {
    const card = element('article', 'item-card'); card.dataset.itemId = item.id; card.classList.toggle('selected', state.selected.has(item.id));
    const controls = element('div', 'card-controls');
    const selectLabel = element('label', 'card-select-label');
    const check = element('input', 'card-select-checkbox'); check.type = 'checkbox'; check.checked = state.selected.has(item.id); check.setAttribute('aria-label', `選取 ${item.title || item.file_name || '資料'}`);
    check.addEventListener('change', () => {
      if (check.checked && state.selected.size >= 20) { check.checked = false; notice('一次最多選取 20 項資料。', true); return; }
      if (check.checked) state.selected.add(item.id); else state.selected.delete(item.id);
      card.classList.toggle('selected', check.checked); updateSelection();
    });
    selectLabel.append(check); controls.append(selectLabel);
    const star = element('button', 'star-button'); star.type = 'button'; star.classList.toggle('pinned', Boolean(item.pinned)); star.append(icon('star'));
    star.setAttribute('aria-label', item.pinned ? `取消星號：${item.title}` : `加入星號：${item.title}`); star.setAttribute('aria-pressed', String(Boolean(item.pinned))); star.title = item.pinned ? '取消星號收藏' : '加入星號收藏';
    star.addEventListener('click', async () => {
      const restoreFocus = document.activeElement === star, originalView = state.view, originalCollection = state.collectionId;
      const originalIndex = state.items.findIndex((entry) => entry.id === item.id); star.disabled = true;
      try { await api(`/api/items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { expected_version: item.version, pinned: !item.pinned } }); await refreshAfterWrite(); }
      catch (error) { notice(error.message, true); await loadItems(); }
      finally {
        star.disabled = false;
        if (restoreFocus && state.view === originalView && state.collectionId === originalCollection && (document.activeElement === document.body || document.activeElement === star)) {
          const cards = [...document.querySelectorAll('.item-card')];
          const target = cards.find((entry) => entry.dataset.itemId === item.id)?.querySelector('.star-button') || cards[Math.min(originalIndex, cards.length - 1)]?.querySelector('.star-button') || $('library-heading');
          if (target === $('library-heading')) target.tabIndex = -1;
          target.focus();
        }
      }
    });
    controls.append(star); card.append(controls);
    const top = element('div', 'item-top'), image = element('img', 'item-icon');
    image.src = `/icons/${categoryKey(item.category)}.svg`; image.alt = categoryName(item.category); image.width = 56; image.height = 56;
    const group = element('div', 'item-title-group');
    const title = element('button', 'item-title', item.title || item.file_name || '未命名資料'); title.type = 'button'; title.addEventListener('click', () => openDetail(item.id));
    group.append(title);
    let source = item.kind === 'file' ? `${formatBytes(item.size)} · ${item.file_name?.split('.').pop()?.toUpperCase() || 'FILE'}` : item.source_type === 'youtube' ? 'YouTube' : '網站連結';
    if (item.kind === 'link' && safeUrl(item.url)) source += ` · ${new URL(item.url).hostname.replace(/^www\./, '')}`;
    group.append(element('p', 'item-source', source)); top.append(image, group); card.append(top, element('p', 'item-notes', item.notes || '已保存，隨時可以交給 AI 繼續整理。'));
    const badges = element('div', 'badges'); badges.append(element('span', 'badge category-badge', categoryName(item.category)));
    const tags = itemTags(item).filter((tag) => tag !== categoryName(item.category));
    tags.slice(0, 2).forEach((tag) => badges.append(element('span', 'badge', tag)));
    if (tags.length > 2) badges.append(element('span', 'badge', `＋${tags.length - 2}`)); card.append(badges);
    const bottom = element('div', 'card-bottom'); bottom.append(element('span', '', collectionName(item.collection_id)), element('span', '', dateLabel(item.updated_at))); card.append(bottom);
    return card;
  }

  function viewParams() {
    const params = new URLSearchParams({ archived: state.view === 'archived' ? '1' : '0', limit: state.view === 'dashboard' ? '6' : String(PAGE_SIZE), offset: String(state.offset), sort: state.view === 'dashboard' ? 'updated' : state.sort });
    if (state.view === 'files') params.set('kind', 'file');
    if (state.view === 'links' || state.view === 'youtube') params.set('kind', 'link');
    if (state.view === 'youtube') params.set('source_type', 'youtube');
    if (state.view === 'pinned') params.set('pinned', '1');
    if (state.view === 'unfiled') params.set('unfiled', '1');
    if (state.view === 'collection') params.set('collection_id', state.collectionId);
    if (state.view !== 'dashboard') {
      if ($('search').value.trim()) params.set('q', $('search').value.trim());
      if ($('category-filter').value) params.set('category', $('category-filter').value);
      if ($('collection-filter').value && state.view !== 'collection') params.set('collection_id', $('collection-filter').value);
    }
    return params;
  }

  function updateSelection() {
    $('selection-bar').hidden = state.selected.size === 0;
    $('selection-count').textContent = `已選取 ${state.selected.size} 項`;
    $('select-all').checked = state.items.length > 0 && state.items.every((item) => state.selected.has(item.id));
    $('select-all').indeterminate = state.selected.size > 0 && !$('select-all').checked;
  }
  function clearSelection() { state.selected.clear(); document.querySelectorAll('.card-select-checkbox').forEach((node) => { node.checked = false; }); document.querySelectorAll('.item-card.selected').forEach((node) => node.classList.remove('selected')); updateSelection(); }

  async function loadItems(background = false) {
    const request = ++state.request;
    $('item-grid').setAttribute('aria-busy', 'true');
    try {
      const data = await api(`/api/items?${viewParams()}`);
      if (request !== state.request) return;
      if (background && workspaceBusy()) return false;
      state.items = Array.isArray(data.items) ? data.items : []; state.total = Number(data.total) || 0;
      const visible = new Set(state.items.map((item) => item.id)); state.selected.forEach((id) => { if (!visible.has(id)) state.selected.delete(id); });
      $('item-grid').replaceChildren(...state.items.map(makeCard)); updateSelection();
      $('result-count').textContent = state.view === 'dashboard' ? (state.total ? `共 ${state.total} 項資料 · 顯示最近 ${state.items.length} 項` : '每一次收藏，都是工作室的累積。') : `共 ${state.total} 項資料`;
      $('empty-state').hidden = state.items.length > 0;
      const filtered = Boolean($('search').value.trim() || $('category-filter').value || $('collection-filter').value);
      $('clear-filters').hidden = !filtered;
      $('empty-title').textContent = filtered ? '換個關鍵字，再找找。' : state.view === 'archived' ? '目前沒有封存資料。' : state.view === 'pinned' ? '把常用的資料，加一顆星。' : state.view === 'unfiled' ? '資料都已經各就各位。' : '這裏準備好，等你的第一份資料。';
      $('empty-copy').textContent = filtered ? '試試其他名稱、標籤，或清除篩選。' : state.view === 'pinned' ? '按資料卡上的星號，就能在這裏快速找到。' : state.view === 'unfiled' ? '新增後未分配收藏集的資料，會先放在這裏。' : state.view === 'archived' ? '封存的資料仍然保留，可在詳情內還原。' : '在左側請 AI 保存文件、網站或學習影片，這裡會同步顯示。';
      $('empty-add').hidden = filtered || ['pinned', 'unfiled', 'archived'].includes(state.view);
      $('previous-page').hidden = state.view === 'dashboard' || state.offset === 0;
      $('next-page').hidden = state.view === 'dashboard' || state.offset + PAGE_SIZE >= state.total;
      $('page-label').textContent = state.view !== 'dashboard' && state.total > PAGE_SIZE ? `${state.offset + 1}–${Math.min(state.offset + PAGE_SIZE, state.total)} / ${state.total}` : '';
      return true;
    } catch (error) { if (request === state.request) { $('result-count').textContent = '讀取失敗'; notice(error.message, true); } if (background) throw error; }
    finally { if (request === state.request) $('item-grid').setAttribute('aria-busy', 'false'); }
  }

  function refresh(resetPage = true, background = false) { if (resetPage) state.offset = 0; if (['workdays', 'todos'].includes(state.view)) return workdaysUI?.refreshCurrent?.(background) || Promise.resolve(); return loadItems(background); }
  async function refreshAfterWrite() { await Promise.all([loadCollections(), loadInfo()]); await refresh(); if (state.view === 'dashboard') await workdaysUI?.refreshDashboard(); }

  function setView(view, collectionId = '') {
    if (!Object.hasOwn(VIEWS, view) && view !== 'collection') return;
    state.view = view; state.collectionId = collectionId; state.offset = 0; clearSelection();
    $('search').value = ''; $('category-filter').value = ''; $('collection-filter').value = '';
    const collection = state.collections.find((entry) => entry.id === collectionId);
    const [title, description, eyebrow] = view === 'collection' ? [collection?.name || '收藏集', collection?.description || '放在一起的參考，讓想法更容易接起來。', 'YOUR COLLECTION'] : VIEWS[view];
    $('page-title').replaceChildren(document.createTextNode(title), element('span', 'title-dot', '.'));
    $('breadcrumb-current').textContent = title; $('page-description').textContent = description; $('page-eyebrow').textContent = eyebrow;
    const workView = ['workdays', 'todos'].includes(view);
    $('view-heading').hidden = view === 'dashboard';
    $(view === 'dashboard' ? 'dashboard-library-slot' : 'library-view-slot').append($('library-panel'));
    $('dashboard-panel').hidden = view !== 'dashboard'; $('library-toolbar').hidden = view === 'dashboard'; $('library-panel').hidden = workView; $('workdays-panel').hidden = !workView;
    $('work-panel-title').textContent = view === 'todos' ? '待辦清單' : '工作紀錄';
    $('library-heading').textContent = view === 'dashboard' ? '最近更新' : view === 'collection' ? '收藏集內容' : '所有資料';
    $('sort-order').hidden = view === 'dashboard'; $('collection-filter').hidden = view === 'collection' || view === 'unfiled';
    document.querySelectorAll('.main-nav [data-view]').forEach((button) => { const active = button.dataset.view === view; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    populateCollectionSelects(); closeSidebar(); notice('');
    workdaysUI?.setView(view);
    if (!workView) refresh();
    if (view === 'dashboard') workdaysUI?.refreshDashboard();
    window.BlueprintStudio?.legacyView(view, collectionId);
  }

  function openDialog(id) {
    $('add-menu').hidden = true; $('add-item').setAttribute('aria-expanded', 'false'); closeSidebar();
    const dialog = $(id); if (!dialog.open) dialog.showModal();
    const first = dialog.querySelector('input:not([type=hidden]), textarea, select'); first?.focus();
  }
  function openAdd(action = 'link') {
    notice('');
    const selectedCollection = state.view === 'collection' ? state.collectionId : $('collection-filter').value;
    if (action === 'upload') {
      $('upload-form').reset(); $('upload-results').replaceChildren(); setError('upload-error', null); $('upload-collection').value = selectedCollection; openDialog('upload-dialog');
    } else if (action === 'collection') {
      $('collection-form').reset(); setError('collection-error', null); openDialog('collection-dialog');
    } else {
      state.newLinkPurpose = action; $('link-form').reset(); setError('link-error', null); $('link-collection').value = selectedCollection;
      $('link-category').value = action === 'design' ? 'web' : '';
      $('link-heading').textContent = action === 'youtube' ? '保存 YouTube 學習影片' : action === 'design' ? '收藏網站／UI 參考' : '收藏資料';
      $('link-url').placeholder = action === 'youtube' ? 'https://www.youtube.com/watch?v=…' : 'https://'; openDialog('link-dialog');
    }
  }

  function closeSidebar() {
    if (!$('sidebar').classList.contains('open')) return;
    $('sidebar').classList.remove('open'); $('sidebar').removeAttribute('role'); $('sidebar').removeAttribute('aria-modal');
    $('sidebar-backdrop').hidden = true; $('mobile-menu').setAttribute('aria-expanded', 'false'); document.body.classList.remove('drawer-open');
    if (sidebarReturnFocus?.isConnected) sidebarReturnFocus.focus();
  }
  function openSidebar() {
    sidebarReturnFocus = document.activeElement; $('sidebar').classList.add('open'); $('sidebar').setAttribute('role', 'dialog'); $('sidebar').setAttribute('aria-modal', 'true');
    $('sidebar-backdrop').hidden = false; $('mobile-menu').setAttribute('aria-expanded', 'true'); document.body.classList.add('drawer-open');
    $('sidebar').querySelector('button, a')?.focus();
  }

  function saveDisplayPreferences() { try { localStorage.setItem('blueprint-backend-display-v1', JSON.stringify({ layout: state.layout, sort: state.sort })); } catch { /* Server data is unaffected when browser storage is unavailable. */ } }
  function applyDisplayPreferences() {
    $('item-grid').classList.toggle('list-mode', state.layout === 'list');
    ['grid', 'list'].forEach((layout) => { $(`${layout}-view`).classList.toggle('active', state.layout === layout); $(`${layout}-view`).setAttribute('aria-pressed', String(state.layout === layout)); });
    $('sort-order').value = $('settings-sort').value = state.sort; $('settings-layout').value = state.layout;
  }
  function setLayout(layout) { state.layout = layout === 'list' ? 'list' : 'grid'; applyDisplayPreferences(); saveDisplayPreferences(); }
  function setSort(sort) { state.sort = ['updated', 'created', 'title'].includes(sort) ? sort : 'updated'; applyDisplayPreferences(); saveDisplayPreferences(); refresh(); }

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
      $('collection-dialog').close();
      if (data.collection?.id) setView('collection', data.collection.id);
      await loadInfo();
      notice('收藏集已建立。');
    });
  });

  $('link-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm(event.currentTarget, 'link-error', async () => {
      const url = safeUrl($('link-url').value.trim());
      if (!url) throw new Error('請輸入以 https:// 或 http:// 開頭的有效網址。');
      const host = new URL(url).hostname.toLowerCase().replace(/\.$/, '');
      if (state.newLinkPurpose === 'youtube' && host !== 'youtu.be' && host !== 'youtube.com' && !host.endsWith('.youtube.com')) throw new Error('請輸入 YouTube 影片連結；其他網站可用「收藏連結」保存。');
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
    await patchDetail({ archived }, archived ? '資料已封存，可在「已封存」內還原。' : '資料已還原。');
  }));
  $('reclassify').addEventListener('click', () => submitForm($('detail-form'), 'detail-error', async () => { requireSavedDetail(); await patchDetail({ reclassify: true }, '已重新按規則分類。'); }));
  $('reload-detail').addEventListener('click', () => submitForm($('detail-form'), 'detail-error', async () => {
    const data = await api(`/api/items/${encodeURIComponent(state.detail.id)}`);
    fillDetail(data.item);
  }));

  async function searchCommands() {
    const request = ++state.commandRequest, query = $('command-search').value.trim();
    let matchingActions = 0;
    $('command-actions').querySelectorAll('[data-action], [data-workday-action]').forEach((button) => { const terms = { upload: '加入檔案 上傳文件 upload file', link: '收藏連結 網站 youtube link', collection: '新增收藏集 collection', workday: '今日收工 整理工作 每日 工作紀錄 待辦 daily work todo' }; const matches = !query || terms[button.dataset.action || 'workday'].toLowerCase().includes(query.toLowerCase()); button.hidden = !matches; if (matches) matchingActions += 1; });
    $('command-actions').hidden = matchingActions === 0; $('command-results').replaceChildren();
    if (!query) { $('command-status').textContent = '搜尋全部未封存資料'; return; }
    $('command-status').textContent = '正在搜尋…';
    try {
      const results = await Promise.allSettled([
        api(`/api/items?${new URLSearchParams({ q: query, archived: '0', limit: '8', sort: 'updated' })}`),
        api(`/api/workdays?${new URLSearchParams({ q: query, limit: '4' })}`),
        api(`/api/todos?${new URLSearchParams({ q: query, done: 'all', limit: '4' })}`),
        window.BlueprintStudio?.isReady() ? window.BlueprintStudio.search(query) : Promise.resolve({records: [], total: 0})
      ]);
      if (request !== state.commandRequest) return;
      const items = results[0].status === 'fulfilled' ? results[0].value : { items: [], total: 0 };
      const workdays = results[1].status === 'fulfilled' ? results[1].value : { workdays: [], total: 0 };
      const todos = results[2].status === 'fulfilled' ? results[2].value : { todos: [], total: 0 };
      const studio = results[3].status === 'fulfilled' ? results[3].value : {records: [], total: 0};
      const total = (Number(items.total) || 0) + (Number(workdays.total) || 0) + (Number(todos.total) || 0) + (Number(studio.total) || 0);
      const failed = results.some((result) => result.status === 'rejected');
      $('command-status').textContent = total ? `找到 ${total} 項資料${failed ? '，部分資料暫時未能搜尋。' : ''}` : failed ? '部分資料暫時未能搜尋，請稍後再試。' : '未找到資料，試試其他名稱或標籤。';
      const nodes = [];
      function appendGroup(label, entries, category, title, subtitle, activate) {
        if (!entries.length) return;
        nodes.push(element('p', 'nav-label command-group-label', label));
        entries.forEach((entry) => {
          const button = element('button', 'command-result'); button.type = 'button';
          const image = element('img'); image.src = `/icons/${category(entry)}.svg`; image.alt = '';
          const group = element('span'); group.append(element('strong', '', title(entry)), element('small', '', subtitle(entry)));
          button.append(image, group, icon('arrow-up-right'));
          button.addEventListener('click', () => { $('command-dialog').close(); activate(entry); }); nodes.push(button);
        });
      }
      appendGroup('文件與收藏', items.items || [], (item) => categoryKey(item.category), (item) => item.title || item.file_name, (item) => `${categoryName(item.category)} · ${collectionName(item.collection_id)}`, (item) => openDetail(item.id));
      appendGroup('每日工作', workdays.workdays || [], () => 'calendar', (item) => item.title || `${item.date} 工作紀錄`, (item) => `${item.date} · ${item.source || '工作紀錄'}`, (item) => workdaysUI?.openReport(item.id));
      appendGroup('待辦事項', todos.todos || [], () => 'document', (item) => item.title, (item) => `${item.done ? '已完成' : '未完成'} · ${item.date} · ${item.workday_title || '每日工作'}`, (item) => workdaysUI?.openReport(item.workday_id));
      appendGroup('公司與工作內容', studio.records || [], () => 'document', item => item.title, item => item.updated_at.slice(0, 10), item => window.BlueprintStudio.openRecord(item.id));
      $('command-results').replaceChildren(...nodes);
    } catch (error) { if (request === state.commandRequest) $('command-status').textContent = error.message; }
  }
  function firstCommandButton() { return $('command-results').querySelector('button') || (!$('command-actions').hidden ? $('command-actions').querySelector('button:not([hidden])') : null); }
  function openCommand() { $('command-search').value = ''; searchCommands(); openDialog('command-dialog'); }

  async function generateContext() {
    const request = ++state.contextRequest;
    const buttons = [$('context-generate'), $('context-copy'), $('context-download')]; buttons.forEach((node) => { node.disabled = true; }); setError('context-error', null);
    $('context-summary').textContent = '正在整理選定資料…';
    try {
      const data = await api('/api/context', { method: 'POST', body: { item_ids: state.contextIds, task: $('context-task').value } });
      if (request !== state.contextRequest) return;
      $('context-markdown').value = data.markdown;
      $('context-summary').textContent = `已整理 ${data.count} 項資料${(data.truncated || data.omitted_text) ? '，較長內容已節錄。' : '。'}`;
      $('context-copy').disabled = $('context-download').disabled = false;
    } catch (error) { if (request === state.contextRequest) { $('context-summary').textContent = ''; $('context-markdown').value = ''; setError('context-error', error); } }
    finally { if (request === state.contextRequest) $('context-generate').disabled = false; }
  }
  function openContext() {
    if (!state.selected.size) return;
    state.contextIds = [...state.selected]; $('context-markdown').value = ''; $('context-task').value = state.view === 'youtube' ? 'learning' : 'general'; openDialog('context-dialog'); generateContext();
  }

  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => { if (!state.busy && !state.profileBusy && !workdaysUI?.isBusy?.()) button.closest('dialog').close(); }));
  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('cancel', (event) => { if (state.busy || state.profileBusy || workdaysUI?.isBusy?.()) event.preventDefault(); });
    dialog.addEventListener('click', (event) => { if (event.target !== dialog || state.busy || state.profileBusy || workdaysUI?.isBusy?.()) return; const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); });
  });
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
  document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => { if ($('command-dialog').open) $('command-dialog').close(); openAdd(button.dataset.action); }));
  document.querySelectorAll('[data-workday-action]').forEach((button) => button.addEventListener('click', () => { if ($('command-dialog').open) $('command-dialog').close(); workdaysUI?.openImport(); }));
  $('add-item').addEventListener('click', () => { $('add-menu').hidden = !$('add-menu').hidden; $('add-item').setAttribute('aria-expanded', String(!$('add-menu').hidden)); });
  document.addEventListener('click', (event) => { if (!event.target.closest('.add-wrapper')) { $('add-menu').hidden = true; $('add-item').setAttribute('aria-expanded', 'false'); } });
  $('empty-add').addEventListener('click', () => openAdd(state.view === 'files' ? 'upload' : state.view === 'youtube' ? 'youtube' : 'link'));
  $('new-collection').addEventListener('click', () => openAdd('collection'));
  $('command-button').addEventListener('click', openCommand);
  $('command-search').addEventListener('input', () => { clearTimeout(commandTimer); ++state.commandRequest; $('command-results').replaceChildren(); $('command-actions').hidden = true; $('command-status').textContent = '正在搜尋…'; commandTimer = setTimeout(searchCommands, 180); });
  $('command-search').addEventListener('keydown', (event) => { if (event.key === 'ArrowDown') { event.preventDefault(); firstCommandButton()?.focus(); } if (event.key === 'Enter') { event.preventDefault(); firstCommandButton()?.click(); } });
  $('command-results').addEventListener('keydown', (event) => { if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return; event.preventDefault(); const results = [...$('command-results').querySelectorAll('button')], index = results.indexOf(document.activeElement); results[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus(); if (index === 0 && event.key === 'ArrowUp') $('command-search').focus(); });
  $('mobile-menu').addEventListener('click', openSidebar); $('sidebar-backdrop').addEventListener('click', closeSidebar);
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { if (!document.querySelector('dialog[open]') || $('command-dialog').open) { event.preventDefault(); openCommand(); } }
    if (event.key === 'Escape') { closeSidebar(); $('add-menu').hidden = true; $('add-item').setAttribute('aria-expanded', 'false'); }
    if (event.key === 'Tab' && $('sidebar').classList.contains('open')) { const focusable = [...$('sidebar').querySelectorAll('a, button')], first = focusable[0], last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
  });
  window.addEventListener('resize', () => { if (window.innerWidth > 900) closeSidebar(); });
  $('help-button').addEventListener('click', () => openDialog('help-dialog')); $('footer-help').addEventListener('click', () => openDialog('help-dialog')); $('settings-button').addEventListener('click', () => openDialog('settings-dialog'));
  $('company-profile-form').addEventListener('submit', saveCompanyProfile);
  $('edit-company-profile').addEventListener('click', openCompanyProfile); $('reload-company-profile').addEventListener('click', reloadCompanyProfile);
  $('company-profile-dialog').addEventListener('close', () => { ++state.profileRequest; });
  $('copy-install-instruction').addEventListener('click', copyInstallInstruction);
  $('grid-view').addEventListener('click', () => setLayout('grid')); $('list-view').addEventListener('click', () => setLayout('list')); $('settings-layout').addEventListener('change', () => setLayout($('settings-layout').value));
  $('sort-order').addEventListener('change', () => setSort($('sort-order').value)); $('settings-sort').addEventListener('change', () => setSort($('settings-sort').value));
  $('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => refresh(), 250); });
  ['category-filter', 'collection-filter'].forEach((id) => $(id).addEventListener('change', () => refresh()));
  $('clear-filters').addEventListener('click', () => { $('search').value = $('category-filter').value = $('collection-filter').value = ''; refresh(); });
  $('previous-page').addEventListener('click', () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadItems(); }); $('next-page').addEventListener('click', () => { state.offset += PAGE_SIZE; loadItems(); });
  $('clear-selection').addEventListener('click', clearSelection); $('select-all').addEventListener('change', () => { if ($('select-all').checked) { state.selected.clear(); state.items.slice(0, 20).forEach((item) => state.selected.add(item.id)); } else state.selected.clear(); if (state.items.length > 20) notice('一次最多選取 20 項資料。'); $('item-grid').replaceChildren(...state.items.map(makeCard)); updateSelection(); });
  $('handoff-button').addEventListener('click', openContext); $('context-generate').addEventListener('click', generateContext); $('context-task').addEventListener('change', generateContext);
  $('context-copy').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('context-markdown').value); $('context-summary').textContent = '已複製，可以貼到你使用的 AI。'; } catch { $('context-markdown').focus(); $('context-markdown').select(); $('context-summary').textContent = '請按 Ctrl / ⌘ C 複製選取內容。'; } });
  $('context-download').addEventListener('click', () => { const blob = new Blob([$('context-markdown').value], { type: 'text/markdown;charset=utf-8' }); const url = URL.createObjectURL(blob); const link = element('a'); link.href = url; link.download = 'blueprint-ai-handoff.md'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); });

  async function init() {
    window.BlueprintConnection?.init({ api, openDialog, notice, setView });
    window.BlueprintUpdate?.init({ api, openDialog, notice });
    workdaysUI = window.BlueprintWorkdays;
    workdaysUI?.init({ api, element, icon, notice, openDialog, setView, refreshInfo: loadInfo, refreshLibrary: async () => { await loadCollections(); if (!['workdays', 'todos'].includes(state.view)) await refresh(); } });
    workdaysUI?.setView('dashboard');
    renderStaticIcons(); populateCategories('category-filter', false, true); populateCategories('link-category', true); populateCategories('detail-category'); populateCategories('collection-icon');
    try { const preferences = JSON.parse(localStorage.getItem('blueprint-backend-display-v1') || '{}'); if (preferences.layout === 'list') state.layout = 'list'; if (['updated', 'created', 'title'].includes(preferences.sort)) state.sort = preferences.sort; } catch { /* Display preference only; company data remains on server. */ }
    $('dashboard-library-slot').append($('library-panel'));
    applyDisplayPreferences(); $('library-toolbar').hidden = true; $('sort-order').hidden = true; $('page-date').textContent = new Date().toLocaleDateString('zh-HK', { month: 'long', day: 'numeric', weekday: 'short' });
    try { await Promise.all([loadInfo(), loadCollections()]); await Promise.all([loadItems(), workdaysUI?.refreshDashboard()]);  }
    catch (error) { $('item-grid').setAttribute('aria-busy', 'false'); $('result-count').textContent = '未能連接資料庫'; notice(error.message, true); }
  }
  function workspaceBusy() {
    return state.busy || state.profileBusy || !!workdaysUI?.isBusy?.() || state.selected.size > 0 ||
      !!document.querySelector('dialog[open], .workspace-board.editing') ||
      !!document.activeElement?.matches('textarea, select, input:not([type=checkbox]):not([type=button])');
  }
  function startWorkspace() { window.BlueprintWorkspace?.start({
    isBusy: workspaceBusy,
    refresh: async () => {
      await Promise.all([loadInfo(), loadCollections(true)]);
      if (workspaceBusy()) return false;
      if (state.view !== 'studio' && await refresh(false, true) === false) return false;
      if (state.view === 'dashboard') await workdaysUI?.refreshDashboard(true);
      await window.BlueprintStudio?.refreshVisible();
    }
  }); }
  init().then(async () => {
    await window.BlueprintStudio?.init({api, notice, icon, openDialog, closeSidebar, setView, openDetail, openAdd, openCommand,
      isBusy: workspaceBusy, hideLegacy: () => { state.view = 'studio'; clearSelection(); workdaysUI?.setView('studio'); }
    });
    startWorkspace();
  }).catch(error => notice(error.message, true));
})();
