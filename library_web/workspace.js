/* V2 notebook layout. Preferences stay separate from server/company data. */
'use strict';
((root) => {
  const KEY = 'blueprint-workspace-v2';
  const UNIT = 24;
  const IDS = ['files', 'links', 'youtube', 'unfiled', 'workdays', 'todos'];
  const DEFAULTS = [
    [0, 0, 10, 8], [10, 0, 10, 8], [20, 0, 10, 8],
    [30, 0, 10, 8], [0, 8, 20, 12], [20, 8, 20, 12]
  ];
  const integer = (value, fallback, min, max) => Number.isInteger(value) ? Math.min(max, Math.max(min, value)) : fallback;
  function normalize(value) {
    const source = value && value.version === 1 && value.cards && typeof value.cards === 'object' ? value.cards : {};
    const cards = {};
    IDS.forEach((id, index) => {
      const [x, y, w, h] = DEFAULTS[index], saved = source[id] || {};
      cards[id] = { x: integer(saved.x, x, 0, 72), y: integer(saved.y, y, 0, 160),
        w: integer(saved.w, w, 6, 48), h: integer(saved.h, h, 4, 32), collapsed: saved.collapsed === true };
    });
    return { version: 1, mode: value?.mode === 'manual' ? 'manual' : 'ai', cards };
  }
  const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  function arrange(value, columns, priority) {
    const cols = Math.max(6, Math.floor(columns) || 6), result = {}, placed = [];
    const ids = priority && IDS.includes(priority) ? [priority, ...IDS.filter(id => id !== priority)] : IDS;
    ids.forEach(id => {
      const source = value.cards[id], w = Math.min(cols, source.w);
      const rect = { x: source.x + w > cols ? 0 : source.x, y: source.y, w, h: source.collapsed ? 4 : source.h };
      while (placed.some(other => overlaps(rect, other))) rect.y++;
      placed.push(rect); result[id] = rect;
    });
    return result;
  }
  function start({ refresh, isBusy }) {
    const board = document.getElementById('workspace-board');
    if (!board) return;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { /* Use a safe layout. */ }
    let preferences = normalize(saved), editing = false, pointer = null, rectangles = {}, syncing = false, timer;
    const widgets = new Map([...board.querySelectorAll('[data-widget]')].map(node => [node.dataset.widget, node]));
    const status = document.getElementById('workspace-sync');
    const announce = (text) => { document.getElementById('board-announcement').textContent = text; };
    const persist = () => {
      try { localStorage.setItem(KEY, JSON.stringify(preferences)); }
      catch { announce('瀏覽器無法保存版面；公司資料不受影響。'); }
    };
    function applyMode() {
      document.body.dataset.entryMode = preferences.mode;
      document.getElementById('settings-entry-mode').value = preferences.mode;
    }
    function render(priority) {
      if (!board.clientWidth) return;
      const columns = Math.max(6, Math.floor(board.clientWidth / UNIT));
      rectangles = arrange(preferences, columns, priority);
      let rows = 0;
      widgets.forEach((node, id) => {
        const rect = rectangles[id], card = preferences.cards[id];
        node.style.gridColumn = `${rect.x + 1} / span ${rect.w}`;
        node.style.gridRow = `${rect.y + 1} / span ${rect.h}`;
        node.classList.toggle('collapsed', card.collapsed);
        const toggle = node.querySelector('[data-board-action="collapse"]');
        toggle.textContent = card.collapsed ? '+' : '−';
        toggle.setAttribute('aria-expanded', String(!card.collapsed));
        toggle.setAttribute('aria-label', `${card.collapsed ? '展開' : '收起'}${node.querySelector('h3').textContent}`);
        node.querySelector('.widget-size').textContent = `${rect.w} × ${rect.h}`;
        node.querySelector('.widget-content').hidden = card.collapsed;
        rows = Math.max(rows, rect.y + rect.h);
      });
      board.style.gridTemplateColumns = `repeat(${columns}, ${UNIT}px)`;
      board.style.minHeight = `${rows * UNIT}px`;
    }
    function commitPositions(priority) {
      render(priority);
      // Save placements only on explicit edits, never on viewport changes.
      IDS.forEach(id => { preferences.cards[id].x = rectangles[id].x; preferences.cards[id].y = rectangles[id].y; });
      persist(); render();
    }
    document.getElementById('edit-board').addEventListener('click', (event) => {
      editing = !editing; board.classList.toggle('editing', editing);
      event.currentTarget.setAttribute('aria-pressed', String(editing));
      event.currentTarget.textContent = editing ? '完成調整' : '調整卡片';
      document.getElementById('reset-board').hidden = document.getElementById('board-help').hidden = !editing;
    });
    document.getElementById('reset-board').addEventListener('click', () => {
      preferences = normalize({ mode: preferences.mode }); persist(); render(); announce('已還原卡片排列，公司資料保持不變。');
    });
    document.getElementById('settings-entry-mode').addEventListener('change', (event) => {
      preferences.mode = event.target.value === 'manual' ? 'manual' : 'ai'; applyMode(); persist();
    });
    board.addEventListener('click', (event) => {
      const control = event.target.closest('[data-board-action]'); if (!control) return;
      const id = control.closest('[data-widget]').dataset.widget, card = preferences.cards[id];
      const action = control.dataset.boardAction;
      if (action === 'collapse') { card.collapsed = !card.collapsed; persist(); render(); }
      if (editing && ['smaller', 'larger'].includes(action)) {
        const amount = action === 'larger' ? 2 : -2;
        card.w = Math.max(6, Math.min(48, rectangles[id].w + amount));
        card.h = Math.max(4, Math.min(32, card.h + amount)); card.collapsed = false; commitPositions(id);
      }
    });
    board.addEventListener('pointerdown', (event) => {
      const control = event.target.closest('[data-board-action]');
      if (!editing || event.button !== 0 || !control || !['move', 'resize'].includes(control.dataset.boardAction)) return;
      const id = control.closest('[data-widget]').dataset.widget;
      pointer = { id, control, action: control.dataset.boardAction, x: event.clientX, y: event.clientY,
        before: JSON.parse(JSON.stringify(preferences)), rect: { ...rectangles[id] } };
      control.setPointerCapture(event.pointerId); event.preventDefault(); board.classList.add('adjusting');
    });
    board.addEventListener('pointermove', (event) => {
      if (!pointer) return;
      const card = preferences.cards[pointer.id], dx = Math.round((event.clientX - pointer.x) / UNIT), dy = Math.round((event.clientY - pointer.y) / UNIT);
      if (pointer.action === 'resize') {
        card.w = Math.max(6, Math.min(48, pointer.rect.w + dx)); card.h = Math.max(4, Math.min(32, pointer.rect.h + dy)); card.collapsed = false;
      } else {
        card.x = Math.max(0, Math.min(72, pointer.rect.x + dx)); card.y = Math.max(0, Math.min(160, pointer.rect.y + dy));
      }
      render(pointer.id);
    });
    const endPointer = (cancel) => {
      if (!pointer) return;
      if (cancel) { preferences = pointer.before; render(); }
      else { commitPositions(pointer.id); announce('卡片位置及大小已儲存。'); }
      pointer = null; board.classList.remove('adjusting');
    };
    board.addEventListener('pointerup', () => endPointer(false));
    board.addEventListener('pointercancel', () => endPointer(true));
    board.addEventListener('lostpointercapture', () => endPointer(true));
    board.addEventListener('keydown', (event) => {
      const control = event.target.closest('[data-board-action]');
      if (event.key === 'Escape' && pointer) { endPointer(true); return; }
      if (!editing || !control || !['move', 'resize'].includes(control.dataset.boardAction) || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const id = control.closest('[data-widget]').dataset.widget, card = preferences.cards[id], rect = rectangles[id];
      const dx = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
      const dy = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
      if (control.dataset.boardAction === 'resize') {
        card.w = Math.max(6, Math.min(48, rect.w + dx)); card.h = Math.max(4, Math.min(32, rect.h + dy)); card.collapsed = false;
      } else { card.x = Math.max(0, rect.x + dx); card.y = Math.max(0, rect.y + dy); }
      commitPositions(id); announce('卡片已按格仔調整。');
    });
    async function sync(manual = false) {
      if (syncing || pointer || document.hidden || document.querySelector('dialog[open]') || isBusy() || editing) {
        if (manual) status.textContent = '完成目前編輯後會繼續同步';
        return;
      }
      syncing = true;
      try {
        if (await refresh() === false) return;
        status.textContent = `資料已同步 · ${new Date().toLocaleTimeString('zh-HK', { hour: '2-digit', minute: '2-digit' })}`;
        status.dataset.state = 'ready';
      } catch {
        status.textContent = '暫時無法同步，稍後會再試'; status.dataset.state = 'error';
      } finally { syncing = false; }
    }
    document.getElementById('refresh-workspace').addEventListener('click', () => sync(true));
    const schedule = async () => { await sync(); timer = setTimeout(schedule, 10000); };
    document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
    window.addEventListener('focus', () => sync());
    window.addEventListener('pagehide', () => clearTimeout(timer));
    window.addEventListener('pageshow', (event) => { if (event.persisted) { clearTimeout(timer); schedule(); } });
    const observer = new ResizeObserver(() => { if (!pointer) render(); }); observer.observe(board);
    applyMode(); render(); schedule();
  }
  const api = { normalize, arrange, overlaps, start, KEY };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BlueprintWorkspace = api;
})(globalThis);
