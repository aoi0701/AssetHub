import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const screenshotsDir = path.resolve(__dirname, '../e2e-screenshots');

test.describe('Requests Page Table Layout', () => {
  const seedPassword = process.env.E2E_SEED_PASSWORD || process.env.DEMO_SEED_PASSWORD;
  if (!seedPassword) {
    throw new Error('Missing E2E_SEED_PASSWORD or DEMO_SEED_PASSWORD environment variable.');
  }

  test('requests table action column does not overflow and buttons do not clip', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/dashboard/);

    // Navigate to /requests
    await page.locator('aside[aria-label="Thanh điều hướng chính"]').getByRole('link', { name: 'Yêu cầu' }).click();
    await expect(page).toHaveURL(/\/requests/);
    await expect(page.getByRole('heading', { name: 'Quản lý yêu cầu tài sản' })).toBeVisible();

    const tableWrapper = page.locator('main .overflow-x-auto');
    await expect(tableWrapper).toBeVisible();

    const actionTh = page.locator('th:has-text("Thao tác")');
    await expect(actionTh).toBeVisible();

    // Check first row if it has Xét duyệt and Chi tiết
    const firstRow = page.locator('tbody tr').first();
    await expect(firstRow).toBeVisible();

    const reviewBtn = firstRow.getByRole('button', { name: 'Xét duyệt' });
    const detailBtn = firstRow.getByRole('button', { name: 'Chi tiết' });

    if (await reviewBtn.count() > 0) {
      await expect(reviewBtn).toBeVisible();
      await expect(detailBtn).toBeVisible();

      const reviewBox = await reviewBtn.boundingBox();
      const detailBox = await detailBtn.boundingBox();
      const cellBox = await firstRow.locator('td').last().boundingBox();
      const wrapperBox = await tableWrapper.boundingBox();

      expect(reviewBox).not.toBeNull();
      expect(detailBox).not.toBeNull();
      expect(cellBox).not.toBeNull();
      expect(wrapperBox).not.toBeNull();

      // Assert detail button is not clipped outside the cell
      expect(detailBox.x + detailBox.width).toBeLessThanOrEqual(cellBox.x + cellBox.width + 1);

      // Assert detail button is within the table wrapper viewport boundary
      expect(detailBox.x + detailBox.width).toBeLessThanOrEqual(wrapperBox.x + wrapperBox.width + 1);

      // Assert review button and detail button do not overlap
      expect(reviewBox.x + reviewBox.width).toBeLessThanOrEqual(detailBox.x + 1);
    }

    // Capture screenshot of the fixed requests table
    await page.screenshot({
      path: path.join(screenshotsDir, 'requests-table-layout.png'),
      animations: 'disabled',
    });
  });
});
