'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {describe, applyPayload, createDraftGuard, createClient, workspaceBusy} = require('../library_web/installer.js');

const ready = () => ({supported: true, version: 7, phase: 'ready', current: {version: '2.10.0'},
  prepared: {version: '2.10.1', release_id: 'release-101', notes: '改善工作流程'}, can_apply: true});
const clean = {reason: () => ''};

test('only supported and confirmed ready state permits owner apply', () => {
  assert.equal(describe(ready(), 'owner').canApply, true);
  assert.equal(describe(ready(), 'admin').canApply, false);
  assert.equal(describe(ready(), 'member').visible, false);
  assert.equal(describe({...ready(), supported: false}, 'owner').visible, false);
  for (const patch of [{phase: 'future'}, {version: NaN}, {version: -1}, {can_apply: 'true'}, {prepared: {version: '2.10.1', release_id: '../foreign'}}]) {
    assert.equal(describe({...ready(), ...patch}, 'owner').canApply, false);
  }
  assert.equal(describe({...ready(), phase: 'preparing'}, 'owner').canApply, false);
});

test('apply carries exact release and CAS version and fails closed for drafts', () => {
  assert.deepEqual(applyPayload(ready(), clean), {release_id: 'release-101', expected_version: 7, drafts_saved: true});
  assert.throws(() => applyPayload(ready(), {reason: () => '未保存工作'}), /未保存/);
  assert.throws(() => applyPayload({...ready(), phase: 'failed'}, clean), /尚未準備好/);
});

test('one confirmation shows the exact affected component versions', () => {
  const status = {...ready(), plan: [
    {component_id: 'blueprint-core', from: '2.10.0', to: '2.10.1'},
    {component_id: 'bluesai-cs-0001', from: '0.4.0-preview.2', to: '0.4.0-preview.3'},
    {component_id: 'missing-version', from: '1'}, null]};
  assert.deepEqual(describe(status, 'owner').components, [
    {name: '藍圖工作室', from: '2.10.0', to: '2.10.1'},
    {name: '客服 AI', from: '0.4.0-preview.2', to: '0.4.0-preview.3'}]);
  assert.equal(applyPayload(status, clean).release_id, 'release-101');
});

test('supervisor queued and recovery phases cannot be presented as installable releases', () => {
  const installed = describe({...ready(), phase: 'installed'}, 'owner');
  assert.equal(installed.visible, false); assert.equal(installed.canApply, false);
  const queued = describe({...ready(), phase: 'apply_requested'}, 'owner');
  assert.match(queued.label, /等待/); assert.equal(queued.busy, true); assert.equal(queued.canApply, false);
  for (const phase of ['prepare_failed', 'recovery_required']) {
    const view = describe({...ready(), phase}, 'owner');
    assert.equal(view.visible, true); assert.equal(view.busy, false); assert.equal(view.canApply, false);
    assert.notEqual(view.label, '更新狀態未能確認');
  }
});

test('draft guard never treats a form submit or reset as confirmed server save', () => {
  const listeners = new Map();
  const doc = {activeElement: null, querySelector: () => null,
    addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name)};
  const guard = createDraftGuard(doc);
  assert.equal(guard.reason(), '');
  const installerControl = {closest: () => ({}), matches: () => true};
  listeners.get('input')({target: installerControl}); assert.equal(guard.reason(), '');
  const businessField = {closest: () => null, matches: () => true};
  listeners.get('change')({target: businessField}); assert.match(guard.reason(), /重新開啟/);
  assert.equal(listeners.has('submit'), false); assert.equal(listeners.has('reset'), false);
  guard.stop(); assert.equal(listeners.size, 0);
});

test('busy work and focused fields block even without a new edit event', () => {
  const doc = {activeElement: null, querySelector: () => null, addEventListener() {}, removeEventListener() {}};
  let busy = true;
  const guard = createDraftGuard(doc, () => busy);
  assert.match(guard.reason(), /完成並保存/);
  busy = false; doc.activeElement = {closest: () => null, matches: () => true};
  assert.match(guard.reason(), /完成並保存/);
  doc.activeElement = null; assert.equal(guard.reason(), '');
});

test('synced presentation with an empty pending list permits update checks', () => {
  const environment = {BlueprintStudio: {presentation: () => ({status: () => ({state: 'synced', pending: []})})}};
  assert.equal(workspaceBusy(environment), false);
  environment.BlueprintStudio.presentation = () => ({status: () => ({state: 'offline', pending: ['layout']})});
  assert.equal(workspaceBusy(environment), true);
  assert.equal(workspaceBusy({BlueprintAgents: {isBusy: () => true}}), true);
});

test('metadata polling cannot issue company data reads or arbitrary update paths', async () => {
  const calls = [];
  const client = createClient(async (...args) => { calls.push(args); return {ok: true, json: async () => ready()}; }, {username: '擁有人'});
  await client(); await client('/check', {}); await client('/apply', applyPayload(ready(), clean));
  assert.deepEqual(calls.map(([path]) => path), ['/api/installer', '/api/installer/check', '/api/installer/apply']);
  assert.equal(calls[0][1].headers['X-Blueprint-Account'], encodeURIComponent('擁有人'));
  assert.equal(calls[1][1].headers['X-Blueprint-Request'], '1');
  assert.equal(calls[2][1].credentials, 'same-origin');
  assert.equal(JSON.parse(calls[2][1].body).expected_version, 7);
  await assert.rejects(client('/api/items'), /不正確/);
});

test('client preserves server account conflicts and errors instead of fabricating readiness', async () => {
  const client = createClient(async () => ({ok: false, status: 409, json: async () => ({error: '登入帳戶已改變'})}), {username: 'owner'});
  await assert.rejects(client(), error => error.status === 409 && /帳戶已改變/.test(error.message));
  const broken = createClient(async () => ({ok: true, json: async () => {throw new Error('bad json');}}), {username: 'owner'});
  await assert.rejects(broken(), /回應未能讀取/);
  assert.throws(() => createClient(async () => {}, {}), /登入帳戶/);
});
