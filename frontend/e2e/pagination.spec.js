import { test, expect } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test.describe('Management Pages Backend Pagination & Dropdown Verification', () => {
  const seedPassword = process.env.E2E_SEED_PASSWORD || process.env.DEMO_SEED_PASSWORD;
  if (!seedPassword) {
    throw new Error('Missing E2E_SEED_PASSWORD or DEMO_SEED_PASSWORD environment variable.');
  }

  test.beforeEach(() => {
    try {
      const projectRoot = path.resolve(__dirname, '../..');
      execSync('docker compose exec -T backend python -c "import django, os; os.environ.setdefault(\'DJANGO_SETTINGS_MODULE\', \'assethub_api.settings\'); django.setup(); from django.core.cache import cache; cache.clear()"', {
        cwd: projectRoot,
        stdio: 'ignore',
        timeout: 10000,
      });
    } catch {
      // ignore
    }
  });

  async function loginAsAdmin(page) {
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);
  }

  test('categories page renders pagination and search resets to page 1', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Danh mục' }).click();
    await expect(page).toHaveURL(/\/categories/);
    await expect(page.getByRole('heading', { name: 'Danh mục tài sản' })).toBeVisible();

    const pagination = page.locator('nav[aria-label="Phân trang"]');
    await expect(pagination).toBeVisible();
    await expect(pagination).toContainText(/Đang xem \d+–\d+ trên tổng \d+ bản ghi/);

    // Search filter resets page to 1
    const searchInput = page.getByPlaceholder(/Tìm theo tên hoặc mô tả/i);
    await searchInput.fill('Laptop');
    await expect(pagination).toContainText(/Đang xem 1–/);
  });

  test('assets page renders pagination and filter change resets to page 1', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Tài sản' }).click();
    await expect(page).toHaveURL(/\/assets/);
    await expect(page.getByRole('heading', { name: 'Quản lý tài sản' })).toBeVisible();

    const pagination = page.locator('nav[aria-label="Phân trang"]');
    await expect(pagination).toBeVisible();
    await expect(pagination).toContainText(/Đang xem 1–\d+ trên tổng \d+ bản ghi/);

    // Status filter resets page to 1
    const statusSelect = page.getByLabel('Lọc theo trạng thái');
    await statusSelect.selectOption('AVAILABLE');
    await expect(pagination).toContainText(/Đang xem 1–/);
  });

  test('requests inbox page renders pagination and action buttons are not clipped', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await loginAsAdmin(page);

    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Yêu cầu' }).click();
    await expect(page).toHaveURL(/\/requests/);
    await expect(page.getByRole('heading', { name: 'Quản lý yêu cầu tài sản' })).toBeVisible();

    const pagination = page.locator('nav[aria-label="Phân trang"]');
    await expect(pagination).toBeVisible();
    await expect(pagination).toContainText(/Đang xem 1–\d+ trên tổng \d+ bản ghi/);

    // Verify action column header and buttons fit without clipping
    const actionTh = page.locator('th:has-text("Thao tác")');
    await expect(actionTh).toBeVisible();
    const actionThBox = await actionTh.boundingBox();
    expect(actionThBox.x + actionThBox.width).toBeLessThanOrEqual(1920);

    const firstRowDetailBtn = page.locator('tbody tr').first().getByRole('button', { name: 'Chi tiết' });
    await expect(firstRowDetailBtn).toBeVisible();
    const detailBox = await firstRowDetailBtn.boundingBox();
    expect(detailBox.x + detailBox.width).toBeLessThanOrEqual(1920);
  });

  test('dropdowns in create asset modal load complete list without pagination cutoff', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsAdmin(page);

    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Tài sản' }).click();
    await expect(page).toHaveURL(/\/assets/);

    const createBtn = page.getByRole('button', { name: 'Tạo tài sản mới' });
    await createBtn.click();

    const modal = page.locator('div[role="dialog"]');
    await expect(modal).toBeVisible();

    const categorySelect = page.locator('#asset-category');
    await expect(categorySelect).toBeVisible();
    const options = await categorySelect.locator('option').count();
    expect(options).toBeGreaterThan(1); // placeholder + seeded categories
  });

  async function loginAsUser(page) {
    await page.goto('/login');
    await page.fill('#username', 'truong');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/my-assets/);
  }

  test('personal asset history paginates 1 -> 2 -> 1 across 25 events using isolated fixture', async ({ page }) => {
    // 25 isolated events in fixture
    const fixtureEvents = Array.from({ length: 25 }, (_, i) => ({
      id: 500 + i + 1,
      asset_id: 100 + i + 1,
      asset_code_snapshot: `AST-E2E-HIST-${String(i + 1).padStart(3, '0')}`,
      asset_name_snapshot: `Thiết bị E2E #${i + 1}`,
      action_type: i % 2 === 0 ? 'ALLOCATE' : 'RETURN',
      from_status: i % 2 === 0 ? 'AVAILABLE' : 'IN_USE',
      to_status: i % 2 === 0 ? 'IN_USE' : 'AVAILABLE',
      created_at: `2026-10-${String(Math.min(28, (i % 28) + 1)).padStart(2, '0')}T10:00:00Z`,
      message: i % 2 === 0 ? 'Tài sản được cấp phát cho bạn' : 'Bạn đã hoàn trả tài sản',
    }));

    // Intercept /api/me/asset-history/ to use the isolated 25-event fixture
    await page.route('**/api/me/asset-history/**', async (route) => {
      const url = new URL(route.request().url());
      const pageNum = parseInt(url.searchParams.get('page') || '1', 10);
      const pageSize = parseInt(url.searchParams.get('page_size') || '20', 10);

      const start = (pageNum - 1) * pageSize;
      const end = start + pageSize;
      const slice = fixtureEvents.slice(start, end);

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          count: fixtureEvents.length,
          total_pages: Math.ceil(fixtureEvents.length / pageSize),
          current_page: pageNum,
          results: slice,
        }),
      });
    });

    await page.setViewportSize({ width: 1366, height: 768 });
    await loginAsUser(page);

    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Lịch sử' }).click();
    await expect(page).toHaveURL(/\/my-history/);
    await expect(page.getByRole('heading', { name: 'Nhật ký tài sản cá nhân' })).toBeVisible();

    const pagination = page.locator('nav[aria-label="Phân trang"]');
    await expect(pagination).toBeVisible();

    // 1. Check Page 1: 20 items (#1 to #20), #21 not present
    await expect(pagination).toContainText('Đang xem 1–20 trên tổng 25 bản ghi');
    await expect(page.getByText('Thiết bị E2E #1').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #20').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #21')).toHaveCount(0);
    const rowsP1 = await page.locator('tbody tr').count();
    expect(rowsP1).toBe(20);

    const prevBtn = pagination.getByRole('button', { name: 'Trang trước' });
    const nextBtn = pagination.getByRole('button', { name: 'Trang sau' });
    await expect(prevBtn).toBeDisabled();
    await expect(nextBtn).toBeEnabled();

    // 2. Click Next -> Go to Page 2
    await nextBtn.click();

    // Check Page 2: 5 items (#21 to #25), #1 not present
    await expect(pagination).toContainText('Đang xem 21–25 trên tổng 25 bản ghi');
    await expect(page.getByText('Thiết bị E2E #21').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #25').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #1')).toHaveCount(0);
    const rowsP2 = await page.locator('tbody tr').count();
    expect(rowsP2).toBe(5);

    await expect(prevBtn).toBeEnabled();
    await expect(nextBtn).toBeDisabled();

    // 3. Click Prev -> Back to Page 1
    await prevBtn.click();

    // Check Page 1 again: 20 items restored
    await expect(pagination).toContainText('Đang xem 1–20 trên tổng 25 bản ghi');
    await expect(page.getByText('Thiết bị E2E #1').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #20').first()).toBeVisible();
    await expect(page.getByText('Thiết bị E2E #21')).toHaveCount(0);
    const rowsP1Again = await page.locator('tbody tr').count();
    expect(rowsP1Again).toBe(20);
  });
});
