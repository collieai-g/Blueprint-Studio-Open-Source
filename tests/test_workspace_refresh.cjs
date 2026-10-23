'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function target() {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    async emit(type, values = {}) {
      for (const listener of listeners.get(type) || []) await listener({ currentTarget: this, ...values });
    }
  };
}

// Exercise the real workspace start handler without requiring a browser or a network.
function fixture() {
  const nodes = new Map(), timers = [], window = target();
  function node() {
    return Object.assign(target(), {
      dataset: {}, style: { setProperty() {} }, classList: { toggle() {}, add() {}, remove() {} },
      clientWidth: 1032, parentElement: {},
      append() {}, replaceChildren() {}, setAttribute() {},
      querySelectorAll: () => [], querySelector: () => null,
      getBoundingClientRect: () => ({ left: 0, top: 0 })
    });
  }
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  };
  const document = Object.assign(target(), {
    hidden: false, body: node(), getElementById: get, createElement: node,
    querySelectorAll: () => [], querySelector: selector => selector === '.app-main' ? get('app-main') : null
  });
  const context = {
    module: { exports: {} }, window, document, structuredClone,
    localStorage: { getItem: () => null, setItem() {} },
    ResizeObserver: class { observe() {} },
    setTimeout: (...args) => { timers.push(args); return timers.length; }, clearTimeout() {},
    requestAnimationFrame() {}, cancelAnimationFrame() {}
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../library_web/workspace.js'), 'utf8'), context);
  return { workspace: context.module.exports, window, document, get, timers };
}

test('initial render and passive browser events leave company data and the current page alone', async () => {
  const f = fixture(); let refreshes = 0;
  f.workspace.start({ isBusy: () => false, refresh: async () => { refreshes++; } });
  assert.equal(f.get('workspace-sync').dataset.state, 'ready');
  assert.equal(refreshes, 0);
  assert.equal(f.timers.length, 0, 'workspace must not schedule background refreshes');
  await f.window.emit('focus');
  await f.document.emit('visibilitychange');
  await f.window.emit('pagehide');
  await f.window.emit('pageshow', { persisted: true });
  assert.equal(refreshes, 0);
  assert.equal(f.timers.length, 0, 'history restoration must not restart a polling loop');
});

test('manual refresh stays available, prevents overlapping requests and preserves active edits', async () => {
  const f = fixture(); let refreshes = 0, busy = true, finish;
  f.workspace.start({ isBusy: () => busy, refresh: () => { refreshes++; return new Promise(resolve => { finish = resolve; }); } });
  await f.get('refresh-workspace').emit('click');
  assert.equal(refreshes, 0);
  assert.match(f.get('workspace-sync').textContent, /完成編輯/);
  busy = false;
  const first = f.get('refresh-workspace').emit('click');
  await f.get('refresh-workspace').emit('click');
  assert.equal(refreshes, 1);
  finish(true); await first;
  assert.equal(f.get('workspace-sync').dataset.state, 'ready');
  assert.match(f.get('workspace-sync').textContent, /資料已更新/);
});

test('a failed manual refresh waits for another user action without scheduling retries', async () => {
  const f = fixture(); let refreshes = 0;
  f.workspace.start({ isBusy: () => false, refresh: async () => { refreshes++; throw new Error('offline'); } });
  await f.get('refresh-workspace').emit('click');
  assert.equal(refreshes, 1);
  assert.equal(f.get('workspace-sync').dataset.state, 'error');
  assert.match(f.get('workspace-sync').textContent, /請再按更新資料/);
  assert.equal(f.timers.length, 0);
});
