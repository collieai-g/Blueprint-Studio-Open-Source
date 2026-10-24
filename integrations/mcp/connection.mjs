/* Acceptance uses the same authenticated API and schema as normal work. */
export const PREFIX = 'blueprint-connect:';
export const INITIAL = 'blueprint-connection/1:pending';
export const COMPLETE = 'blueprint-connection/1:verified';
const TITLE = 'AI 接駁測試', TODO = '接駁測試：待辦寫入及更新';
const fail = (message, status = 409) => { throw Object.assign(new Error(message), { status }); };

export async function verifyConnection(request, { challenge, client, date }) {
  if (!/^[a-f0-9]{32}$/.test(challenge) || !client.trim() || /[\x00-\x1f]/.test(client) || [...client].length > 80 ||
      !/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date) fail('接駁測試參數無效。', 400);
  const key = PREFIX + challenge;
  const list = await request('GET', '/api/workdays', { q: key, limit: 100 });
  const matches = list.workdays.filter(r => r.source_key === key && r.source === client && r.date === date);
  if (matches.length > 1) fail('找到多筆接駁紀錄，未覆寫。');
  let id = matches[0]?.id;
  if (!id) id = (await request('POST', '/api/workdays', {
    date, source: client, source_key: key, title: TITLE, summary: INITIAL,
    entries: [{ type: 'outcome', title: TITLE, content: challenge }, { type: 'todo', title: TODO, content: challenge }]
  })).workday.id;
  const path = '/api/workdays/' + id;
  let report = (await request('GET', path)).workday;
  const e = report.entries;
  if (report.source_key !== key || report.source !== client || report.date !== date || report.title !== TITLE ||
      ![INITIAL, COMPLETE].includes(report.summary) || e.length !== 2 ||
      e[0].type !== 'outcome' || e[0].title !== TITLE || e[1].type !== 'todo' || e[1].title !== TODO ||
      e.some(entry => entry.content !== challenge)) fail('測試紀錄已被修改，未覆寫；請在目前 AI 對話核對，只有要求另一次驗收才建立新的測試。');
  await request('PATCH', '/api/todos/' + e[1].id, { expected_version: e[1].version, done: true });
  report = (await request('GET', path)).workday;
  if (!report.entries[1].done) fail('待辦更新未能讀回，驗收未完成。', 502);
  if (![INITIAL, COMPLETE].includes(report.summary)) fail('測試紀錄已被修改，未覆寫。');
  const patchEntries = report.entries.map(e => ({ ...Object.fromEntries(['id', 'type', 'title', 'content', 'url', 'tags'].map(k => [k, e[k]])), ...(e.type === 'todo' ? { due_date: e.due_date } : {}) }));
  await request('PATCH', path, { expected_version: report.version, summary: COMPLETE, entries: patchEntries });
  report = (await request('GET', path)).workday;
  if (report.summary !== COMPLETE || !report.entries[1].done) fail('驗收結果未能讀回。', 502);
  return { verified: true, client, challenge, workday_id: id, checked_at: report.updated_at, workday: report,
    next_step: '在目前 AI 對話按 workday_id 另行讀回紀錄，並按待辦 ID 核對測試待辦已完成；可到工作台「工作 → 工作紀錄」查看同一筆。新對話須重新檢查實際工具。' };
}
