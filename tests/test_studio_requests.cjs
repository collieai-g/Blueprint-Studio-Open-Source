'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the real studio controller against a small DOM and authenticated API fixture.
// Requests and displayed results are observed through its public navigation and buttons.
function fixture({initial = 'projects', focus = [], intercept} = {}) {
  const nodes = new Map(), calls = [], notices = [];
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
      this.attributes = new Map(); this.listeners = new Map(); this._text = '';
      this.hidden = false; this.value = ''; this.checked = false;
      const classes = new Set();
      this.classList = {
        add: (...values) => values.forEach(value => classes.add(value)),
        remove: (...values) => values.forEach(value => classes.delete(value)),
        contains: value => classes.has(value),
        toggle(value, force) { const add = force === undefined ? !classes.has(value) : force; if (add) classes.add(value); else classes.delete(value); return add; }
      };
      Object.defineProperty(this, 'className', {set(value) { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(value => classes.add(value)); }});
    }
    set id(value) { this._id = value; nodes.set(value, this); }
    get id() { return this._id; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    append(...children) { children.forEach(child => { child.parentElement = this; this.children.push(child); }); }
    prepend(...children) { children.forEach(child => { child.parentElement = this; }); this.children.unshift(...children); }
    replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
    replaceWith(other) { const index = this.parentElement.children.indexOf(this); this.parentElement.children[index] = other; other.parentElement = this.parentElement; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    removeAttribute(key) { this.attributes.delete(key); }
    addEventListener(type, run) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(run); }
    async emit(type, extra = {}) { for (const run of this.listeners.get(type) || []) await run({currentTarget: this, preventDefault() {}, ...extra}); }
    querySelectorAll(selector) {
      const match = element => selector === 'button' ? element.tagName === 'BUTTON' : selector.startsWith('.') ? element.classList.contains(selector.slice(1)) : /^\[data-/.test(selector) && (() => {
        const [, key, value] = selector.match(/^\[data-([\w-]+)(?:=([^\]]+))?\]$/) || [];
        const field = key?.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        return field && Object.hasOwn(element.dataset, field) && (value === undefined || element.dataset[field] === value);
      })();
      const all = [];
      for (const child of this.children) { if (match(child)) all.push(child); all.push(...child.querySelectorAll(selector)); }
      return all;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    get options() { return this.children.filter(child => child.tagName === 'OPTION'); }
    get selectedOptions() { return this.options.filter(child => child.selected); }
    close() { this.open = false; }
  }
  const get = id => { if (!nodes.has(id)) { const element = new Element(); element.id = id; } return nodes.get(id); };
  const document = {
    body: new Element('body'), createElement: tag => new Element(tag), getElementById: get,
    querySelector: selector => get(selector), querySelectorAll: () => [], addEventListener() {}
  };
  const records = new Map([
    ['project-1', {id: 'project-1', kind: 'project', title: '目前項目', version: 1, data: {progress: 20}, refs: []}],
    ['task-1', {id: 'task-1', kind: 'task', title: '目前待辦', version: 1, data: {done: false}, refs: []}],
    ['inbox-1', {id: 'inbox-1', kind: 'inbox', title: '選定收件項目', version: 1, data: {}, refs: []}]
  ]);
  const schemas = Object.fromEntries(['project', 'task', 'inbox', 'report', 'specialist', 'inspiration'].map(kind => [kind, {label: kind, fields: {}}]));
  const state = {
    identity: {role: 'owner'}, schemas, catalog: [records.get('project-1')], templates: [],
    preferences: {version: 1, data: {focus, pins: [], workspaces: [], groups: [], notifications: true}},
    summary: {counts: {}, reminders: [], specialists: [], finance: {}}
  };
  const app = {
    async api(path, options = {}) {
      calls.push({path, options});
      if (intercept) { const result = intercept(path, options); if (result !== undefined) return result; }
      if (path === '/api/studio/state') return structuredClone(state);
      const url = new URL(path, 'https://fixture.invalid');
      if (url.pathname === '/api/studio/records') {
        const kind = url.searchParams.get('kind');
        const visible = [...records.values()].filter(record => !kind || record.kind === kind);
        return {records: structuredClone(visible), total: visible.length};
      }
      const match = /^\/api\/studio\/records\/([^/]+)(\/context)?$/.exec(path);
      if (match) {
        const record = records.get(match[1]); assert.ok(record, path);
        if (options.method === 'PATCH') { assert.equal(options.body.expected_version, record.version); Object.assign(record.data, options.body.data); record.version++; }
        return structuredClone(match[2] ? {record, sources: []} : {record});
      }
      if (path.startsWith('/api/items?')) return {items: [{kind: 'file', id: 'file-1', title: '可選文件'}]};
      if (path.startsWith('/api/workdays?')) return {workdays: []};
      if (path.startsWith('/api/todos?')) return {todos: []};
      throw new Error('Unexpected request: ' + path);
    },
    notice: (text, error) => notices.push({text, error}), icon: () => new Element('svg'),
    openDialog: id => { get(id).open = true; }, closeSidebar() {}, isBusy: () => false,
    setView(view) { get('dashboard-panel').hidden = view !== 'dashboard'; },
    hideLegacy() { get('dashboard-panel').hidden = true; }
  };
  const context = {document, URLSearchParams, location: {hash: '#/' + initial, search: '', protocol: 'http:', origin: 'https://fixture.invalid'}, navigator: {}, history: {replaceState() {}, pushState() {}}, window: {addEventListener() {}}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../library_web/navigation.js'), 'utf8'), context);
  vm.runInNewContext(fs.readFileSync(require.resolve('../library_web/studio.js'), 'utf8'), context);
  const studio = context.BlueprintStudio;
  const findButton = (id, title) => { const button = get(id).querySelectorAll('button').find(button => button.textContent === title); assert.ok(button, title); return button; };
  return {studio, app, get, calls, records, state, notices, findButton, start: () => studio.init(app)};
}

test('booting a work page requests only its state and visible list', async () => {
  const f = fixture(); await f.start();
  assert.equal(f.studio.isReady(), true);
  assert.deepEqual(f.calls.map(call => call.path), ['/api/studio/state', '/api/studio/records?kind=project&category=&archived=0&q=&offset=0&limit=30']);
  assert.match(f.get('studio-panel').textContent, /目前項目/);
  f.calls.length = 0;
  await f.get('.workspace-label').emit('click');
  assert.equal(f.get('studio-action-dialog').open, true);
  assert.equal(f.calls.length, 0, 'plain workspace fields do not fetch unrelated reference sources');
});

test('focus options load once and only request studio records', async () => {
  const f = fixture({initial: 'overview'}); await f.start(); f.calls.length = 0;
  await f.findButton('studio-focus', '設定今日焦點').emit('click');
  assert.deepEqual(f.calls.map(call => call.path), ['/api/studio/records?limit=100']);
  assert.equal(f.get('studio-focus-options').options.length, 3);
  assert.equal(f.get('studio-action-dialog').open, true);
});

test('reference-capable editors still fetch their authorized sources when opened', async () => {
  const f = fixture(); await f.start(); f.calls.length = 0;
  await f.findButton('studio-panel', '新增project').emit('click');
  assert.equal(f.calls.length, 4);
  assert.ok(f.calls.some(call => call.path === '/api/items?limit=200'));
  assert.equal(f.get('studio-editor').open, true);
  assert.ok(f.get('studio-refs').options.some(option => option.textContent.includes('可選文件')));
});

test('record details use one authorized context read and discard an obsolete navigation result', async () => {
  let finish;
  const f = fixture({intercept: path => path === '/api/studio/records/inbox-1/context' ? new Promise(resolve => { finish = resolve; }) : undefined});
  await f.start(); f.calls.length = 0;
  await f.studio.openRecord('project-1');
  assert.deepEqual(f.calls.map(call => call.path), ['/api/studio/records/project-1/context']);
  const oldNavigation = f.studio.openRecord('inbox-1');
  await f.studio.navigate('projects');
  finish({record: f.records.get('inbox-1'), sources: []}); await oldNavigation;
  assert.match(f.get('studio-panel').textContent, /目前項目/);
  assert.doesNotMatch(f.get('studio-panel').textContent, /選定收件項目/);
});

test('document, workday and todo pages avoid hidden dashboard task requests', async () => {
  const f = fixture(); await f.start();
  for (const [route, expected] of [['documents', 0], ['overview/workdays', 0], ['overview/todos', 1]]) {
    f.calls.length = 0; await f.studio.navigate(route);
    assert.equal(f.calls.filter(call => call.path === '/api/studio/records?kind=task&limit=100').length, expected, route);
  }
});

test('an overview response cannot update the hidden dashboard after a later navigation', async () => {
  let finish;
  const f = fixture({intercept: path => path === '/api/studio/records?kind=task&limit=100' ? new Promise(resolve => { finish = resolve; }) : undefined});
  await f.start();
  f.get('studio-dashboard-tasks').textContent = 'existing dashboard';
  const overview = f.studio.navigate('overview');
  await f.studio.navigate('projects');
  finish({records: [f.records.get('task-1')]}); await overview;
  assert.equal(f.get('studio-dashboard-tasks').textContent, 'existing dashboard');
  assert.match(f.get('studio-panel').textContent, /目前項目/);
});

test('explicit refresh shows new data and a completed overview task without duplicate task reads', async () => {
  const f = fixture({initial: 'overview', focus: [{kind: 'inbox', id: 'inbox-1'}]}); await f.start();
  assert.match(f.get('studio-focus').textContent, /選定收件項目/);
  f.calls.length = 0;
  await f.get('studio-dashboard-tasks').children[0].children[0].emit('change');
  assert.equal(f.records.get('task-1').data.done, true);
  assert.equal(f.get('studio-dashboard-tasks').children.length, 0);
  assert.equal(f.calls.filter(call => call.path === '/api/studio/records?kind=task&limit=100').length, 1);
  f.records.set('task-2', {id: 'task-2', kind: 'task', title: '工具剛保存的待辦', version: 1, data: {done: false}, refs: []});
  await f.studio.refreshVisible();
  assert.match(f.get('studio-dashboard-tasks').textContent, /工具剛保存的待辦/);
  assert.equal(f.notices.filter(notice => notice.error).length, 0);
});

test('capture work is requested explicitly and its existing command is handed back to the AI', async () => {
  let f;
  f = fixture({intercept(path, options) {
    if (path !== '/api/studio/records/inspiration-1/actions') return undefined;
    assert.equal(options.body.action, 'capture_request');
    assert.deepEqual(JSON.parse(JSON.stringify(options.body.arguments)), {});
    const command = f.records.get('capture-command') || {id: 'capture-command', kind: 'command', version: 1, title: '擷取工作', data: {instruction: '讀取已選來源並擷取截圖'}, refs: []};
    f.records.set(command.id, command);
    return {created: [command]};
  }});
  f.records.set('inspiration-1', {id: 'inspiration-1', kind: 'inspiration', title: '網站參考', version: 1, data: {}, refs: []});
  await f.start(); await f.studio.openRecord('inspiration-1');
  assert.equal(f.records.has('capture-command'), false, 'viewing a saved reference does not queue a capture');
  assert.match(f.get('studio-panel').textContent, /可按「擷取截圖與摘要」建立工作/);
  for (let attempt = 0; attempt < 2; attempt++) {
    await f.findButton('studio-panel', '擷取截圖與摘要').emit('click');
    assert.equal(f.get('studio-handoff').open, true);
    assert.match(f.get('studio-handoff-text').value, /指令編號：capture-command/);
    f.get('studio-handoff').close();
  }
  assert.equal(f.records.get('capture-command').version, 1);
  assert.equal(f.notices.filter(notice => notice.error).length, 0);
});

test('optional specialist presets remain empty until the user selects one', async () => {
  let f;
  f = fixture({intercept(path, options) {
    if (path !== '/api/studio/templates') return undefined;
    assert.equal(options.body.template_id, 'preset:website');
    const record = {id: 'new-specialist', kind: 'specialist', title: '網站客服 AI', version: 1, data: {role: '處理網站查詢', state: 'unconnected'}, refs: []};
    f.records.set(record.id, record); f.state.catalog.push(record);
    return {record};
  }});
  f.state.specialist_presets = [{id: 'preset:website', title: '網站客服 AI', role: '處理網站查詢', rules: ''}];
  await f.start(); await f.studio.navigate('templates');
  assert.equal([...f.records.values()].filter(record => record.kind === 'specialist').length, 0);
  await f.findButton('studio-panel', '使用範本').emit('click');
  assert.equal([...f.records.values()].filter(record => record.kind === 'specialist').length, 1);
  assert.match(f.get('studio-panel').textContent, /網站客服 AI/);
  assert.match(f.get('studio-panel').textContent, /尚未接入對外執行服務/);
  assert.equal(f.notices.filter(notice => notice.error).length, 0);
});
