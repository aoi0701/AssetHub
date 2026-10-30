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

test.describe('Categories Page UI/UX and Linear SaaS Design', () => {
  const seedPassword = process.env.E2E_SEED_PASSWORD || process.env.DEMO_SEED_PASSWORD;
  if (!seedPassword) {
    throw new Error('Missing E2E_SEED_PASSWORD or DEMO_SEED_PASSWORD environment variable.');
  }

  test.beforeEach(() => {
    try {
      execSync(
        'docker compose exec -T backend python manage.py shell -c "from django.core.cache import cache; cache.clear()"',
        { stdio: 'ignore', timeout: 10000 }
      );
    } catch {
      // ignore
    }
  });

  test('categories page renders cleanly on desktop with Linear SaaS styling, search and accessible modal', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);

    // Navigate to /categories
    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Danh mục' }).click();
    await expect(page).toHaveURL(/\/categories/);

    // Verify Title and subtitle
    const heading = page.getByRole('heading', { name: 'Danh mục tài sản' });
    await expect(heading).toBeVisible();
    await expect(page.getByText('Phân loại và định danh nhóm tài sản phục vụ quản lý, cấp phát và bảo trì.')).toBeVisible();

    // Verify Add Button on desktop
    const addBtn = page.getByRole('button', { name: 'Thêm danh mục' });
    await expect(addBtn).toBeVisible();

    // Verify search and refresh toolbar
    const searchInput = page.getByPlaceholder('Tìm theo tên hoặc mô tả danh mục...');
    await expect(searchInput).toBeVisible();
    const refreshBtn = page.getByRole('button', { name: 'Làm mới' });
    await expect(refreshBtn).toBeVisible();

    // Verify table structure
    const table = page.locator('table');
    await expect(table).toBeVisible();
    await expect(page.locator('th', { hasText: 'Tên danh mục' })).toBeVisible();
    await expect(page.locator('th', { hasText: 'Mô tả' })).toBeVisible();
    await expect(page.locator('th', { hasText: 'Thao tác' })).toBeVisible();

    // Verify NO delete button anywhere
    expect(await page.locator('button:has-text("Xóa"), button:has-text("Delete")').count()).toBe(0);

    // Capture desktop screenshot
    await page.screenshot({
      path: path.join(screenshotsDir, 'categories-desktop-1366x768.png'),
      animations: 'disabled',
    });

    // Open Modal
    await addBtn.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Thêm danh mục mới' })).toBeVisible();

    // Verify auto focus on name input
    const nameInput = dialog.locator('#cat_name');
    await expect(nameInput).toBeFocused();

    // Capture modal screenshot
    await page.screenshot({
      path: path.join(screenshotsDir, 'categories-modal-desktop.png'),
      animations: 'disabled',
    });

    // Close on Escape
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    // Verify focus returned to add button
    await expect(addBtn).toBeFocused();
  });

  test('categories page is responsive on mobile viewport (375x667)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);

    // Navigate to /categories
    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Danh mục' }).click();
    await expect(page).toHaveURL(/\/categories/);

    const heading = page.getByRole('heading', { name: 'Danh mục tài sản' });
    await expect(heading).toBeVisible();

    // Capture mobile screenshot
    await page.screenshot({
      path: path.join(screenshotsDir, 'categories-mobile-375x667.png'),
      animations: 'disabled',
    });
  });
});
