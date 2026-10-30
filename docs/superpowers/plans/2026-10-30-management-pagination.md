# Quản lý phân trang Backend & Frontend cho AssetHub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm cơ chế phân trang phía backend (tối đa 15 bản ghi/trang) và tích hợp component phân trang dùng chung cho 5 trang quản lý (Assets, Requests, Users, Departments, Categories) với khả năng tìm kiếm/lọc toàn bộ dữ liệu, tự động quay về trang 1 khi lọc, và giữ nguyên dữ liệu đầy đủ cho dropdowns.

**Architecture:**
- Backend: Cung cấp `StandardPagination` kế thừa `PageNumberPagination` với `page_size = 15`, trả về `count`, `total_pages`, `current_page`, `page_size`, `next`, `previous`, `results`. Hỗ trợ `optional_pagination` cho `CategoryViewSet` và `DepartmentViewSet` và tham số `all=true` / `pagination=false` để các dropdown và test hiện tại luôn nhận đủ danh sách mà không bị ngắt quãng.
- Frontend: Tạo component dùng chung `Pagination.jsx` hiển thị định dạng “Đang xem {start}–{end} trên tổng {count} bản ghi”, số trang hiện tại / tổng số trang, nút Trước / Sau hỗ trợ responsive mobile. Cập nhật state trang `currentPage`, `pageSize = 15`, `totalCount`, `totalPages` cho 5 trang quản lý và tự động reset về trang 1 khi người dùng đổi bộ lọc hoặc từ khóa tìm kiếm.

**Tech Stack:** Django 5.2, Django REST Framework, React 18, Tailwind CSS, Lucide React, Pytest, Vitest, Playwright.

**Spec:** Yêu cầu từ người dùng ngày 2026-10-30:
1. Phân trang phía backend, mỗi trang tối đa 15 bản ghi.
2. Hiển thị “Đang xem 1–15 trên tổng X bản ghi”, số trang, nút Trước/Sau.
3. Đổi bộ lọc hoặc tìm kiếm thì tự quay về trang 1.
4. Tìm kiếm và lọc áp dụng trên toàn bộ dữ liệu, không chỉ trang đang xem.
5. Giữ đầy đủ lựa chọn trong dropdown của các biểu mẫu; bảo toàn phân quyền.
6. Dùng chung một component phân trang, đồng bộ giao diện và hỗ trợ mobile.
7. Sau khi thêm/sửa dữ liệu, cập nhật lại danh sách và tổng số bản ghi phù hợp.

## Global Constraints

- Mọi trang quản lý sử dụng cỡ trang chuẩn 15 bản ghi (`page_size=15`).
- Dropdown chọn danh mục, phòng ban, người dùng trong modal không bị giới hạn 15 bản ghi.
- Tất cả 171 test cases backend hiện có phải tiếp tục pass 100%.
- Toàn bộ test frontend Vitest và Playwright E2E phải pass.
- Giữ nguyên thiết kế Linear SaaS: viền nhẹ `border-slate-200/90`, màu chính blue-600, typography chuẩn, mobile responsive.

## Review Focus

1. Dropdown trong modal thêm/sửa tài sản và yêu cầu không bị mất dữ liệu khi có > 15 danh mục/phòng ban.
2. Khi người dùng đang ở trang 3 và nhập từ khóa tìm kiếm mới, bảng tự động nhảy về trang 1 và hiển thị đúng kết quả tìm kiếm toàn cục.
3. Bảng không có bản ghi nào (totalCount = 0): Phân trang ẩn nút chuyển hoặc hiển thị "Đang xem 0–0 trên tổng 0 bản ghi", không gây lỗi chia cho 0.
4. Nút "Trước" bị disable ở trang 1; nút "Sau" bị disable ở trang cuối cùng.
5. Tạo hoặc cập nhật bản ghi thành công: Refetch trang hiện tại và cập nhật lại `totalCount`, `totalPages`.

---

### Task 1: Backend StandardPagination & ViewSet Pagination Support

**Files:**
- Create: `backend/apps/common/pagination.py`
- Modify: `backend/apps/assets/views.py`
- Modify: `backend/apps/departments/views.py`
- Modify: `backend/apps/authentication/admin_api.py`
- Modify: `backend/apps/requests/views.py`
- Test: `backend/tests/test_pagination.py`

**Interfaces:**
- Consumes: DRF `PageNumberPagination`, Django ORM `Q`.
- Produces: `StandardPagination` class với cấu trúc JSON:
  ```json
  {
    "count": 42,
    "total_pages": 3,
    "current_page": 1,
    "page_size": 15,
    "next": "http://.../?page=2",
    "previous": null,
    "results": [...]
  }
  ```

- [ ] **Step 1: Write the failing test for backend pagination**

Tạo `backend/tests/test_pagination.py` kiểm tra:
1. `GET /api/assets/?page=1` trả về đúng 15 bản ghi với metadata `count`, `total_pages`, `current_page`.
2. `GET /api/categories/?page=1` trả về paginated data với metadata; `GET /api/categories/` không truyền `page` trả về mảng đầy đủ cho dropdown.
3. `GET /api/departments/?page=1` trả về paginated data; `GET /api/departments/` không truyền `page` trả về mảng đầy đủ.
4. `GET /api/users/?page=1` và `GET /api/requests/?page=1` trả về đúng 15 bản ghi kèm metadata.
5. Tìm kiếm `GET /api/categories/?search=...` lọc toàn bộ database trước khi phân trang.

- [ ] **Step 2: Run test to verify it fails**

Chạy: `docker compose exec -T backend pytest tests/test_pagination.py -v`
Expected: FAIL (chưa có `StandardPagination` và viewsets chưa hỗ trợ phân trang chuẩn).

- [ ] **Step 3: Implement `StandardPagination` and update ViewSets**

1. Tạo `backend/apps/common/pagination.py`:
   - `page_size = 15`
   - `page_size_query_param = 'page_size'`
   - `max_page_size = 100`
   - Phương thức `paginate_queryset`: Bỏ qua phân trang nếu `request.query_params.get('all') == 'true'` hoặc `request.query_params.get('pagination') == 'false'`, hoặc nếu view có cờ `optional_pagination = True` và request không truyền `page` / `page_size`.
   - Phương thức `get_paginated_response`: Trả về `count`, `total_pages`, `current_page`, `page_size`, `next`, `previous`, `results`.
2. Trong `backend/apps/assets/views.py`: Thay `AssetPagination` bằng `StandardPagination` (hoặc đặt `AssetPagination.page_size = 15` và trả về metadata mở rộng).
3. Trong `backend/apps/departments/views.py`:
   - Gắn `pagination_class = StandardPagination` và `optional_pagination = True` cho `CategoryViewSet` và `DepartmentViewSet`.
   - Bổ sung `search` filter cho `CategoryViewSet` và `DepartmentViewSet` để tìm kiếm toàn bộ dữ liệu.
4. Trong `backend/apps/authentication/admin_api.py`: Gắn `pagination_class = StandardPagination` và `optional_pagination = True` cho `UserAdminViewSet`.
5. Trong `backend/apps/requests/views.py`: Gắn `pagination_class = StandardPagination` cho `RequestListCreateView`. Bổ sung query `search` cho mã phiếu, lý do, người yêu cầu.

- [ ] **Step 4: Run test to verify it passes**

Chạy: `docker compose exec -T backend pytest tests/test_pagination.py -v`
Expected: PASS 100%.

- [ ] **Step 5: Verify existing 171 backend tests still pass**

Chạy: `docker compose exec -T backend pytest`
Expected: 171+ passed, 0 failed.

- [ ] **Step 6: Commit backend changes**

```bash
git add backend/apps/common/pagination.py backend/apps/assets/views.py backend/apps/departments/views.py backend/apps/authentication/admin_api.py backend/apps/requests/views.py backend/tests/test_pagination.py
git commit -m "feat(backend): add StandardPagination and search filters for management endpoints"
```

---

### Task 2: Frontend Reusable Pagination Component

**Files:**
- Create: `frontend/src/components/Pagination.jsx`
- Create: `frontend/src/tests/Pagination.test.jsx`

**Interfaces:**
- Consumes: Props `{ currentPage, totalPages, totalCount, pageSize, onPageChange, loading }`.
- Produces: Render chuẩn Accessible UI với định dạng “Đang xem {start}–{end} trên tổng {totalCount} bản ghi”, nút Trước/Sau và các nút chuyển số trang.

- [ ] **Step 1: Write the failing test for `Pagination.jsx`**

Tạo `frontend/src/tests/Pagination.test.jsx`:
1. Hiển thị "Đang xem 1–15 trên tổng 40 bản ghi".
2. Trang 1: Nút "Trước" bị disabled, nút "Sau" enabled. Click "Sau" gọi `onPageChange(2)`.
3. Trang cuối (trang 3/3): Nút "Sau" bị disabled.
4. Khi `totalCount = 0`: Hiển thị "Đang xem 0–0 trên tổng 0 bản ghi", không hiển thị trang âm.
5. Render nút số trang và click trang 2 gọi `onPageChange(2)`.

- [ ] **Step 2: Run test to verify it fails**

Chạy: `npm --prefix frontend test -- frontend/src/tests/Pagination.test.jsx --run`
Expected: FAIL (chưa có component `Pagination.jsx`).

- [ ] **Step 3: Implement `frontend/src/components/Pagination.jsx`**

- Tính toán `start = totalCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;`
- Tính toán `end = Math.min(currentPage * pageSize, totalCount);`
- Hỗ trợ responsive: Text tóm tắt bên trái, cụm nút bấm bên phải; trên mobile tự co thành flex hàng gọn gàng hoặc xếp gọn.
- Style Linear SaaS: nút viền `border-slate-200/90`, chữ `text-slate-700`, active page `bg-blue-50 text-blue-700 border-blue-200/90 font-semibold`, disabled `opacity-40 cursor-not-allowed`.

- [ ] **Step 4: Run test to verify it passes**

Chạy: `npm --prefix frontend test -- frontend/src/tests/Pagination.test.jsx --run`
Expected: PASS 100%.

- [ ] **Step 5: Commit `Pagination` component**

```bash
git add frontend/src/components/Pagination.jsx frontend/src/tests/Pagination.test.jsx
git commit -m "feat(ui): add reusable Pagination component with responsive mobile support"
```

---

### Task 3: Apply Pagination to CategoryPage

**Files:**
- Modify: `frontend/src/pages/CategoryPage.jsx`
- Modify: `frontend/src/tests/AdminPages.test.jsx`

**Interfaces:**
- Consumes: `<Pagination />`, `/api/categories/?page=...&page_size=15&search=...`.
- Produces: Quản lý danh mục có phân trang backend 15 mục/trang, tự động về trang 1 khi gõ tìm kiếm, cập nhật lại dữ liệu sau khi thêm/sửa danh mục.

- [ ] **Step 1: Write test for CategoryPage pagination in `AdminPages.test.jsx`**

1. Cập nhật MSW handler giả lập trả về `{ count: 25, total_pages: 2, current_page: 1, results: mockCategories.slice(0, 15) }` khi có `page=1`.
2. Assert trang 1 hiển thị "Đang xem 1–15 trên tổng 25 bản ghi".
3. Click "Sau" chuyển sang gọi `page=2` và hiển thị "Đang xem 16–25 trên tổng 25 bản ghi".
4. Khi nhập từ khóa tìm kiếm, kiểm tra request gửi tham số `page=1`.

- [ ] **Step 2: Run test to verify it fails**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AdminPages.test.jsx --run`
Expected: FAIL do CategoryPage chưa có phân trang.

- [ ] **Step 3: Implement pagination in `CategoryPage.jsx`**

- Khai báo state: `currentPage = 1`, `pageSize = 15`, `totalCount = 0`, `totalPages = 1`.
- Gửi query params: `{ page: currentPage, page_size: pageSize, search: searchTerm.trim() || undefined }`.
- Khi `searchTerm` thay đổi: `setCurrentPage(1)`.
- Nhận kết quả: Cập nhật `categories`, `totalCount`, `totalPages`.
- Sau khi thêm hoặc sửa thành công: gọi `fetchCategories()`.
- Chèn `<Pagination />` vào dưới bảng.

- [ ] **Step 4: Run test to verify it passes**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AdminPages.test.jsx --run`
Expected: PASS 100%.

- [ ] **Step 5: Commit CategoryPage changes**

```bash
git add frontend/src/pages/CategoryPage.jsx frontend/src/tests/AdminPages.test.jsx
git commit -m "feat(categories): integrate backend pagination and search reset"
```

---

### Task 4: Apply Pagination to DepartmentPage and UserAdminPage

**Files:**
- Modify: `frontend/src/pages/DepartmentPage.jsx`
- Modify: `frontend/src/pages/UserAdminPage.jsx`
- Modify: `frontend/src/tests/AdminPages.test.jsx`

**Interfaces:**
- Consumes: `<Pagination />`, `/api/departments/`, `/api/users/`.
- Produces: Trang phòng ban và người dùng có phân trang 15 bản ghi/trang; bộ lọc vai trò, trạng thái, phòng ban tự động quay về trang 1; dropdown chọn phòng ban trong modal người dùng vẫn nạp đủ danh sách không bị phân trang ngắt cụt.

- [ ] **Step 1: Write tests for Department & UserAdmin pagination**

Bổ sung test cases trong `AdminPages.test.jsx`:
1. `UserAdminPage`: Đổi filter vai trò `role` hoặc `is_active` gọi API với `page=1`.
2. Kiểm tra `<Pagination />` xuất hiện và click trang 2 gọi API với `page=2`.
3. Kiểm tra dropdown phòng ban trong modal tạo/sửa người dùng nhận đủ danh sách phòng ban.

- [ ] **Step 2: Run test to verify it fails**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AdminPages.test.jsx --run`
Expected: FAIL.

- [ ] **Step 3: Implement pagination in `DepartmentPage.jsx` and `UserAdminPage.jsx`**

1. `DepartmentPage.jsx`:
   - Thêm state `currentPage`, `pageSize = 15`, `totalCount`, `totalPages`.
   - Gọi `/api/departments/` với `page` và `page_size`.
   - Render `<Pagination />` dưới bảng. Sau khi thêm/sửa, gọi `fetchDepartments()`.
2. `UserAdminPage.jsx`:
   - Thêm state `currentPage`, `pageSize = 15`, `totalCount`, `totalPages`.
   - Gọi `/api/users/` với `page`, `page_size`, `role`, `is_active`, `department_id`, `search`.
   - Khi bất kỳ filter nào thay đổi (role, is_active, department, search): `setCurrentPage(1)`.
   - Khi load dropdown phòng ban cho form: gọi `/api/departments/?all=true` để nhận đủ tất cả phòng ban.
   - Render `<Pagination />` dưới bảng. Sau khi kích hoạt/phê duyệt/cập nhật role, refetch trang hiện tại.

- [ ] **Step 4: Run test to verify it passes**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AdminPages.test.jsx --run`
Expected: PASS 100%.

- [ ] **Step 5: Commit Department & UserAdmin changes**

```bash
git add frontend/src/pages/DepartmentPage.jsx frontend/src/pages/UserAdminPage.jsx frontend/src/tests/AdminPages.test.jsx
git commit -m "feat(users,departments): integrate pagination and full-list dropdown fetching"
```

---

### Task 5: Apply Pagination to AssetListPage and RequestInboxPage

**Files:**
- Modify: `frontend/src/pages/AssetListPage.jsx`
- Modify: `frontend/src/pages/RequestInboxPage.jsx`
- Modify: `frontend/src/tests/AssetFlows.test.jsx`
- Modify: `frontend/src/tests/RequestFlows.test.jsx`

**Interfaces:**
- Consumes: `<Pagination />`, `/api/assets/`, `/api/requests/`.
- Produces: Trang tài sản và yêu cầu có phân trang 15 bản ghi/trang; filter trạng thái, danh mục, phòng ban tự động về trang 1; dropdown chọn danh mục/tài sản trong modal nạp đủ bản ghi (`all=true`).

- [ ] **Step 1: Write test for Asset & Request pagination in flows tests**

1. `AssetFlows.test.jsx`: Test đổi categoryFilter gọi API với `page=1`; test click nút chuyển trang gọi `page=2`.
2. `RequestFlows.test.jsx`: Test đổi statusFilter gọi API với `page=1`; test render phân trang chuẩn.

- [ ] **Step 2: Run test to verify it fails**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AssetFlows.test.jsx frontend/src/tests/RequestFlows.test.jsx --run`
Expected: FAIL.

- [ ] **Step 3: Implement pagination in `AssetListPage.jsx` and `RequestInboxPage.jsx`**

1. `AssetListPage.jsx`:
   - Thêm state `currentPage`, `pageSize = 15`, `totalCount`, `totalPages`.
   - Khi filter (`searchTerm`, `statusFilter`, `categoryFilter`, `departmentFilter`) thay đổi: `setCurrentPage(1)`.
   - Nạp dropdown danh mục và phòng ban bằng `axiosClient.get('/api/categories/?all=true')` và `axiosClient.get('/api/departments/?all=true')`.
   - Render `<Pagination />` ở chân bảng.
2. `RequestInboxPage.jsx`:
   - Thêm state `currentPage`, `pageSize = 15`, `totalCount`, `totalPages`.
   - Khi filter (`statusFilter`, `typeFilter`) thay đổi: `setCurrentPage(1)`.
   - Nạp dropdown trong modal tạo yêu cầu với `all=true`.
   - Render `<Pagination />` ở chân bảng. Sau khi xét duyệt hoặc tạo yêu cầu, refetch trang hiện tại.

- [ ] **Step 4: Run test to verify it passes**

Chạy: `npm --prefix frontend test -- frontend/src/tests/AssetFlows.test.jsx frontend/src/tests/RequestFlows.test.jsx --run`
Expected: PASS 100%.

- [ ] **Step 5: Commit Asset & Request changes**

```bash
git add frontend/src/pages/AssetListPage.jsx frontend/src/pages/RequestInboxPage.jsx frontend/src/tests/AssetFlows.test.jsx frontend/src/tests/RequestFlows.test.jsx
git commit -m "feat(assets,requests): integrate pagination and full dropdown options"
```

---

### Task 6: Comprehensive Verification & Whole-Suite Test

**Files:**
- Create: `frontend/e2e/pagination.spec.js`
- Modify: `docs/verification-report.md`

- [ ] **Step 1: Write E2E test for pagination in Playwright**

Tạo `frontend/e2e/pagination.spec.js`:
1. Đăng nhập admin, vào `/categories`: Kiểm tra thanh phân trang "Đang xem ... trên tổng ... bản ghi".
2. Vào `/assets`: Kiểm tra phân trang và nút Trước/Sau. Đổi filter trạng thái và assert trang quay về 1.
3. Vào `/requests`: Kiểm tra phân trang hiển thị đúng.
4. Mở modal tạo tài sản / yêu cầu: Kiểm tra dropdown danh mục và phòng ban có đầy đủ các option.

- [ ] **Step 2: Run Playwright E2E**

Chạy: `npx playwright test e2e/pagination.spec.js`
Expected: PASS 100%.

- [ ] **Step 3: Run entire test suites**

1. Backend: `docker compose exec -T backend pytest` (171+ passed).
2. Frontend Vitest: `npm --prefix frontend test -- --run` (all passed).
3. Frontend Build: `npm --prefix frontend run build` (exit 0).
4. All E2E: `npx playwright test` (all passed).

- [ ] **Step 4: Update verification report & commit**

```bash
git add docs/verification-report.md frontend/e2e/pagination.spec.js
git commit -m "docs: record verification report for pagination across all management pages"
```
