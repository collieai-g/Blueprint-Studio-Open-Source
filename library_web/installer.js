/* Managed installation status only. Company data remains under its existing APIs. */
'use strict';
((root) => {
  const ENDPOINT = '/api/installer';
  const POLL_MS = 15000;
  const PHASES = {
    idle: '目前沒有待套用的更新', installed: '目前沒有待套用的更新', checking: '正在檢查更新', preparing: '正在背景準備，可繼續工作',
    ready: '更新已準備好', applying: '正在套用更新', complete: '更新已完成',
    apply_requested: '等待套用更新', prepare_failed: '更新準備未完成', recovery_required: '更新需要恢復處理',
    failed: '更新未完成', blocked: '更新暫時無法套用'
  };
  const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

  function describe(value, role) {
    const allowed = ['owner', 'admin'].includes(role);
    if (!allowed || value?.supported !== true) return {visible: false, supported: false, canApply: false};
    const valid = Number.isSafeInteger(value.version) && value.version >= 0 && Object.hasOwn(PHASES, value.phase);
    const prepared = value.prepared;
    const ready = valid && value.phase === 'ready' && value.can_apply === true &&
      prepared && typeof prepared.release_id === 'string' && SAFE_ID.test(prepared.release_id) &&
      typeof prepared.version === 'string' && prepared.version.length <= 80;
    return {
      supported: true, visible: !valid || !['idle', 'installed'].includes(value.phase),
      canApply: role === 'owner' && !!ready,
      label: valid ? PHASES[value.phase] : '更新狀態未能確認',
      current: typeof value.current?.version === 'string' ? value.current.version.slice(0, 80) : '待核對',
      next: typeof prepared?.version === 'string' ? prepared.version.slice(0, 80) : '',
      notes: typeof prepared?.notes === 'string' ? prepared.notes.slice(0, 8000) : '',
      error: typeof value.error === 'string' ? value.error.slice(0, 1000) : '',
      components: Array.isArray(value.plan) ? value.plan.filter(item => item && typeof item.component_id === 'string' && typeof item.from === 'string' && typeof item.to === 'string').map(item => ({name: ({'blueprint-core': '藍圖工作室', 'bluesai-cs-0001': '客服 AI'})[item.component_id] || item.component_id.slice(0,80), from: item.from.slice(0,80), to: item.to.slice(0,80)})) : [],
      busy: valid && ['checking', 'preparing', 'apply_requested', 'applying'].includes(value.phase),
      complete: valid && value.phase === 'complete'
    };
  }

  function applyPayload(status, guard) {
    if (!describe(status, 'owner').canApply) throw new Error('更新尚未準備好，請重新核對狀態。');
    const problem = guard?.reason?.();
    if (problem) throw new Error(problem);
    return {release_id: status.prepared.release_id, expected_version: status.version, drafts_saved: true};
  }

  function editable(target) {
    return !!target && !target.closest?.('[data-installer-ui]') &&
      !!target.matches?.('textarea:not([readonly]), select, input:not([type=button]):not([type=submit]):not([type=hidden]):not([readonly]), [contenteditable=true]');
  }

  function createDraftGuard(doc, busy = () => false) {
    let edited = false;
    const track = event => { if (editable(event.target)) edited = true; };
    doc.addEventListener('input', track, true);
    doc.addEventListener('change', track, true);
    return {
      reason() {
        if (edited) return '此分頁曾修改內容。請先完成保存，並在確認工作已保存後重新開啟此頁，再套用更新。';
        if (busy() || editable(doc.activeElement) || doc.querySelector('dialog[open]:not([data-installer-ui]), .workspace-board.editing')) {
          return '請先完成並保存目前的工作，再關閉其他工作視窗及表單。';
        }
        return '';
      },
      stop() { doc.removeEventListener('input', track, true); doc.removeEventListener('change', track, true); }
    };
  }

  function createClient(fetcher, identity) {
    const username = identity?.username;
    if (typeof username !== 'string' || !username || username.length > 80) throw new Error('請重新確認登入帳戶。');
    const account = encodeURIComponent(username);
    return async (path = '', body) => {
      if (!['', '/check', '/apply'].includes(path)) throw new Error('更新操作不正確。');
      const options = {credentials: 'same-origin', cache: 'no-store', headers: {'X-Blueprint-Account': account}};
      if (body !== undefined) {
        options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
        options.headers['X-Blueprint-Request'] = '1'; options.body = JSON.stringify(body);
      }
      const response = await fetcher(ENDPOINT + path, options);
      let data;
      try { data = await response.json(); } catch { throw new Error('更新狀態回應未能讀取。'); }
      if (!response.ok) {
        const error = new Error(typeof data.error === 'string' ? data.error : '未能讀取或套用更新。');
        error.status = response.status; throw error;
      }
      return data;
    };
  }

  function workspaceBusy(environment) {
    if (['BlueprintAgents', 'BlueprintCalendar', 'BlueprintSkills', 'BlueprintIntegrations', 'BlueprintWorkdays'].some(name => environment[name]?.isBusy?.()) ||
        environment.BlueprintStudio?.isLayoutBusy?.()) return true;
    const presentation = environment.BlueprintStudio?.presentation?.()?.status?.();
    return !!(presentation && ((Array.isArray(presentation.pending) && presentation.pending.length > 0) ||
      ['starting', 'saving', 'conflict', 'diverged'].includes(presentation.state)));
  }

  async function mount(doc = root.document) {
    const find = id => doc.getElementById(id);
    const entry = find('installer-entry'), dialog = find('installer-dialog');
    if (!entry || !dialog) return;
    let status = null, role = '', request, timer, stopped = false, pending = false, generation = 0;
    const guard = createDraftGuard(doc, () => workspaceBusy(root));
    function error(problem) {
      const node = find('installer-error'); node.textContent = problem?.message || ''; node.hidden = !problem;
    }
    function render() {
      const view = describe(status, role);
      entry.hidden = !view.visible;
      if (!view.supported) {
        find('installer-apply').disabled = true;
        find('installer-check').disabled = true;
        find('installer-reload').hidden = true;
        return;
      }
      find('installer-entry-label').textContent = view.label;
      find('installer-status').textContent = view.label;
      find('installer-versions').textContent = `目前版本：${view.current}${view.next ? `；準備版本：${view.next}` : ''}`;
      const components = find('installer-components');
      if (components) {
        components.replaceChildren();
        for (const item of view.components) {
          const row = doc.createElement('li'); row.textContent = `${item.name}：${item.from} → ${item.to}`;
          components.appendChild(row);
        }
        components.hidden = view.components.length === 0;
      }
      find('installer-notes').textContent = view.notes || '詳細更新內容由已驗證的發行套件提供。';
      find('installer-drafts').textContent = guard.reason() || '套用前請確認工作已保存，並關閉其他已保存的工作分頁。';
      find('installer-apply').disabled = pending || !view.canApply || !!guard.reason();
      find('installer-check').disabled = pending || view.busy || role !== 'owner';
      find('installer-reload').hidden = !view.complete;
      find('installer-reload').disabled = !!guard.reason();
      find('installer-owner-note').hidden = role === 'owner';
      if (view.error) error(new Error(view.error));
    }
    async function refresh() {
      if (stopped || pending || !request || doc.visibilityState === 'hidden') return;
      const sequence = ++generation;
      try {
        const value = await request();
        if (sequence !== generation || stopped) return;
        status = value; error(null); render();
        if (value.supported !== true) { stopped = true; clearTimeout(timer); }
      } catch (problem) {
        if (sequence !== generation || stopped) return;
        if ([401, 403, 404, 409].includes(problem.status)) {
          stopped = true; status = null; clearTimeout(timer); error(problem); render(); return;
        }
        error(problem); if (status) { status = {...status, can_apply: false}; render(); }
      }
    }
    function schedule() {
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(async () => { await refresh(); schedule(); }, POLL_MS);
    }
    async function act(path, payload) {
      if (pending || stopped) return;
      pending = true; ++generation; error(null); render();
      try { status = await request(path, payload); }
      catch (problem) { error(problem); status = status ? {...status, can_apply: false} : status; }
      finally { pending = false; render(); }
      await refresh();
    }
    entry.addEventListener('click', () => { render(); if (!dialog.open) dialog.showModal(); });
    find('installer-close').addEventListener('click', () => dialog.close());
    find('installer-later').addEventListener('click', () => dialog.close());
    find('installer-check').addEventListener('click', () => act('/check', {}));
    find('installer-apply').addEventListener('click', () => {
      try { if (role !== 'owner') throw new Error('請由工作室擁有人套用更新。'); return act('/apply', applyPayload(status, guard)); }
      catch (problem) { error(problem); render(); }
    });
    find('installer-reload').addEventListener('click', () => {
      const problem = guard.reason(); if (problem) { error(new Error(problem)); return; }
      root.location.reload();
    });
    doc.addEventListener('input', () => { if (dialog.open) render(); }, true);
    doc.addEventListener('change', () => { if (dialog.open) render(); }, true);
    doc.addEventListener('visibilitychange', () => { if (doc.visibilityState !== 'hidden') refresh(); });
    root.addEventListener?.('pagehide', () => { stopped = true; ++generation; clearTimeout(timer); guard.stop(); }, {once: true});
    try {
      // One startup identity read; subsequent checks read update metadata only.
      const response = await root.fetch('/api/info', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) { guard.stop(); return; }
      const info = await response.json(); role = info.account?.role;
      if (!['owner', 'admin'].includes(role)) { guard.stop(); return; }
      request = createClient(root.fetch.bind(root), info.account);
      await refresh(); schedule();
    } catch (problem) { error(problem); }
  }

  const api = {ENDPOINT, POLL_MS, describe, applyPayload, createDraftGuard, createClient, workspaceBusy, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    root.BlueprintInstaller = api;
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => mount(), {once: true});
    else mount();
  }
})(globalThis);
