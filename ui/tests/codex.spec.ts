import { readFile } from 'node:fs/promises';
import { expect, test, type APIRequestContext } from '@playwright/test';

const allowed = process.env.BLUEPRINT_E2E_ALLOW_WRITES === '1';
const email = process.env.BLUEPRINT_E2E_EMAIL;
const password = process.env.BLUEPRINT_E2E_PASSWORD;
const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
const name = `browser-codex-${suffix}`;
const fullName = `Codex · ${name}`;
let api: APIRequestContext;

test.describe('real Codex connection onboarding', () => {
  test.setTimeout(60_000);
  test.skip(!allowed || !email || !password, 'Requires a disposable real workspace and explicit E2E write opt-in.');
  test.beforeAll(async ({ playwright, baseURL }) => {
    api = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { 'X-Blueprint-Request': '1' } });
    expect((await api.post('/api/v1/login', { data: { email, password } })).ok()).toBeTruthy();
  });
  test.afterAll(async () => {
    if (!api) return;
    const response = await api.get('/api/v1/tokens');
    if (response.ok()) for (const token of await response.json()) if (token.name === fullName && !token.revoked_at) await api.delete(`/api/v1/tokens/${token.id}`);
    await api.dispose();
  });

  test('create, privately download, use and revoke a dedicated Codex credential', async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, storageState: await api.storageState() });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/#codex');
    await expect(page.getByRole('heading', { name: 'Codex 協作', exact: true })).toBeVisible();
    await expect(page.getByLabel('Codex 開始指示')).toHaveValue(/blueprint_context/);
    await page.getByRole('button', { name: '建立連接金鑰', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('連接名稱', { exact: true }).fill(name);
    await expect(dialog.getByLabel('允許整理及儲存工作')).toBeChecked();
    await expect(dialog.getByLabel('允許執行工作流程及向 AI 員工派工')).not.toBeChecked();
    await dialog.getByRole('button', { name: '建立金鑰', exact: true }).click();
    const field = dialog.getByLabel('新建立的 Codex 連接金鑰');
    await expect(field).toHaveAttribute('type', 'password');
    const secret = await field.inputValue();
    expect(secret.startsWith('bp_')).toBeTruthy();
    const downloaded = page.waitForEvent('download');
    await dialog.getByRole('button', { name: '下載 codex-token', exact: true }).click();
    const file = await downloaded;
    expect(file.suggestedFilename()).toBe('codex-token');
    const path = await file.path();
    expect(path !== null).toBeTruthy();
    expect((await readFile(path!, 'utf8')).trim() === secret).toBeTruthy();
    const persisted = await page.evaluate(value => {
      const local = Object.values(localStorage).join(' ');
      const session = Object.values(sessionStorage).join(' ');
      return local.includes(value) || session.includes(value) || location.href.includes(value);
    }, secret);
    expect(persisted).toBe(false);
    await dialog.getByRole('button', { name: '完成並關閉', exact: true }).click();
    await expect(page.getByLabel('新建立的 Codex 連接金鑰')).toHaveCount(0);
    const card = page.locator('.codex-token-card').filter({ hasText: fullName });
    await expect(card).toContainText('尚無使用紀錄');
    const tokens = await (await api.get('/api/v1/tokens')).json();
    const token = tokens.find((item: any) => item.name === fullName);
    expect(!!token).toBeTruthy();
    expect(token.scopes.includes('admin:write') || token.scopes.includes('admin:read') || token.scopes.includes('workflows:execute')).toBe(false);
    expect(token.scopes.includes('business:write')).toBeTruthy();
    expect(token.scopes.includes('content:write')).toBeTruthy();
    expect(new Date(token.expires_at).getTime() > Date.now() + 89 * 24 * 60 * 60 * 1000).toBeTruthy();
    const mcp = await api.post('/api/v1/mcp', { headers: { Authorization: `Bearer ${secret}` }, data: { jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} } });
    expect(mcp.ok()).toBeTruthy();
    await page.getByRole('button', { name: '重新整理 Codex 協作' }).click();
    await expect(card).not.toContainText('尚無使用紀錄');
    await card.getByRole('button', { name: `撤銷 ${fullName}`, exact: true }).click();
    await expect(card).toContainText('已撤銷');
    const revoked = await api.post('/api/v1/mcp', { headers: { Authorization: `Bearer ${secret}` }, data: { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} } });
    expect(revoked.status()).toBe(401);
    await page.reload();
    await expect(page.getByLabel('新建立的 Codex 連接金鑰')).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    expect(errors).toEqual([]);
    await context.close();
  });
});
