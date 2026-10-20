import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext } from '@playwright/test';

const allowWrites = process.env.BLUEPRINT_E2E_ALLOW_WRITES === '1';
const email = process.env.BLUEPRINT_E2E_EMAIL;
const password = process.env.BLUEPRINT_E2E_PASSWORD;
const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
const fileName = `ui-content-${suffix}.txt`;
const renamed = `ui-renamed-${suffix}.txt`;
const folderName = `ui-folder-${suffix}`;
const collectionName = `ui-collection-${suffix}`;
const resumedName = `ui-resumed-${suffix}.bin`;
const body = `Blueprint workspace browser integration ${suffix}\n繁體中文內容及持久保存。`;
let context: APIRequestContext;

test.describe('real self-hosted workspace flows', () => {
  test.setTimeout(90_000);
  test.skip(!allowWrites || !email || !password, 'Set explicit E2E write opt-in and dedicated test credentials.');
  test.beforeAll(async ({ playwright, baseURL }) => {
    context = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { 'X-Blueprint-Request': '1' } });
    const status = await (await context.get('/api/v1/status')).json();
    if (!status.initialized) {
      const setup = await context.post('/api/v1/setup', { data: { token: process.env.BLUEPRINT_E2E_SETUP_TOKEN ?? '', company_name: 'UI 驗收工作空間', name: 'UI 測試管理員', email, password } });
      expect(setup.ok(), 'Dedicated test instance must initialize with its configured setup token').toBeTruthy();
    }
    const login = await context.post('/api/v1/login', { data: { email, password } });
    expect(login.ok(), 'Dedicated test credentials must authenticate').toBeTruthy();
  });
  test.afterAll(async () => {
    if (!context) return;
    for (const kind of ['file', 'folder', 'collection']) {
      const response = await context.get(`/api/v1/resources/${kind}`);
      if (!response.ok()) continue;
      const resources = await response.json();
      for (const resource of resources) {
        if ([fileName, renamed, folderName, collectionName, resumedName].includes(resource.name)) await context.delete(`/api/v1/resource/${resource.id}?expected_version=${resource.version}`);
      }
    }
    await context.dispose();
  });

  test('content lifecycle, shared download, keyboard search and mobile layout', async ({ browser, baseURL }) => {
    const browserContext = await browser.newContext({ baseURL, viewport: { width: 1440, height: 960 }, storageState: await context.storageState() });
    const page = await browserContext.newPage();
    const browserErrors: string[] = [];
    page.on('pageerror', error => browserErrors.push(error.message));
    await page.goto('/#files');
    await expect(page.getByRole('heading', { name: '文件', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '新增', exact: true }).click();
    await page.getByRole('button', { name: '資料夾', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('名稱', { exact: true }).fill(folderName);
    await dialog.getByRole('button', { name: '儲存', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: folderName })).toBeVisible();

    await page.locator('input[type=file]').first().setInputFiles({ name: fileName, mimeType: 'text/plain', buffer: Buffer.from(body) });
    let row = page.getByRole('row').filter({ hasText: fileName });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row.locator('.resource-open').click();
    dialog = page.getByRole('dialog', { name: `預覽 ${fileName}` });
    await expect(dialog.locator('.text-preview')).toContainText('繁體中文內容', { timeout: 20_000 });
    await dialog.getByRole('button', { name: '活動', exact: true }).click();
    await dialog.getByLabel('新增留言').fill('真實留言：內容已確認。');
    await dialog.getByRole('button', { name: '發佈留言' }).click();
    await expect(dialog.locator('.comments-list')).toContainText('真實留言：內容已確認。');
    await dialog.getByRole('button', { name: '關閉預覽' }).click();

    await row.getByRole('button', { name: `${fileName} 的更多操作` }).click();
    await page.getByRole('button', { name: '重新命名', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('名稱', { exact: true }).fill(renamed);
    await dialog.getByRole('button', { name: '儲存', exact: true }).click();
    row = page.getByRole('row').filter({ hasText: renamed });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: `${renamed} 的更多操作` }).click();
    await page.getByRole('button', { name: '加入星號', exact: true }).click();
    await page.getByRole('link', { name: '星號收藏', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: renamed })).toBeVisible();
    await page.getByRole('link', { name: '文件', exact: true }).click();
    row = page.getByRole('row').filter({ hasText: renamed });
    await page.getByRole('button', { name: '新增', exact: true }).click();
    await page.getByRole('main').getByRole('button', { name: '個人集合', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '建立個人集合' });
    await dialog.getByLabel('名稱', { exact: true }).fill(collectionName);
    await dialog.getByRole('button', { name: '儲存', exact: true }).click();
    await row.getByRole('button', { name: `${renamed} 的更多操作` }).click();
    await page.getByRole('button', { name: '加入集合', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '加入個人集合' });
    await dialog.getByLabel('選擇集合').selectOption({ label: collectionName });
    await dialog.getByRole('button', { name: '儲存', exact: true }).click();
    await page.getByRole('link', { name: collectionName, exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: renamed })).toBeVisible();
    await page.getByRole('link', { name: '文件', exact: true }).click();
    while (await page.locator('.toast-stack .toast button').count()) await page.locator('.toast-stack .toast button').first().click();
    await page.screenshot({ path: '/tmp/blueprint-v2-ui-desktop.png', fullPage: true, animations: 'disabled' });
    await row.getByRole('button', { name: `分享 ${renamed}` }).click();
    dialog = page.getByRole('dialog', { name: '分享內容' });
    await dialog.getByLabel('誰可以開啟連結？').selectOption('anyone');
    await dialog.getByRole('button', { name: '建立分享連結', exact: true }).click();
    const shareInput = dialog.getByLabel('分享連結', { exact: true });
    await expect(shareInput).toHaveValue(/\/share\//);
    const shareUrl = await shareInput.inputValue();
    const publicContext = await browser.newContext();
    const publicPage = await publicContext.newPage();
    await publicPage.goto(shareUrl);
    await expect(publicPage.getByRole('heading', { name: renamed })).toBeVisible();
    const [download] = await Promise.all([publicPage.waitForEvent('download'), publicPage.getByRole('button', { name: '下載檔案' }).click()]);
    expect(download.suggestedFilename()).toBe(renamed);
    expect(await download.failure()).toBeNull();
    expect(await readFile((await download.path())!, 'utf8')).toBe(body);
    await publicContext.close();
    await dialog.getByRole('button', { name: '完成', exact: true }).click();

    await row.getByRole('button', { name: `${renamed} 的更多操作` }).click();
    await page.getByRole('button', { name: '移至垃圾桶', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '移至垃圾桶' });
    await dialog.getByRole('button', { name: '移至垃圾桶', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: renamed })).toHaveCount(0);
    await page.goto('/#trash');
    row = page.getByRole('row').filter({ hasText: renamed });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: `${renamed} 的更多操作` }).click();
    await page.getByRole('button', { name: '還原', exact: true }).click();
    await page.goto('/#files');
    await expect(page.getByRole('row').filter({ hasText: renamed })).toBeVisible();

    await page.keyboard.press('Control+k');
    const search = page.getByRole('dialog', { name: '搜尋工作空間' });
    await search.getByLabel('全域搜尋').fill(renamed);
    await expect(search.locator('.search-result').filter({ hasText: renamed }).first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(search).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('button', { name: '開啟導覽' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    while (await page.locator('.toast-stack .toast button').count()) await page.locator('.toast-stack .toast button').first().click();
    await page.screenshot({ path: '/tmp/blueprint-v2-ui-mobile.png', fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '開啟導覽' }).click();
    await expect(page.getByRole('navigation', { name: '主要導覽' })).toBeVisible();
    await page.getByRole('button', { name: '關閉導覽' }).click();
    expect(browserErrors).toEqual([]);
    await browserContext.close();
  });

  test('resumes a real chunked upload after a network interruption and page reload', async ({ browser, baseURL }) => {
    const temporary = await mkdtemp(join(tmpdir(), 'blueprint-ui-upload-'));
    const path = join(temporary, resumedName);
    const size = 17 * 1024 * 1024;
    await writeFile(path, Buffer.alloc(size, 0x62));
    const owner = await browser.newContext({ baseURL, storageState: await context.storageState() });
    const page = await owner.newPage();
    let patches = 0;
    let creates = 0;
    let uploadId = '';
    page.on('response', response => {
      if (response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/uploads') {
        creates++;
        void response.json().then(value => { uploadId = value.id; });
      }
    });
    // Only interrupt one real network request; all responses come from the live Rust service.
    await page.route('**/api/v1/uploads/*', async route => {
      if (route.request().method() === 'PATCH' && ++patches === 2) await route.abort('connectionreset');
      else await route.continue();
    });
    try {
      await page.goto('/#files');
      await page.locator('input[type=file]').first().setInputFiles(path);
      await expect(page.locator('.upload-panel')).toContainText('網絡連線中斷', { timeout: 30_000 });
      expect(uploadId).not.toBe('');
      const interrupted = await (await context.get(`/api/v1/uploads/${uploadId}`)).json();
      expect(interrupted.offset).toBe(8 * 1024 * 1024);
      await page.reload();
      await page.locator('input[type=file]').first().setInputFiles(path);
      await expect(page.getByRole('row').filter({ hasText: resumedName })).toBeVisible({ timeout: 45_000 });
      expect(creates).toBe(1);
      const completed = await (await context.get(`/api/v1/uploads/${uploadId}`)).json();
      expect(completed.offset).toBe(size);
      expect(completed.status).toBe('completed');
    } finally { await owner.close(); await rm(temporary, { recursive: true, force: true }); }
  });

});
