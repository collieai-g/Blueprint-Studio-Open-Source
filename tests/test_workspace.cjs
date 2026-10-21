'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { normalize, arrange, overlaps } = require('../library_web/workspace.js');

test('corrupt browser preferences cannot hide cards or create unbounded geometry', () => {
  const value = normalize({ version: 1, mode: 'injected', cards: { files: { x: -12, y: Infinity, w: 100000, h: -2, collapsed: 'false' }, unknown: { w: 1 } } });
  assert.equal(value.mode, 'ai');
  assert.equal(Object.keys(value.cards).length, 6);
  assert.deepEqual(value.cards.files, { x: 0, y: 0, w: 48, h: 4, collapsed: false });
  assert.deepEqual(normalize(null), normalize({ version: 99 }));
});

test('colliding cards fit mobile and desktop without changing saved desktop sizes', () => {
  const value = normalize(null);
  value.cards.files.w = 36;
  for (const columns of [6, 13, 30, 44]) {
    const rects = Object.values(arrange(value, columns, 'files'));
    for (let i = 0; i < rects.length; i++) {
      assert.ok(rects[i].x >= 0 && rects[i].x + rects[i].w <= columns);
      for (let j = i + 1; j < rects.length; j++) assert.equal(overlaps(rects[i], rects[j]), false);
    }
  }
  assert.equal(value.cards.files.w, 36);
});

test('dragged card has priority; collapsing retains expanded height for reload', () => {
  const value = normalize(null);
  value.cards.todos = { x: 0, y: 0, w: 12, h: 20, collapsed: true };
  const layout = arrange(value, 36, 'todos');
  assert.equal(layout.todos.y, 0);
  assert.equal(layout.todos.h, 4);
  assert.ok(layout.files.y >= 4);
  const reloaded = normalize(JSON.parse(JSON.stringify(value)));
  reloaded.cards.todos.collapsed = false;
  assert.equal(arrange(reloaded, 36).todos.h, 20);
});
