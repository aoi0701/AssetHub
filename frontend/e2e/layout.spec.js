import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const screenshotsDir = path.resolve(__dirname, '../e2e-screenshots');

if (!fs.existsSync(screenshotsDir)) {
  fs.mkdirSync(screenshotsDir, { recursive: true });
}

function doBoxesOverlap(b1, b2) {
  return !(
    b1.x + b1.width <= b2.x ||
    b2.x + b2.width <= b1.x ||
    b1.y + b1.height <= b2.y ||
    b2.y + b2.height <= b1.y
  );
}

test.describe('AssetHub Left Sidebar & Responsive Layout', () => {
  const seedPassword = process.env.E2E_SEED_PASSWORD || process.env.DEMO_SEED_PASSWORD;
  if (!seedPassword) {
    throw new Error('Missing E2E_SEED_PASSWORD or DEMO_SEED_PASSWORD environment variable.');
  }

  test.beforeEach(() => {
    try {
      execSync('docker compose exec -T backend python manage.py shell -c "from django.core.cache import cache; cache.clear()"', {
        stdio: 'ignore',
        timeout: 10000,
      });
    } catch {
      // ignore in environments without docker access
    }
  });

  async function loginAsAdmin(page) {
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);
    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    await expect(sidebar).toBeVisible();
  }

  async function waitForChartsReady(page) {
    // 1. Ensure Recharts responsive container and SVG surfaces are mounted
    await page.waitForSelector('.recharts-responsive-container', { state: 'visible', timeout: 15000 });
    await page.waitForSelector('.recharts-surface', { state: 'visible', timeout: 15000 });

    // 2. Wait for actual chart graphical elements to be attached
    await page.waitForSelector('.recharts-pie-sector, .recharts-bar-rectangle, .recharts-line', {
      state: 'attached',
      timeout: 15000,
    });

    // 3. Inject CSS to stop and freeze any remaining CSS animations or transitions
    await page.addStyleTag({
      content: `
        *, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
          transition-delay: 0s !important;
        }
      `,
    });

    // 4. Allow layout and rendering frames to settle completely
    await page.waitForTimeout(600);
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
      await waitForChartsReady(page);

      const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
      const main = page.locator('main');
      const logoLink = sidebar.locator('a[aria-label="AssetHub"]');
      const toggleBtn = sidebar.locator('button[aria-label*="thanh điều hướng"]');

      // 1. Expanded state verification
      await expect(sidebar).toBeVisible();
      await expect(main).toBeVisible();

      let sidebarBox = await sidebar.boundingBox();
      let mainBox = await main.boundingBox();
      let logoBox = await logoLink.boundingBox();
      let toggleBox = await toggleBtn.boundingBox();

      expect(sidebarBox).not.toBeNull();
      expect(mainBox).not.toBeNull();
      expect(logoBox).not.toBeNull();
      expect(toggleBox).not.toBeNull();

      // Bounding box constraint: sidebar never overlaps main
      expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);
      // Main is not clipped outside viewport
      expect(mainBox.x + mainBox.width).toBeLessThanOrEqual(vp.width + 1);

      // Verify logo and toggle button DO NOT intersect in expanded state
      expect(doBoxesOverlap(logoBox, toggleBox)).toBe(false);

      // Expanded width check (w-64 = 256px)
      expect(sidebarBox.width).toBeGreaterThanOrEqual(250);
      expect(sidebarBox.width).toBeLessThanOrEqual(265);

      // Toggle button attribute
      await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
      await expect(toggleBtn).toHaveAttribute('aria-label', 'Thu gọn thanh điều hướng');

      // Screenshot expanded state after chart settled
      await page.screenshot({
        path: path.join(screenshotsDir, `layout-${vp.width}x${vp.height}-expanded.png`),
        animations: 'disabled',
      });

      // 2. Toggle to Collapsed state
      await toggleBtn.click();
      await page.waitForTimeout(300); // wait for CSS width transition

      sidebarBox = await sidebar.boundingBox();
      mainBox = await main.boundingBox();
      logoBox = await logoLink.boundingBox();
      toggleBox = await toggleBtn.boundingBox();

      expect(sidebarBox).not.toBeNull();
      expect(mainBox).not.toBeNull();
      expect(logoBox).not.toBeNull();
      expect(toggleBox).not.toBeNull();

      // Bounding box constraint in collapsed state: sidebar never overlaps main
      expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);
      // Main is not clipped outside viewport
      expect(mainBox.x + mainBox.width).toBeLessThanOrEqual(vp.width + 1);

      // Verify logo and toggle button DO NOT intersect in collapsed state
      expect(doBoxesOverlap(logoBox, toggleBox)).toBe(false);

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

      // Screenshot collapsed state after chart settled
      await page.screenshot({
        path: path.join(screenshotsDir, `layout-${vp.width}x${vp.height}-collapsed.png`),
        animations: 'disabled',
      });

      // 3. Toggle back to Expanded
      await toggleBtn.click();
      await page.waitForTimeout(300);
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

    // 1. Verify header toggle and footer logout are initially visible within viewport
    await expect(toggleBtn).toBeVisible();
    await expect(logoutBtn).toBeVisible();

    // 2. Inject extra menu items to guarantee vertical scroll overflow
    await page.evaluate(() => {
      const nav = document.querySelector('nav[aria-label="Menu chính"]');
      if (nav) {
        for (let i = 1; i <= 15; i++) {
          const item = document.createElement('div');
          item.className = 'py-3 px-3 text-sm text-gray-500 rounded-lg border border-dashed border-gray-200';
          item.textContent = `Mục kiểm thử cuộn độc lập ${i}`;
          nav.appendChild(item);
        }
      }
    });

    // 3. Assert scrollHeight > clientHeight (guaranteed overflow)
    const scrollHeight = await navMenu.evaluate((el) => el.scrollHeight);
    const clientHeight = await navMenu.evaluate((el) => el.clientHeight);
    expect(scrollHeight).toBeGreaterThan(clientHeight);

    // 4. Capture positions of header toggle and footer logout BEFORE scrolling
    const toggleBoxBefore = await toggleBtn.boundingBox();
    const logoutBoxBefore = await logoutBtn.boundingBox();
    expect(toggleBoxBefore).not.toBeNull();
    expect(logoutBoxBefore).not.toBeNull();

    // 5. Scroll navMenu and assert scrollTop > 0
    await navMenu.evaluate((el) => {
      el.scrollTop = 70;
    });
    const scrolledTop = await navMenu.evaluate((el) => el.scrollTop);
    expect(scrolledTop).toBeGreaterThan(0);

    // 6. Assert header and footer remain fixed in position when menu scrolls
    const toggleBoxAfter = await toggleBtn.boundingBox();
    const logoutBoxAfter = await logoutBtn.boundingBox();
    expect(toggleBoxAfter).not.toBeNull();
    expect(logoutBoxAfter).not.toBeNull();

    expect(toggleBoxAfter.y).toBe(toggleBoxBefore.y);
    expect(logoutBoxAfter.y).toBe(logoutBoxBefore.y);
    expect(toggleBoxAfter.y).toBeGreaterThanOrEqual(0);
    expect(logoutBoxAfter.y + logoutBoxAfter.height).toBeLessThanOrEqual(480);

    // Bounding box constraint in short viewport
    const sidebarBox = await sidebar.boundingBox();
    const mainBox = await page.locator('main').boundingBox();
    expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);

    // Screenshot short viewport expanded with animations disabled
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1280x480-expanded.png'),
      animations: 'disabled',
    });

    // Toggle to collapsed in short viewport
    await toggleBtn.click();
    await page.waitForTimeout(300);

    // Screenshot short viewport collapsed with animations disabled
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1280x480-collapsed.png'),
      animations: 'disabled',
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
    const pageHeading = page.getByRole('heading', { name: 'Quản lý tài sản' });
    await expect(pageHeading).toBeVisible();

    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    const main = page.locator('main');
    const tableWrapper = page.locator('main .overflow-x-auto');
    const table = tableWrapper.locator('table');

    await expect(tableWrapper).toBeVisible();
    await expect(table).toBeVisible();

    // 1. Inject extra wide columns to guarantee horizontal scroll overflow
    await page.evaluate(() => {
      const tbl = document.querySelector('main .overflow-x-auto table');
      if (tbl) {
        const theadRow = tbl.querySelector('thead tr');
        const tbodyRows = tbl.querySelectorAll('tbody tr');
        if (theadRow) {
          for (let i = 1; i <= 6; i++) {
            const th = document.createElement('th');
            th.className = 'px-8 py-3 whitespace-nowrap min-w-[200px] font-semibold text-gray-700';
            th.textContent = `Thông số kỹ thuật mở rộng ${i}`;
            theadRow.appendChild(th);
          }
          tbodyRows.forEach((row) => {
            for (let i = 1; i <= 6; i++) {
              const td = document.createElement('td');
              td.className = 'px-8 py-4 whitespace-nowrap min-w-[200px] text-gray-600 text-sm';
              td.textContent = `Dữ liệu đo đạc chi tiết cột ${i}`;
              row.appendChild(td);
            }
          });
        }
      }
    });

    // 2. Assert scrollWidth > clientWidth (guaranteed horizontal overflow)
    const tableScrollWidth = await tableWrapper.evaluate((el) => el.scrollWidth);
    const tableClientWidth = await tableWrapper.evaluate((el) => el.clientWidth);
    expect(tableScrollWidth).toBeGreaterThan(tableClientWidth);

    // 3. Record position of page heading and sidebar before scrolling table
    const headingBoxBefore = await pageHeading.boundingBox();
    const sidebarBoxBefore = await sidebar.boundingBox();
    expect(headingBoxBefore).not.toBeNull();
    expect(sidebarBoxBefore).not.toBeNull();

    // 4. Scroll table and assert scrollLeft > 0
    await tableWrapper.evaluate((el) => {
      el.scrollLeft = 80;
    });
    const scrolledLeft = await tableWrapper.evaluate((el) => el.scrollLeft);
    expect(scrolledLeft).toBeGreaterThan(0);

    // 5. Assert page heading and sidebar header remain fixed (unaffected by table horizontal scroll)
    const headingBoxAfter = await pageHeading.boundingBox();
    const sidebarBoxAfter = await sidebar.boundingBox();
    expect(headingBoxAfter.y).toBe(headingBoxBefore.y);
    expect(headingBoxAfter.x).toBe(headingBoxBefore.x);
    expect(sidebarBoxAfter.x).toBe(sidebarBoxBefore.x);
    expect(sidebarBoxAfter.y).toBe(sidebarBoxBefore.y);

    // Verify bounding box constraint
    const sidebarBox = await sidebar.boundingBox();
    const mainBox = await main.boundingBox();
    expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x + 1);
    expect(mainBox.x + mainBox.width).toBeLessThanOrEqual(1024 + 1);

    // Verify no unintended root horizontal overflow
    const hasUnintendedHorizontalScroll = await page.evaluate(() => {
      return document.documentElement.scrollWidth > window.innerWidth + 2;
    });
    expect(hasUnintendedHorizontalScroll).toBe(false);

    // Capture screenshot of table page with animations disabled
    await page.screenshot({
      path: path.join(screenshotsDir, 'layout-1024x768-table.png'),
      animations: 'disabled',
    });
  });

  test('localStorage state persistence across in-app navigation', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    const sidebar = page.locator('aside[aria-label="Thanh điều hướng chính"]');
    const toggleBtn = sidebar.locator('button[aria-label*="thanh điều hướng"]');

    // Collapse sidebar on dashboard
    await toggleBtn.click();
    await page.waitForTimeout(300);
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
    await page.waitForTimeout(300);
    sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox.width).toBeGreaterThanOrEqual(250);
  });

  test('authenticated user accessing /login and /register renders guest layout without sidebar', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    // Helper for internal SPA navigation to keep in-memory tokens & auth state intact
    const spaNavigate = async (targetPath) => {
      await page.evaluate((path) => {
        window.history.pushState({}, '', path);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, targetPath);
    };

    // Navigate to /login via internal SPA navigation while authenticated
    await spaNavigate('/login');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('aside[aria-label="Thanh điều hướng chính"]')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Đăng nhập AssetHub' })).toBeVisible();

    // Navigate to /register via internal SPA navigation while authenticated
    await spaNavigate('/register');
    await expect(page).toHaveURL(/\/register/);
    await expect(page.locator('aside[aria-label="Thanh điều hướng chính"]')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Đăng ký tài khoản' })).toBeVisible();

    // Return to /dashboard via SPA navigation to confirm in-memory auth session remained alive
    await spaNavigate('/dashboard');
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.locator('aside[aria-label="Thanh điều hướng chính"]')).toBeVisible();
  });
});
