import { expect, test } from '@playwright/test';

test.describe.configure({ mode: 'serial' });
const fixture = 'http://127.0.0.1:8767';

test('online links auto connect desktop and mobile, synchronize and revoke without approval', async ({ browser, request }) => {
    const links = await (await request.get(fixture)).json();
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const a = await desktop.newPage(), b = await mobile.newPage();
    try {
        for (const page of [a, b]) {
            await page.goto(links.online);
            await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
            await expect(page.getByRole('button', { name: '请求配对', exact: true })).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            expect(new URL(page.url()).hash).toBe('');
            await expect(page.getByRole('button', { name: '启动桥接', exact: true })).toBeVisible();
            await expect(page.getByRole('button', { name: '连接 USB', exact: true })).toBeVisible();
            const notice = page.getByRole('status', { name: '服务连接状态', exact: true });
            const before = await notice.evaluate(node => node.getBoundingClientRect().top);
            await page.evaluate(() => window.scrollTo(0, 350));
            if (page === b) expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(300);
            await expect.poll(async () => {
                const after = await notice.evaluate(node => node.getBoundingClientRect().top);
                return Math.abs(after + await page.evaluate(() => window.scrollY) - before);
            }).toBeLessThan(2);
            await page.evaluate(() => window.scrollTo(0, 0));
        }
        await a.getByLabel('业务 COM', { exact: true }).fill('COM88');
        await expect(b.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
        await b.reload();
        await expect(b.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
        await expect(b.getByLabel('业务 COM', { exact: true })).toHaveValue('COM88');
        await a.getByRole('button', { name: '局域网连接', exact: true }).click();
        const mobilePeer = a.getByRole('listitem').filter({ hasText: '手机 / 平板浏览器' });
        await expect(mobilePeer).toBeVisible();
        await mobilePeer.getByRole('button', { name: '撤销连接' }).click();
        await expect(b.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('已撤销');
        await b.reload();
        await expect(b.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('已断开或被撤销');
        const notice = await b.getByRole('status', { name: '服务连接状态', exact: true }).boundingBox();
        expect(notice!.x).toBeGreaterThanOrEqual(0);
        expect(notice!.x + notice!.width).toBeLessThanOrEqual(390);
        await a.getByRole('button', { name: '关闭连接面板' }).click();
        await a.screenshot({ path: 'test-results/lan-desktop.png', fullPage: true });
        await b.screenshot({ path: 'test-results/lan-mobile.png', fullPage: true });
    } finally { await desktop.close(); await mobile.close(); }
});

test('local QR link and plain LAN page auto connect without internet', async ({ browser, request }) => {
    const links = await (await request.get(fixture)).json();
    for (const target of [links.local, 'http://127.0.0.1:8766/']) {
        const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
        const page = await context.newPage();
        try {
            await context.route('https://**/*', route => route.abort());
            await page.goto(target);
            await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
            await expect(page.getByRole('button', { name: '启动桥接', exact: true })).toBeVisible();
            await page.getByRole('button', { name: '局域网连接', exact: true }).click();
            await expect(page.getByRole('region', { name: '局域网连接', exact: true })).toBeVisible();
            await page.getByRole('button', { name: '关闭连接面板' }).click();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        } finally { await context.close(); }
    }
});

test('fixed frontend URL restores last service in a new tab and disconnect remains disconnected', async ({ context, page, request }) => {
    const links = await (await request.get(fixture)).json();
    await page.goto(links.online);
    await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
    const other = await context.newPage();
    await other.goto('/');
    await expect(other.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
    await other.getByRole('button', { name: '局域网连接', exact: true }).click();
    await other.getByRole('button', { name: '断开本终端' }).click();
    await expect(other.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('已断开或被撤销');
    await other.close();
});

test('fresh fixed frontend does not guess service or make enrollment requests', async ({ page }) => {
    let requests = 0;
    page.on('request', req => { if (req.url().includes('/api/')) requests++; });
    await page.goto('/');
    await expect(page.getByText('浏览器界面预览', { exact: false })).toBeVisible();
    expect(requests).toBe(0);
});

test('rotated links reject stale enrollment while connected sessions keep working', async ({ browser, request }) => {
    const links = await (await request.get(fixture)).json();
    const context = await browser.newContext();
    const page = await context.newPage();
    const fresh = await browser.newContext();
    const oldLink = await fresh.newPage();
    try {
        await page.goto(links.online);
        await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
        await request.post(fixture, { data: { action: 'rotate' } });
        await oldLink.goto(links.online);
        await expect(oldLink.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('链接已失效');
        await page.reload();
        await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
        const current = await (await request.get(fixture)).json();
        await oldLink.goto(current.online);
        await expect(oldLink.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
    } finally { await context.close(); await fresh.close(); }
});

test('network failure explains retry and local entry, then retry succeeds', async ({ page, request }) => {
    const links = await (await request.get(fixture)).json();
    await page.route('**/api/connect', route => route.abort());
    await page.goto(links.online);
    await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('无法连接服务');
    await page.unroute('**/api/connect');
    await page.getByRole('button', { name: '局域网连接', exact: true }).click();
    await expect(page.getByRole('button', { name: '打开局域网工作台' })).toBeVisible();
    await page.getByRole('button', { name: '连接服务 / 重试' }).click();
    await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
});

test('lost enrollment response retries the same enrollment instead of adding a second terminal', async ({ page, request }) => {
    const links = await (await request.get(fixture)).json();
    const identifiers: string[] = [];
    let first = true;
    await page.route('**/api/connect', async route => {
        identifiers.push(route.request().postDataJSON().join_id);
        if (first) { first = false; await route.fetch(); await route.abort(); }
        else await route.continue();
    });
    await page.goto(links.online);
    await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toContainText('无法连接服务');
    await page.getByRole('button', { name: '局域网连接', exact: true }).click();
    await page.getByRole('button', { name: '连接服务 / 重试' }).click();
    await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
    expect(identifiers).toHaveLength(2);
    expect(identifiers[0]).toBe(identifiers[1]);
});

for (const width of [1440, 390]) {
    test(`drawers cover service status and connection popover at ${width}px`, async ({ page, request }) => {
        await page.setViewportSize({ width, height: 1000 });
        const links = await (await request.get(fixture)).json();
        await page.goto(links.online);
        await expect(page.getByRole('status', { name: '服务连接状态', exact: true })).toHaveText('服务已连接');
        for (const [button, title] of [
            ['打开远程复位', '远程复位'], ['打开配置面板', '设备配置面板'],
            ['查看链路诊断', '链路诊断'], ['使用帮助', '使用帮助'],
        ]) {
            await page.getByRole('button', { name: '局域网连接', exact: true }).click();
            await page.getByRole('button', { name: button, exact: true }).click();
            const mobilePage = width <= 760 && ['设备配置面板', '链路诊断', '远程复位'].includes(title);
            const dialog = page.getByRole(mobilePage ? 'region' : 'dialog', { name: title, exact: true });
            await expect(dialog).toBeVisible();
            await page.evaluate(() => window.scrollTo(0, 0));
            // Test actual hit targets where the header content would otherwise obscure the drawer.
            await expect.poll(() => page.evaluate(() => {
                const backdrop = document.querySelector('.drawer-backdrop');
                return ['.lan-connection-state', '.lan-panel'].every(selector => {
                    const element = document.querySelector(selector)!;
                    const rect = element.getBoundingClientRect();
                    const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2));
                    const y = Math.max(0, Math.min(innerHeight - 1, rect.top + Math.min(rect.height / 2, 70)));
                    return backdrop?.contains(document.elementFromPoint(x, y));
                });
            })).toBe(true);
            if (title === '远程复位') await dialog.getByLabel('设备 IPv4 地址', { exact: true }).fill('192.168.4.1');
            await page.screenshot({ path: `test-results/drawer-${width}-${title}.png`, animations: 'disabled' });
            await dialog.getByRole('button', { name: mobilePage ? '返回工作台' : '关闭面板', exact: true }).click();
            await expect(dialog).not.toBeVisible();
            await page.getByRole('button', { name: '局域网连接', exact: true }).click();
        }
    });
}
