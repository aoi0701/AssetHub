import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const screenshotsDir = path.resolve(__dirname, '../e2e-screenshots');

if (!fs.existsSync(screenshotsDir)) {
  fs.mkdirSync(screenshotsDir, { recursive: true });
}

test.describe('AssetHub Left Sidebar & Responsive Layout', () => {
  const seedPassword = process.env.E2E_SEED_PASSWORD || 'aoi0701@';

  async function loginAsAdmin(page) {
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);
    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    await expect(sidebar).toBeVisible();
  }

  const standardViewports = [
    { width: 1024, height: 768, label: '1024x768' },
    { width: 1366, height: 768, label: '1366x768' },
    { width: 1920, height: 1080, label: '1920x1080' },
  ];

  for (const vp of standardViewports) {
    test(`renders sidebar & main layout correctly at ${vp.label} (expanded & collapsed)`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await loginAsAdmin(page);

      const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
      const main = page.locator('main');
      const toggleBtn = sidebar.locator('button[aria-label*="thanh điều hướng"]');

      // 1. Expanded state verification
      await expect(sidebar).toBeVisible();
      await expect(main).toBeVisible();

      let sidebarBox = await sidebar.boundingBox();
      let mainBox = await main.boundingBox();
      expect(sidebarBox).not.toBeNull();
      expect(mainBox).not.toBeNull();

      // Bounding box constraint: sidebar never overlaps main
      expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);

      // Expanded width check (w-64 = 256px)
      expect(sidebarBox.width).toBeGreaterThanOrEqual(250);
      expect(sidebarBox.width).toBeLessThanOrEqual(265);

      // Toggle button attribute
      await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
      await expect(toggleBtn).toHaveAttribute('aria-label', 'Thu gọn thanh điều hướng');

      // Screenshot expanded state
      await page.screenshot({
        path: path.join(screenshotsDir, `layout-${vp.width}x${vp.height}-expanded.png`),
      });

      // 2. Toggle to Collapsed state
      await toggleBtn.click();
      await page.waitForTimeout(250); // wait for CSS width transition

      sidebarBox = await sidebar.boundingBox();
      mainBox = await main.boundingBox();
      expect(sidebarBox).not.toBeNull();
      expect(mainBox).not.toBeNull();

      // Bounding box constraint in collapsed state
      expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);

      // Collapsed width check (w-20 = 80px)
      expect(sidebarBox.width).toBeGreaterThanOrEqual(75);
      expect(sidebarBox.width).toBeLessThanOrEqual(85);

      // Toggle button attribute updated
      await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
      await expect(toggleBtn).toHaveAttribute('aria-label', 'Mở rộng thanh điều hướng');

      // Check localStorage preference saved
      const savedCollapsed = await page.evaluate(() => {
        return localStorage.getItem('assethub_sidebar_collapsed');
      });
      expect(savedCollapsed).toBe('true');

      // Screenshot collapsed state
      await page.screenshot({
        path: path.join(screenshotsDir, `layout-${vp.width}x${vp.height}-collapsed.png`),
      });

      // 3. Toggle back to Expanded
      await toggleBtn.click();
      await page.waitForTimeout(250);
      sidebarBox = await sidebar.boundingBox();
      expect(sidebarBox.width).toBeGreaterThanOrEqual(250);
      expect(sidebarBox.width).toBeLessThanOrEqual(265);

      const savedExpanded = await page.evaluate(() => {
        return localStorage.getItem('assethub_sidebar_collapsed');
      });
      expect(savedExpanded).toBe('false');
    });
  }

  test('short viewport (1280x480): sidebar menu scrolls independently with sticky header and footer', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 480 });
    await loginAsAdmin(page);

    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    const toggleBtn = sidebar.locator('button[aria-label*="thanh điều hướng"]');
    const navMenu = sidebar.locator('nav[aria-label="Menu chính"]');
    const logoutBtn = sidebar.locator('button[aria-label="Đăng xuất"]');

    // Verify header toggle is visible and fully within viewport height
    await expect(toggleBtn).toBeVisible();
    const toggleBox = await toggleBtn.boundingBox();
    expect(toggleBox).not.toBeNull();
    expect(toggleBox.y).toBeGreaterThanOrEqual(0);
    expect(toggleBox.y + toggleBox.height).toBeLessThanOrEqual(480);

    // Verify footer logout button is visible and fully within viewport height
    await expect(logoutBtn).toBeVisible();
    const logoutBox = await logoutBtn.boundingBox();
    expect(logoutBox).not.toBeNull();
    expect(logoutBox.y).toBeGreaterThanOrEqual(0);
    expect(logoutBox.y + logoutBox.height).toBeLessThanOrEqual(480);

    // Verify independent menu scrollability
    await expect(navMenu).toBeVisible();
    const menuScrollInfo = await navMenu.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return {
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        overflowY: style.overflowY,
      };
    });

    expect(['auto', 'scroll']).toContain(menuScrollInfo.overflowY);
    expect(menuScrollInfo.scrollHeight).toBeGreaterThanOrEqual(menuScrollInfo.clientHeight);

    // Bounding box constraint in short viewport
    const sidebarBox = await sidebar.boundingBox();
    const mainBox = await page.locator('main').boundingBox();
    expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);

    // Screenshot short viewport expanded
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1280x480-expanded.png'),
    });

    // Toggle to collapsed in short viewport
    await toggleBtn.click();
    await page.waitForTimeout(250);

    // Screenshot short viewport collapsed
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1280x480-collapsed.png'),
    });

    // Header toggle and footer logout remain visible and accessible in collapsed state
    await expect(toggleBtn).toBeVisible();
    await expect(logoutBtn).toBeVisible();
  });

  test('table containment: asset list table is wrapped in overflow-x-auto and does not overflow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await loginAsAdmin(page);

    // Navigate to /assets
    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Tài sản' }).click();
    await expect(page).toHaveURL(/\/assets/);
    await expect(page.getByRole('heading', { name: 'Quản lý tài sản' })).toBeVisible();

    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    const main = page.locator('main');
    const tableWrapper = page.locator('main .overflow-x-auto');
    const table = tableWrapper.locator('table');

    await expect(tableWrapper).toBeVisible();
    await expect(table).toBeVisible();

    // Verify bounding box constraint
    const sidebarBox = await sidebar.boundingBox();
    const mainBox = await main.boundingBox();
    expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);

    // Verify no unintended root horizontal overflow
    const hasUnintendedHorizontalScroll = await page.evaluate(() => {
      return document.documentElement.scrollWidth > window.innerWidth + 2;
    });
    expect(hasUnintendedHorizontalScroll).toBe(false);

    // Capture screenshot of table page
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1024x768-table.png'),
    });
  });

  test('localStorage state persistence across in-app navigation', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    const toggleBtn = sidebar.locator('button[aria-label*="thanh điều hướng"]');

    // Collapse sidebar on dashboard
    await toggleBtn.click();
    await page.waitForTimeout(250);
    let sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox.width).toBeLessThanOrEqual(85);

    // Navigate to /requests via collapsed icon
    await sidebar.getByRole('link', { name: 'Yêu cầu' }).click();
    await expect(page).toHaveURL(/\/requests/);

    // Verify sidebar remains collapsed after navigation
    sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox.width).toBeLessThanOrEqual(85);
    await expect(sidebar.locator('button[aria-label="Mở rộng thanh điều hướng"]')).toBeVisible();

    // Expand sidebar back
    await sidebar.locator('button[aria-label="Mở rộng thanh điều hướng"]').click();
    await page.waitForTimeout(250);
    sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox.width).toBeGreaterThanOrEqual(250);
  });
});
