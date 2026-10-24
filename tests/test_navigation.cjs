'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const sandbox = { module: { exports: {} } };
runInNewContext(readFileSync(join(__dirname, '../library_web/navigation.js'), 'utf8'), sandbox);
const { groups, pages, aliases } = JSON.parse(JSON.stringify(sandbox.module.exports));

test('the workspace has four stable primary entries with compact core navigation', () => {
  assert.deepEqual(groups.map(group => group.label), ['總覽', '工作', 'AI', '資料']);
  assert.equal(new Set(groups.map(group => group.route)).size, 4);
  for (const group of groups) {
    assert.equal(pages[group.route].group, group.id);
    assert.ok(group.items.length <= (group.id === 'inspiration' ? 5 : 4));
  }
  const visible = new Set(groups.flatMap(group => group.items.map(item => item[0])));
  for (const route of ['overview', 'projects', 'inbox', 'overview/todos', 'overview/workdays', 'skills', 'agents', 'command', 'documents', 'inspirations', 'topics', 'pinned', 'archived']) {
    assert.ok(visible.has(route), `${route} remains reachable from a primary entry`);
  }
  for (const route of ['revenue', 'costs', 'team', 'customers', 'specialists', 'specialist/specialist-website', 'inspirations/web']) {
    assert.ok(!visible.has(route), `${route} does not clutter the main navigation`);
    assert.ok(pages[route], `${route} keeps its existing URL`);
  }
});

test('archived material remains reachable as the last data tab without another primary entry', () => {
  const data = groups.find(group => group.id === 'inspiration');
  assert.deepEqual(data.items.map(item => item[0]), ['documents', 'inspirations', 'topics', 'pinned', 'archived']);
  assert.deepEqual(data.items.map(item => item[1]), ['文件', '收藏', '主題', '釘選', '已封存']);
  assert.equal(pages.archived.group, data.id);
  assert.equal(aliases.archived, 'archived');
});

test('legacy bookmarks resolve and restricted page definitions retain permissions', () => {
  for (const target of Object.values(aliases)) assert.ok(pages[target], `legacy bookmark resolves to ${target}`);
  for (const route of ['revenue', 'costs', 'team', 'connections', 'templates']) assert.equal(pages[route].access, 'admin');
  for (const page of Object.values(pages)) assert.ok(groups.some(group => group.id === page.group), `${page.route} has a current primary entry`);
});
