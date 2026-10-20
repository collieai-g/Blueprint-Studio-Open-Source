import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test';

const allowed = process.env.BLUEPRINT_E2E_ALLOW_WRITES === '1';
const email = process.env.BLUEPRINT_E2E_EMAIL;
const password = process.env.BLUEPRINT_E2E_PASSWORD;
let api: APIRequestContext;
let storageKey: string;

async function openOverview(browser: Browser, baseURL?: string, viewport = { width: 1440, height: 960 }) {
  const context = await browser.newContext({ baseURL, viewport, storageState: await api.storageState() });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#overview');
  await expect(page.getByRole('heading', { name: '工作總覽', exact: true })).toBeVisible();
  return { context, page, errors };
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
}

test.describe('real workspace canvas layout', () => {
  test.setTimeout(90_000);
  test.skip(!allowed || !email || !password, 'Requires dedicated real workspace credentials and explicit E2E opt-in.');

  test.beforeAll(async ({ playwright, baseURL }) => {
    api = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { 'X-Blueprint-Request': '1' } });
    expect((await api.post('/api/v1/login', { data: { email, password } })).ok()).toBeTruthy();
    const me = await (await api.get('/api/v1/me')).json();
    storageKey = `blueprint-v2-workspace-layout-${(me.user ?? me).id}`;
  });

  test.afterAll(async () => { await api?.dispose(); });

  test('real summaries, independent card sizes, snapped pointer and keyboard resizing persist', async ({ browser, baseURL }) => {
    const { context, page, errors } = await openOverview(browser, baseURL);
    const cards = page.locator('article[data-widget-id]');
    await expect(cards.first()).toBeVisible();
    expect(await cards.count()).toBeGreaterThan(2);
    await expect(page.locator('.overview-page')).not.toContainText('正在載入', { timeout: 20_000 });
    const filesResponse = await api.get('/api/v1/resources/file');
    expect(filesResponse.ok()).toBeTruthy();
    const files = await filesResponse.json();
    const recentFiles = (Array.isArray(files) ? files : files.items ?? [])
      .filter((item: any) => !item.data.archived)
      .sort((a: any, b: any) => b.updated_at.localeCompare(a.updated_at));
    if (recentFiles.length) await expect(page.locator('[data-widget-id="files"]')).toContainText(recentFiles[0].name);

    const firstId = await cards.nth(0).getAttribute('data-widget-id');
    const secondId = await cards.nth(1).getAttribute('data-widget-id');
    const first = page.locator(`article[data-widget-id="${firstId}"]`);
    const second = page.locator(`article[data-widget-id="${secondId}"]`);
    const title = (await first.getByRole('heading', { level: 2 }).innerText()).trim();
    const secondSize = [await second.getAttribute('data-width'), await second.getAttribute('data-height')];
    await page.getByRole('button', { name: '編輯版面', exact: true }).click();
    await first.getByLabel(`${title} 的闊度`).selectOption('6');
    await first.getByLabel(`${title} 的高度`).selectOption('7');
    await expect(first).toHaveAttribute('data-width', '6');
    await expect(first).toHaveAttribute('data-height', '7');
    await expect(second).toHaveAttribute('data-width', secondSize[0]!);
    await expect(second).toHaveAttribute('data-height', secondSize[1]!);

    const handle = first.getByRole('button', { name: `調整 ${title} 大小`, exact: true });
    await handle.focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowDown');
    await expect(first).toHaveAttribute('data-width', '7');
    await expect(first).toHaveAttribute('data-height', '8');
    const point = await handle.boundingBox();
    const gridWidth = await page.locator('.workspace-widget-grid').evaluate(element => element.getBoundingClientRect().width);
    expect(point).not.toBeNull();
    await page.mouse.move(point!.x + point!.width / 2, point!.y + point!.height / 2);
    await page.mouse.down();
    await page.mouse.move(point!.x + point!.width / 2 + (gridWidth + 16) / 12, point!.y + point!.height / 2 + 56, { steps: 6 });
    await page.mouse.up();
    await expect(first).toHaveAttribute('data-width', '8');
    await expect(first).toHaveAttribute('data-height', '9');
    await handle.scrollIntoViewIfNeeded();
    const cancelPoint = await handle.boundingBox();
    await page.mouse.move(cancelPoint!.x + cancelPoint!.width / 2, cancelPoint!.y + cancelPoint!.height / 2);
    await page.mouse.down();
    await page.mouse.move(cancelPoint!.x + cancelPoint!.width / 2 - (gridWidth + 16) / 12, cancelPoint!.y + cancelPoint!.height / 2 - 56, { steps: 4 });
    await expect(first).toHaveAttribute('data-width', '7');
    await page.keyboard.press('Escape');
    await page.mouse.up();
    await expect(first).toHaveAttribute('data-width', '8');
    await expect(first).toHaveAttribute('data-height', '9');
    await expect(page.getByRole('status').filter({ hasText: '版面已儲存於此瀏覽器' })).toBeVisible();
    await page.getByRole('button', { name: '完成', exact: true }).click();
    await page.reload();
    await expect(first).toHaveAttribute('data-width', '8');
    await expect(first).toHaveAttribute('data-height', '9');
    await expect(second).toHaveAttribute('data-width', secondSize[0]!);
    await expectNoHorizontalOverflow(page);

    // The tablet projection must not overwrite a wider saved desktop card
    // when the user only adjusts its height.
    await page.setViewportSize({ width: 1000, height: 960 });
    await page.getByRole('button', { name: '編輯版面', exact: true }).click();
    await handle.scrollIntoViewIfNeeded();
    const tabletPoint = await handle.boundingBox();
    await page.mouse.move(tabletPoint!.x + tabletPoint!.width / 2, tabletPoint!.y + tabletPoint!.height / 2);
    await page.mouse.down();
    await page.mouse.move(tabletPoint!.x + tabletPoint!.width / 2, tabletPoint!.y + tabletPoint!.height / 2 + 56, { steps: 4 });
    await page.mouse.up();
    await expect(first).toHaveAttribute('data-width', '8');
    await expect(first).toHaveAttribute('data-height', '10');
    await expectNoHorizontalOverflow(page);
    expect(errors).toEqual([]);
    await context.close();
  });

  test('reordering, hiding, restoring and mobile sizing remain usable without overflow', async ({ browser, baseURL }) => {
    const { context, page, errors } = await openOverview(browser, baseURL);
    const cards = page.locator('article[data-widget-id]');
    const originalOrder = await cards.evaluateAll(elements => elements.map(element => element.getAttribute('data-widget-id')));
    const firstId = originalOrder[0]!;
    const first = page.locator(`article[data-widget-id="${firstId}"]`);
    const title = (await first.getByRole('heading', { level: 2 }).innerText()).trim();
    await page.getByRole('button', { name: '編輯版面', exact: true }).click();
    await first.getByRole('button', { name: `將 ${title} 向後移動`, exact: true }).click();
    await expect(cards.nth(1)).toHaveAttribute('data-widget-id', firstId);
    await first.getByRole('button', { name: `隱藏 ${title}`, exact: true }).click();
    await expect(first).toHaveCount(0);
    await page.getByLabel('加入卡片', { exact: true }).selectOption(firstId);
    await expect(first).toBeVisible();
    await page.getByRole('button', { name: '還原版面', exact: true }).click();
    expect(await cards.evaluateAll(elements => elements.map(element => element.getAttribute('data-widget-id')))).toEqual(originalOrder);

    await page.setViewportSize({ width: 390, height: 844 });
    await expectNoHorizontalOverflow(page);
    await first.getByLabel(`${title} 的闊度`).selectOption('4');
    await first.getByLabel(`${title} 的高度`).selectOption('5');
    await expect(first).toHaveAttribute('data-width', '4');
    await expect(first).toHaveAttribute('data-height', '5');
    await page.getByRole('button', { name: '完成', exact: true }).click();
    await page.reload();
    await expect(first).toHaveAttribute('data-width', '4');
    await expectNoHorizontalOverflow(page);
    await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
    await page.locator('.main-navigation').getByRole('link', { name: '文件', exact: true }).click();
    await expect(page.getByRole('heading', { name: '文件', exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    expect(errors).toEqual([]);
    await context.close();
  });

  test('damaged saved layout safely recovers to defaults and can be saved again', async ({ browser, baseURL }) => {
    const { context, page, errors } = await openOverview(browser, baseURL);
    const original = await page.locator('article[data-widget-id]').evaluateAll(elements => elements.map(element => ({
      id: element.getAttribute('data-widget-id'), width: element.getAttribute('data-width'), height: element.getAttribute('data-height'),
    })));
    await page.evaluate(key => localStorage.setItem(key, '{broken layout'), storageKey);
    await page.reload();
    await expect(page.getByRole('heading', { name: '工作總覽', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '編輯版面', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '無法讀取已儲存的版面' })).toBeVisible();
    expect(await page.locator('article[data-widget-id]').evaluateAll(elements => elements.map(element => ({
      id: element.getAttribute('data-widget-id'), width: element.getAttribute('data-width'), height: element.getAttribute('data-height'),
    })))).toEqual(original);
    await page.getByRole('button', { name: '還原版面', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '版面已儲存於此瀏覽器' })).toBeVisible();
    expect(errors).toEqual([]);
    await context.close();
  });
});
