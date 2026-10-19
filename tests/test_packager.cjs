/* Run with: node --test tests/test_packager.cjs (Node 24+).
 * Fixtures below were produced independently with Python's zipfile module.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

global.window = global;
global.BlueprintIcons = Object.fromEntries(
  ['general', 'chat', 'web', 'finance', 'document', 'calendar', 'automation', 'research']
    .map(kind => [kind, `<svg xmlns="http://www.w3.org/2000/svg"><title>${kind}</title></svg>`])
);
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '../web/packager.js'), 'utf8'), {
  filename: 'packager.js',
});
const { getZipBlob, importZip } = global.BlueprintPackage;

const fixture = {
  deflated: 'UEsDBBQAAAAIANsqU126pm+gewAAAIoAAAAeAAAAZml4dHVyZS8uYmx1ZXByaW50L2ZvbGRlci5qc29uq1bKTFGyUlBKy6woKS1K1QXydBSU8hJzU0GiTxdPfr5j3bMda16sXAYST0ktTi7KLCjJzM8DSYOEShLTi4HsaKWnuxc/m7vxxcqNSrFA4eTEktT0/KJKkLL01LzUosQckOrUipKiRKBYtVJBUWpxalEZyJqSotLU2loAUEsDBBQAAAAIANsqU10wgktiEgAAAA8AAAARAAAAZml4dHVyZS9BR0VOVFMubWRTVnjaN//ZnM5nPe1P90zlAgBQSwMEFAAACAgA2ypTXePmARwUAAAABAEAAB0AAABmaXh0dXJlL+izh+aWmS/kvaDlpb3wn5GLLnR4dHuyY+2zae1PW5c/XbeT68nI5AAAUEsDBBQAAAAIANsqU10t2wngBwAAAAUAAAATAAAAZml4dHVyZS9wYXlsb2FkLmJpbmP438DIAABQSwECFAMUAAAACADbKlNduqZvoHsAAACKAAAAHgAAAAAAAAAAAAAAgAEAAAAAZml4dHVyZS8uYmx1ZXByaW50L2ZvbGRlci5qc29uUEsBAhQDFAAAAAgA2ypTXTCCS2ISAAAADwAAABEAAAAAAAAAAAAAAIABtwAAAGZpeHR1cmUvQUdFTlRTLm1kUEsBAhQDFAAACAgA2ypTXePmARwUAAAABAEAAB0AAAAAAAAAAAAAAIAB+AAAAGZpeHR1cmUv6LOH5paZL+S9oOWlvfCfkYsudHh0UEsBAhQDFAAAAAgA2ypTXS3bCeAHAAAABQAAABMAAAAAAAAAAAAAAIABRwEAAGZpeHR1cmUvcGF5bG9hZC5iaW5QSwUGAAAAAAQABAAXAQAAfwEAAAAA',
  traversal: 'UEsDBBQAAAAAANsqU126pm+gigAAAIoAAAAeAAAAZml4dHVyZS8uYmx1ZXByaW50L2ZvbGRlci5qc29ueyJpZCI6ICJmaXh0dXJlLWlkIiwgIm5hbWUiOiAi5aOT57iu5ris6KmmIiwgImRlc2NyaXB0aW9uIjogIiIsICJ0YWdzIjogWyLlu6PmnbHoqbEiXSwgImNhdGVnb3J5IjogImdlbmVyYWwiLCAiZXh0cmEiOiB7InByZXNlcnZlIjogdHJ1ZX19UEsDBBQAAAAAANsqU11bAbieDwAAAA8AAAAVAAAAZml4dHVyZS8uLi9lc2NhcGUudHh0bXVzdCBub3QgZXNjYXBlUEsBAhQDFAAAAAAA2ypTXbqmb6CKAAAAigAAAB4AAAAAAAAAAAAAAIABAAAAAGZpeHR1cmUvLmJsdWVwcmludC9mb2xkZXIuanNvblBLAQIUAxQAAAAAANsqU11bAbieDwAAAA8AAAAVAAAAAAAAAAAAAACAAcYAAABmaXh0dXJlLy4uL2VzY2FwZS50eHRQSwUGAAAAAAIAAgCPAAAACAEAAAAA',
  duplicate: 'UEsDBBQAAAAAANsqU126pm+gigAAAIoAAAAeAAAAZml4dHVyZS8uYmx1ZXByaW50L2ZvbGRlci5qc29ueyJpZCI6ICJmaXh0dXJlLWlkIiwgIm5hbWUiOiAi5aOT57iu5ris6KmmIiwgImRlc2NyaXB0aW9uIjogIiIsICJ0YWdzIjogWyLlu6PmnbHoqbEiXSwgImNhdGVnb3J5IjogImdlbmVyYWwiLCAiZXh0cmEiOiB7InByZXNlcnZlIjogdHJ1ZX19UEsDBBQAAAAAANsqU11X7nGSBQAAAAUAAAANAAAAZml4dHVyZS9hLnR4dGZpcnN0UEsDBBQAAAAAANsqU11pER+2BgAAAAYAAAANAAAAZml4dHVyZS9hLnR4dHNlY29uZFBLAQIUAxQAAAAAANsqU126pm+gigAAAIoAAAAeAAAAAAAAAAAAAACAAQAAAABmaXh0dXJlLy5ibHVlcHJpbnQvZm9sZGVyLmpzb25QSwECFAMUAAAAAADbKlNdV+5xkgUAAAAFAAAADQAAAAAAAAAAAAAAgAHGAAAAZml4dHVyZS9hLnR4dFBLAQIUAxQAAAAAANsqU11pER+2BgAAAAYAAAANAAAAAAAAAAAAAACAAfYAAABmaXh0dXJlL2EudHh0UEsFBgAAAAADAAMAwgAAACcBAAAAAA==',
};

function blank(overrides = {}) {
  return {
    id: 'identity-for-round-trip',
    name: '空白專員',
    description: '',
    category: 'general',
    tags: [],
    files: [],
    ...overrides,
  };
}

function fixtureBlob(name) {
  return new Blob([Buffer.from(fixture[name], 'base64')], { type: 'application/zip' });
}

test('blank folder round trip retains identity and leaves purpose blank', async () => {
  const [result] = await importZip(await getZipBlob(blank()));
  assert.equal(result.id, 'identity-for-round-trip');
  assert.equal(result.name, '空白專員');
  assert.equal(result.description, '');
  assert.equal(result.category, 'general');
  assert.deepEqual(result.tags, []);
  assert.equal(result.icon.platform, 'portable');
  assert.equal(result.icon.applied, false);
  assert.match(result.files.find(file => file.path === 'AGENTS.md').content, /用途可以留空/);
});

test('Chinese and emoji paths, original guidance, text and binary survive two round trips', async () => {
  const guidance = '# 自訂 Codex 指引\r\n\r\n保留所有內容："引號"、👋。\r\n';
  const text = '你好，world!\n\"quoted\" and \'single\'\t🏦\n';
  const binary = Buffer.from([0, 255, 128, 1, 2, 127, 240, 159, 145, 139]);
  const original = blank({
    name: '藍圖工作室 🗂️',
    description: '中文描述，含 "引號" 與 emoji 🏦',
    tags: ['廣東話', 'emoji 👋'],
    files: [
      { path: 'AGENTS.md', content: guidance, encoding: 'utf-8' },
      { path: '資料/你好👋.txt', content: text, encoding: 'utf-8' },
      { path: '資料/payload.bin', content: binary.toString('base64'), encoding: 'base64' },
    ],
  });
  const [first] = await importZip(await getZipBlob(original));
  const [result] = await importZip(await getZipBlob(first));
  assert.equal(result.name, original.name);
  assert.equal(result.description, original.description);
  assert.deepEqual(result.tags, original.tags);
  assert.equal(result.files.length, 3);
  const byPath = new Map(result.files.map(file => [file.path, file]));
  assert.equal(byPath.get('AGENTS.md').content, guidance);
  assert.equal(byPath.get('資料/你好👋.txt').content, text);
  assert.equal(byPath.get('資料/payload.bin').encoding, 'base64');
  assert.deepEqual(Buffer.from(byPath.get('資料/payload.bin').content, 'base64'), binary);
});

test('unknown metadata fields are preserved rather than reduced to a summary', async () => {
  const extension = { client: '企業 A', settings: { enabled: false, limits: [0, 5] } };
  const original = blank({ custom_extension: extension, future_field: null, revision: 42 });
  const [result] = await importZip(await getZipBlob(original));
  assert.deepEqual(result.custom_extension, extension);
  assert.equal(result.future_field, null);
  assert.equal(result.revision, 42);
});

test('payload corruption is rejected by ZIP CRC validation', async () => {
  const sentinel = 'this payload must keep its checksum';
  const blob = await getZipBlob(blank({ files: [{ path: 'content.txt', content: sentinel }] }));
  const damaged = Buffer.from(await blob.arrayBuffer());
  const offset = damaged.indexOf(sentinel, 0, 'utf8');
  assert.ok(offset >= 0, 'fixture must contain an uncompressed payload');
  damaged[offset] ^= 1;
  await assert.rejects(importZip(new Blob([damaged])), /校驗/);
});

test('unsafe, duplicate and reserved file paths cannot be exported', async () => {
  for (const unsafe of ['../escape.txt', 'a/../../escape.txt', '/absolute.txt', 'a\\b.txt', '.blueprint/folder.json']) {
    await assert.rejects(getZipBlob(blank({ files: [{ path: unsafe, content: 'data' }] })));
  }
  await assert.rejects(getZipBlob(blank({ files: [
    { path: 'same.txt', content: 'first' },
    { path: 'same.txt', content: 'second' },
  ] })), /重複/);
  await assert.rejects(getZipBlob(blank({ name: '../escape' })));
});

test('ZIP import rejects traversal and duplicate entries from independent archives', async () => {
  await assert.rejects(importZip(fixtureBlob('traversal')), /路徑|\.\./);
  await assert.rejects(importZip(fixtureBlob('duplicate')), /重複/);
});

test('imports Python ZIP_DEFLATED files, including Unicode paths and binary content', async () => {
  const [result] = await importZip(fixtureBlob('deflated'));
  assert.equal(result.id, 'fixture-id');
  assert.equal(result.name, '壓縮測試');
  assert.equal(result.description, '');
  assert.deepEqual(result.extra, { preserve: true });
  const byPath = new Map(result.files.map(file => [file.path, file]));
  assert.equal(byPath.get('AGENTS.md').content, '# 原有指引\n');
  assert.equal(byPath.get('資料/你好👋.txt').content, '中文內容\n'.repeat(20));
  assert.deepEqual(Buffer.from(byPath.get('payload.bin').content, 'base64'), Buffer.from([0, 255, 128, 1, 0]));
});
