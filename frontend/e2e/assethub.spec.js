import { test, expect } from '@playwright/test';

test.describe('AssetHub End-to-End Lifecycle', () => {
  const timestamp = Date.now();
  const seedPassword = process.env.E2E_SEED_PASSWORD || process.env.DEMO_SEED_PASSWORD;
  if (!seedPassword) {
    throw new Error('Missing E2E_SEED_PASSWORD or DEMO_SEED_PASSWORD environment variable.');
  }
  const testUser = {
    username: `e2e_user_${timestamp}`,
    email: `e2e_${timestamp}@example.com`,
    fullName: `E2E Test User ${timestamp}`,
    phone: '0912345678',
    password: 'E2ePassword123!',
  };

  test('full end-to-end user lifecycle, request workflow, and access control', async ({ page }) => {
    // -------------------------------------------------------------------------
    // 1. User Registration
    // -------------------------------------------------------------------------
    await page.goto('/register');
    await expect(page.getByRole('heading', { name: 'Đăng ký tài khoản' })).toBeVisible();

    await page.fill('#username', testUser.username);
    await page.fill('#email', testUser.email);
    await page.fill('#full_name', testUser.fullName);
    await page.fill('#phone', testUser.phone);
    await page.fill('#password', testUser.password);
    await page.fill('#password_confirm', testUser.password);

    await page.click('button[type="submit"]');

    // Shows pending approval notice
    await expect(page.getByText('Đăng ký thành công!')).toBeVisible();
    await expect(
      page.getByText('Tài khoản đang chờ Quản trị viên kích hoạt trước khi có thể đăng nhập.')
    ).toBeVisible();

    // -------------------------------------------------------------------------
    // 2. Admin Activation
    // -------------------------------------------------------------------------
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/dashboard/);
    await page.getByRole('link', { name: 'Người dùng' }).click();
    await expect(page).toHaveURL(/\/users/);
    await expect(page.getByRole('heading', { name: 'Quản lý người dùng' })).toBeVisible();

    // Search for newly registered user
    const searchInput = page.locator('input[placeholder*="Tìm theo tên"]');
    await searchInput.fill(testUser.username);
    await page.waitForTimeout(500); // debounce / filter

    const userRow = page.locator('tbody tr', { hasText: testUser.username });
    await expect(userRow).toBeVisible();
    await expect(userRow.getByText('Vô hiệu hóa')).toBeVisible();

    // Edit user to assign IT department
    await userRow.getByRole('button', { name: 'Sửa' }).click();
    const editModal = page.getByRole('dialog');
    await expect(editModal).toBeVisible();

    // Select IT department (Option with IT / Information Technology)
    const deptSelect = editModal.locator('#modal_department');
    await deptSelect.selectOption({ label: 'Information Technology (IT)' });
    await editModal.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await expect(editModal).toBeHidden();

    // Now activate user
    await userRow.getByRole('button', { name: 'Kích hoạt' }).click();
    await expect(userRow.getByText('Hoạt động')).toBeVisible();

    // Admin Logout
    await page.getByRole('button', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);

    // -------------------------------------------------------------------------
    // 3. User Login & Request (ALLOCATE)
    // -------------------------------------------------------------------------
    await page.fill('#username', testUser.username);
    await page.fill('#password', testUser.password);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/my-assets/);
    await expect(page.getByRole('heading', { name: 'Tài sản của tôi' })).toBeVisible();

    // Submits ALLOCATE request for Laptop
    await page.getByRole('button', { name: 'Gửi yêu cầu cấp mới' }).click();
    const createReqModal = page.getByRole('dialog');
    await expect(createReqModal).toBeVisible();

    // Select Laptop category
    const catSelect = createReqModal.locator('#requested-category');
    await catSelect.selectOption({ label: 'Laptop' });

    await createReqModal.locator('#request-reason').fill('Cần cấp phát laptop phục vụ dự án kiểm thử E2E');
    await createReqModal.getByRole('button', { name: 'Gửi yêu cầu' }).click();
    await expect(createReqModal).toBeHidden();

    await expect(page.getByText('Gửi yêu cầu thành công. Vui lòng chờ quản lý phê duyệt.')).toBeVisible();

    // User Logout
    await page.getByRole('button', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);

    // -------------------------------------------------------------------------
    // 4. Manager Review & Approval (ALLOCATE)
    // -------------------------------------------------------------------------
    await page.fill('#username', 'manager_it');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/dashboard/);
    await page.getByRole('link', { name: 'Yêu cầu' }).click();
    await expect(page).toHaveURL(/\/requests/);
    await expect(page.getByRole('heading', { name: 'Quản lý yêu cầu tài sản' })).toBeVisible();

    // Locate the pending request from our user
    const allocReqRow = page.locator('tbody tr', { hasText: testUser.fullName }).first();
    await expect(allocReqRow).toBeVisible();
    await expect(allocReqRow.getByText('Chờ duyệt')).toBeVisible();

    await allocReqRow.getByRole('button', { name: 'Xét duyệt' }).click();
    const reviewModal = page.getByRole('dialog');
    await expect(reviewModal).toBeVisible();

    // Select available laptop
    const assetSelect = reviewModal.locator('#review-select-asset');
    await expect(assetSelect).toBeVisible();
    await assetSelect.selectOption({ index: 1 }); // select first available laptop

    await reviewModal.getByRole('button', { name: 'Xác nhận duyệt' }).click();
    await expect(reviewModal).toBeHidden();
    await expect(page.getByText('Xử lý phiếu yêu cầu thành công.')).toBeVisible();

    // Manager Logout
    await page.getByRole('button', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);

    // -------------------------------------------------------------------------
    // 5. User Asset Verification & Return Request
    // -------------------------------------------------------------------------
    await page.fill('#username', testUser.username);
    await page.fill('#password', testUser.password);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/my-assets/);
    const assetRow = page.locator('tbody tr').first();
    await expect(assetRow).toBeVisible();
    await expect(assetRow.getByText('Đang sử dụng')).toBeVisible();

    // Submit RETURN request
    await assetRow.getByRole('button', { name: 'Trả tài sản' }).click();
    const returnModal = page.getByRole('dialog');
    await expect(returnModal).toBeVisible();

    await returnModal.locator('#request-reason').fill('Hoàn thành nhiệm vụ, hoàn trả máy tính cho phòng ban');
    await returnModal.getByRole('button', { name: 'Gửi yêu cầu' }).click();
    await expect(returnModal).toBeHidden();
    await expect(page.getByText('Gửi yêu cầu thành công. Vui lòng chờ quản lý phê duyệt.')).toBeVisible();

    // User Logout
    await page.getByRole('button', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);

    // -------------------------------------------------------------------------
    // 6. Manager Return Approval
    // -------------------------------------------------------------------------
    await page.fill('#username', 'manager_it');
    await page.fill('#password', seedPassword);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/dashboard/);
    await page.getByRole('link', { name: 'Yêu cầu' }).click();
    await expect(page).toHaveURL(/\/requests/);

    // Locate the pending RETURN request
    const returnReqRow = page.locator('tbody tr', { hasText: 'Hoàn trả tài sản' })
      .filter({ hasText: testUser.fullName })
      .filter({ hasText: 'Chờ duyệt' })
      .first();
    await expect(returnReqRow).toBeVisible();

    await returnReqRow.getByRole('button', { name: 'Xét duyệt' }).click();
    const returnReviewModal = page.getByRole('dialog');
    await expect(returnReviewModal).toBeVisible();

    // Check confirmation and condition GOOD
    await returnReviewModal.locator('#review-received-confirmed').check();
    await expect(returnReviewModal.locator('#review-condition')).toHaveValue('GOOD');

    await returnReviewModal.getByRole('button', { name: 'Xác nhận duyệt' }).click();
    await expect(returnReviewModal).toBeHidden();
    await expect(page.getByText('Xử lý phiếu yêu cầu thành công.')).toBeVisible();

    // Manager Logout
    await page.getByRole('button', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);

    // -------------------------------------------------------------------------
    // 7. Personal History Verification
    // -------------------------------------------------------------------------
    await page.fill('#username', testUser.username);
    await page.fill('#password', testUser.password);
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL(/\/my-assets/);
    await page.getByRole('link', { name: 'Lịch sử' }).click();
    await expect(page).toHaveURL(/\/my-history/);
    await expect(page.getByRole('heading', { name: 'Nhật ký tài sản cá nhân' })).toBeVisible();

    // Verify both ALLOCATE and RETURN events are visible even after return
    await expect(page.getByText('Cấp phát').first()).toBeVisible();
    await expect(page.getByText('Hoàn trả').first()).toBeVisible();

    // -------------------------------------------------------------------------
    // 8. Role Scoping & Protection
    // -------------------------------------------------------------------------
    // User attempts to navigate directly to /users -> 403 Forbidden
    await page.evaluate((path) => {
      window.history.pushState({}, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, '/users');
    await expect(page.getByText('403 - Truy cập bị từ chối')).toBeVisible();

    // User attempts to navigate directly to /dashboard -> 403 Forbidden
    await page.evaluate((path) => {
      window.history.pushState({}, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, '/dashboard');
    await expect(page.getByText('403 - Truy cập bị từ chối')).toBeVisible();
  });
});
