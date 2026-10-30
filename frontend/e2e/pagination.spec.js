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
});
