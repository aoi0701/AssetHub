# Hướng Dẫn Triển Khai Hệ Thống AssetHub (Deployment Guide)

Tài liệu hướng dẫn chi tiết quy trình chuẩn bị cấu hình, đối chiếu tài nguyên thực tế và các bước triển khai hệ thống **AssetHub** theo mô hình Hybrid Edge-Core: Backend Origin phục vụ tại cổng độc lập 8001 qua Cloudflare Tunnel kết hợp Cloudflare D1, Workers AI, R2 và Pages.

---

## 1. Bảng Đối Chiếu Tài Nguyên Thực Tế & Trạng Thái Cloudflare

Kết quả khảo sát tài nguyên thực tế qua Cloudflare OAuth (Account: `hnhoa494@gmail.com`, Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`):

| Thành phần | Trạng thái Cloud | Tên / Định danh thực tế | URL / Endpoint thực tế | Config sử dụng | Bí mật cần điền (Local) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cloudflare Account** | PRESENT | `Hnhoa494@gmail.com's Account`<br>`5e76c2bf4e09587a010d31ea416ec8d3` | — | `deploy/.env.demo.example` | Token OAuth Wrangler có sẵn |
| **Worker Subdomain** | PRESENT | `hnhoa494-assethub` | `*.hnhoa494-assethub.workers.dev` | `wrangler.toml` (tự động) | — |
| **D1 Database** | PRESENT | `assethub-test-d1`<br>UUID: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1` | — | `cloudflare-worker/bridge/wrangler.toml`<br>`cloudflare-worker/wrangler.toml` | — *(Dữ liệu hiện có: 3 users, 5 depts, 2 assets — PHẢI GIỮ)* |
| **D1 Bridge Worker** | PRESENT | `assethub-test-worker` | `https://assethub-test-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/bridge/wrangler.toml` | `D1_BRIDGE_SECRET_TOKEN` (khớp `BRIDGE_SECRET_TOKEN` trên Worker) |
| **AI Worker** | MISSING | `assethub-ai-worker` *(dự kiến)* | `https://assethub-ai-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/wrangler.toml` | `AI_SERVICE_TOKEN` (tối thiểu 32 ký tự, nạp lúc deploy) |
| **R2 Storage** | BLOCKED | `assethub-attachments` *(dự kiến)* | `https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com` | `deploy/.env.demo.example` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`<br>*(Cần kích hoạt R2 trên Cloudflare Dashboard)* |
| **Demo Backend Origin**| READY (LOCAL)| Container `assethub-demo-backend`<br>(Cổng 8001, mount `ro`, pull `never`)| `http://127.0.0.1:8001` | `deploy/docker-compose.demo.yml`<br>`deploy/.env.demo` | `DJANGO_SECRET_KEY` (sinh ngẫu nhiên 64 ký tự) |
| **Cloudflare Tunnel** | PENDING | Quick Tunnel qua binary host `/usr/local/bin/cloudflared` | Lấy lúc mở Tunnel: `https://<subdomain>.trycloudflare.com` | `deploy/.env.demo` | — *(Không mở tunnel trước khi deploy)* |
| **Frontend Pages** | MISSING | `assethub` *(dự kiến)* | Lấy sau deploy: `https://<pages-project>.pages.dev` | `frontend/dist` | — |

---

## 2. Thứ Tự Triển Khai Theo Phụ Thuộc (Dependency Sequence)

> [!IMPORTANT]
> **Ranh giới an toàn:** Tất cả các lệnh deploy cloud dưới đây CHỈ được thực thi sau khi Codex kiểm duyệt và người dùng chấp thuận. Hiện tại hệ thống đang ở trạng thái chuẩn bị cấu hình cục bộ (Local Staging).

### Giai đoạn 1: Chuẩn bị Cấu Hình & Môi Trường Local (Hoàn toàn Read/Local - ĐÃ SẴN SÀNG)
1. **Kiểm tra công cụ dòng lệnh đã có sẵn**:
   ```bash
   # Dùng binary Wrangler sẵn có trong hệ thống (KHÔNG chạy npm install hoặc npx tự tải):
   WRANGLER="node /home/truong/.npm/_npx/32026684e21afda6/node_modules/wrangler/bin/wrangler.js"
   $WRANGLER whoami
   /usr/local/bin/cloudflared --version
   ```
2. **Chuẩn bị file cấu hình `deploy/.env.demo` từ template**:
   ```bash
   cp deploy/.env.demo.example deploy/.env.demo
   # Sinh DJANGO_SECRET_KEY ngẫu nhiên an toàn:
   python3 -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50))"
   ```

### Giai đoạn 2: Sao Lưu & Chuẩn Bị Cloud Resources (Mutation / Cloud)
3. **Sao lưu CSDL D1 hiện tại trước khi thực hiện bất kỳ thao tác nào**:
   ```bash
   $WRANGLER d1 export assethub-test-d1 --remote --output=./backups/d1_backup_$(date +%Y%m%d_%H%M%S).sql
   ```
4. **Đối chiếu Schema D1**:
   - Schema D1 `assethub-test-d1` đã có sẵn 19 bảng và triggers bảo vệ. Tuyệt đối **KHÔNG DROP**, không reset và không chạy `migrate_pg_to_d1` đè dữ liệu.
5. **Kích hoạt R2 (Nếu người dùng muốn kiểm tra tính năng đính kèm file)**:
   - Người dùng truy cập Cloudflare Dashboard kích hoạt R2 (nhập thông tin thanh toán nếu tài khoản yêu cầu).
   - Tạo bucket `assethub-attachments` và API Token có quyền Object Read & Write.
   - Điền `R2_ACCESS_KEY_ID` và `R2_SECRET_ACCESS_KEY` vào `deploy/.env.demo`.
   *(Nếu chưa kích hoạt R2, hệ thống vẫn hoạt động bình thường; test R2 sẽ skip sạch sẽ).*
6. **Deploy Workers lên Cloudflare**:
   ```bash
   # 6a. Cập nhật Bridge Worker (nếu cần deploy phiên bản mới):
   cd cloudflare-worker/bridge
   $WRANGLER secret put BRIDGE_SECRET_TOKEN   # Nhập token >= 32 ký tự
   $WRANGLER deploy

   # 6b. Deploy AI Worker (gợi ý danh mục @cf/meta/llama-3.1-8b-instruct-fp8):
   cd ..
   $WRANGLER secret put AI_SERVICE_TOKEN       # Nhập token >= 32 ký tự
   $WRANGLER deploy
   ```

### Giai đoạn 3: Khởi Động Backend Origin & Mở Ingress Tunnel
7. **Khởi động Demo Backend Container (Cổng 8001)**:
   ```bash
   docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d
   curl -f http://127.0.0.1:8001/api/health/
   ```
   *Lưu ý:* Backend sử dụng `pull_policy: never` và gắn `backend:/app:ro`, hoàn toàn độc lập với stack cổng 8000.
8. **Mở Quick Tunnel qua binary host**:
   ```bash
   /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8001
   ```
   - Ghi lại URL công khai in ra trên terminal, ví dụ: `https://asset-demo-xyz.trycloudflare.com`.
   - Cập nhật URL này vào `DJANGO_ALLOWED_HOSTS` trong `deploy/.env.demo` và khởi động lại container demo:
     ```bash
     docker compose -p assethub-demo -f deploy/docker-compose.demo.yml restart backend-demo
     ```

### Giai đoạn 4: Build & Deploy Frontend SPA lên Cloudflare Pages
9. **Build Frontend SPA với URL Backend Tunnel**:
   ```bash
   export VITE_API_BASE_URL="https://asset-demo-xyz.trycloudflare.com"
   npm --prefix frontend run build
   ```
   Xác minh thư mục `frontend/dist` đã chứa `_headers` và `_redirects`.
10. **Deploy lên Cloudflare Pages**:
    ```bash
    $WRANGLER pages deploy frontend/dist --project-name=assethub
    ```
    - Ghi lại URL Pages được cấp (ví dụ: `https://assethub.pages.dev`).
    - Cập nhật `CORS_ALLOWED_ORIGINS=https://assethub.pages.dev` trong `deploy/.env.demo` và reload backend demo.

### Giai đoạn 5: Kiểm Thử Toàn Tuyến (End-to-End Cloud Smoke)
11. **Thực thi Cloud Smoke Test**:
    ```bash
    RUN_CLOUD_SMOKE=1 \
    CLOUD_API_BASE_URL="https://asset-demo-xyz.trycloudflare.com" \
    D1_BRIDGE_URL="https://assethub-test-worker.hnhoa494-assethub.workers.dev" \
    D1_BRIDGE_TOKEN="<token>" \
    USE_D1_DATABASE=True \
    backend/.venv/bin/pytest backend/tests/e2e/test_d1_cloud_smoke.py -m cloud_smoke -v -s
    ```
    - Xác nhận toàn bộ 5 bước HTTP cốt lõi (Health, Auth JWT, Asset CRUD, Last Admin Trigger, Teardown Ownership) PASSED 100%.
    - R2 và Workers AI có log kiểm chứng riêng biệt.

---

## 3. Đề Xuất Tập Dữ Liệu Demo Tối Thiểu (Minimal Demo Dataset)

Để bảo đảm tính an toàn dữ liệu và tránh làm ô nhiễm CSDL D1 hiện tại:
- **Nguyên tắc:** KHÔNG tự động chạy seed script diện rộng. Sử dụng tập dữ liệu tối thiểu phục vụ kiểm thử nghiệp vụ.
- **Tập dữ liệu đề xuất**:
  1. **1 Department**: `Phòng Công nghệ thông tin` (Mã: `IT`, Mô tả: `Bộ phận IT hỗ trợ kỹ thuật`).
  2. **1 Category**: `Laptop & Máy trạm` (Tên: `Laptop & Máy trạm`, Mô tả: `Thiết bị máy tính xách tay`).
  3. **2 Users**:
     - `demo_admin` (Role: `ADMIN`, Email: `admin.demo@assethub.local`): Quản trị viên duyệt cấp phát và quản lý tài sản.
     - `demo_user` (Role: `USER`, Email: `user.demo@assethub.local`): Nhân viên tạo phiếu yêu cầu cấp phát.
  4. **2 Assets**:
     - `AST-2026-0001`: MacBook Pro 16 M3 Max, Trạng thái: `AVAILABLE` (Sẵn sàng cấp phát).
     - `AST-2026-0002`: Dell Precision 5570, Trạng thái: `IN_USE` (Đang sử dụng).
  5. **1 Request**:
     - `REQ-2026-0001`: Phiếu yêu cầu cấp phát laptop cho `demo_user`, trạng thái `PENDING` (Chờ `demo_admin` phê duyệt).

### Hướng Dẫn Băm Mật Khẩu Chuẩn Django (PBKDF2 1.000.000 Vòng):
Tuyệt đối không chèn mật khẩu thô hay hạ thuật toán hasher để chạy test:
```python
# Chạy an toàn qua Django manage.py shell nội bộ:
from apps.authentication.models import User
from django.contrib.auth.hashers import make_password

# Băm an toàn chuẩn PBKDF2 SHA-256 1.000.000 rounds:
hashed_pw = make_password("DemoPassword#2026!")
# Sử dụng hashed_pw này để khởi tạo tài khoản quản trị viên demo nếu D1 thiếu Admin nền.
```

---

## 4. Quy Trình Hoàn Tác Sự Cố (Rollback Sequence)

Nếu xảy ra sự cố trong quá trình triển khai hoặc kiểm thử Cloud:
1. **Dừng Ingress & Cô Lập Ngay Lập Tức**:
   - Nhấn `Ctrl + C` hoặc kill tiến trình `cloudflared` Quick Tunnel -> Mọi truy cập từ Internet vào backend lập tức bị ngắt.
   - Dừng container demo backend:
     ```bash
     docker compose -p assethub-demo -f deploy/docker-compose.demo.yml down
     ```
2. **Khôi Phục Trạng Thái Frontend Pages**:
   - Nếu Pages bị lỗi, dùng Wrangler rollback về deployment trước đó:
     ```bash
     $WRANGLER pages deployment list --project-name=assethub
     $WRANGLER pages rollback <deployment-id> --project-name=assethub
     ```
3. **Bảo Toàn CSDL Gốc**:
   - Container PostgreSQL production (`assethub-db-1` cổng 5433) và Backend chính (`assethub-backend-1` cổng 8000) không hề bị chỉnh sửa hay restart trong toàn bộ quá trình demo, sẵn sàng phục vụ tiếp tục với RPO = 0s.
   - Nếu D1 cần khôi phục về trạng thái trước demo:
     ```bash
     $WRANGLER d1 execute assethub-test-d1 --remote --file=./backups/d1_backup_YYYYMMDD_HHMMSS.sql
     ```
