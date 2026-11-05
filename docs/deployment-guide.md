# Hướng Dẫn Triển Khai Hệ Thống AssetHub (Deployment Guide)

Tài liệu hướng dẫn chi tiết quy trình chuẩn bị cấu hình, đối chiếu tài nguyên thực tế và các bước triển khai hệ thống **AssetHub** theo mô hình Hybrid Edge-Core: Backend Origin phục vụ tại cổng độc lập 8001 qua Cloudflare Tunnel kết hợp Cloudflare D1, Workers AI, R2 và Pages.

Mọi lệnh trong tài liệu này mặc định thực thi từ thư mục gốc repository: `/home/truong/Downloads/AssetHub`.

---

## 1. Bảng Đối Chiếu Tài Nguyên Thực Tế & Trạng Thái Cloudflare

Kết quả khảo sát tài nguyên thực tế qua Cloudflare OAuth (Account: `hnhoa494@gmail.com`, Account ID: `5e76c2bf4e09587a010d31ea416ec8d3`):

| Thành phần | Trạng thái Cloud | Tên / Định danh thực tế | URL / Endpoint thực tế | Config sử dụng | Bí mật cần điền (Local) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cloudflare Account** | PRESENT | `Hnhoa494@gmail.com's Account`<br>`5e76c2bf4e09587a010d31ea416ec8d3` | — | `deploy/.env.demo.example` | Token OAuth Wrangler có sẵn |
| **Worker Subdomain** | PRESENT | `hnhoa494-assethub` | `*.hnhoa494-assethub.workers.dev` | `wrangler.toml` (tự động) | — |
| **D1 Database** | PRESENT | `assethub-test-d1`<br>UUID: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1` | — | `cloudflare-worker/bridge/wrangler.toml`<br>`cloudflare-worker/wrangler.toml` | — *(Dữ liệu hiện có: 3 users, 5 departments, 2 assets — chứng minh data đã tồn tại và PHẢI BẢO TỒN; không tự ý xóa/reset)* |
| **D1 Bridge Worker** | PRESENT | `assethub-test-worker` | `https://assethub-test-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/bridge/wrangler.toml` | `D1_BRIDGE_SECRET_TOKEN` *(Khớp `BRIDGE_SECRET_TOKEN` đã deploy trên Worker; bảo toàn bí mật, không rotate không cần thiết)* |
| **AI Worker** | MISSING | `assethub-ai-worker` *(dự kiến)* | `https://assethub-ai-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/wrangler.toml` | `AI_SERVICE_TOKEN` (tối thiểu 32 ký tự, nạp lúc deploy nếu kích hoạt) |
| **R2 Storage** | DISABLED | `assethub-attachments` *(dự kiến)* | `https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com` | `deploy/.env.demo.example` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`<br>*(Cloudflare Error 10042: R2 chưa kích hoạt trên account, cần kích hoạt thủ công trên Dashboard; khi chưa bật, để trống credentials và smoke test tự skip)* |
| **Demo Backend Origin**| READY (LOCAL)| Service `backend`<br>(Container cổng 8001, mount `ro`, pull `never`)| `http://127.0.0.1:8001` | `deploy/docker-compose.demo.yml`<br>`deploy/.env.demo` | `DJANGO_SECRET_KEY` (sinh ngẫu nhiên 64 ký tự, ghi trực tiếp vào file không in ra terminal) |
| **Cloudflare Tunnel** | PENDING | Quick Tunnel qua binary host `/usr/local/bin/cloudflared` | Lấy lúc mở Tunnel: `https://<subdomain>.trycloudflare.com` | `deploy/.env.demo` | — *(Không mở tunnel trước khi deploy)* |
| **Frontend Pages** | MISSING | `assethub` *(chưa tạo trên Cloudflare)* | Lấy sau deploy: `https://assethub.pages.dev` | `frontend/dist` | Khởi tạo với production branch `main` |

---

## 2. Thứ Tự Triển Khai Theo Phụ Thuộc (Dependency Sequence)

> [!IMPORTANT]
> **Ranh giới an toàn:** Tất cả các lệnh deploy cloud dưới đây CHỈ được thực thi sau khi Codex kiểm duyệt và người dùng chấp thuận. Hiện tại hệ thống đang ở trạng thái chuẩn bị cấu hình cục bộ (Local Staging).
> **Quy tắc thư mục làm việc (CWD):** Mọi lệnh chạy từ thư mục gốc `/home/truong/Downloads/AssetHub`. Đối với các tác vụ trong thư mục con, sử dụng subshell `(cd ... && ...)` để không làm thay đổi thư mục làm việc của terminal hiện tại.

### Giai đoạn 1: Chuẩn bị Cấu Hình & Môi Trường Local (Hoàn toàn Read/Local - ĐÃ SẴN SÀNG)

1. **Kiểm tra công cụ dòng lệnh đã có sẵn**:
   ```bash
   cd /home/truong/Downloads/AssetHub

   # Dùng binary Wrangler sẵn có trong hệ thống (KHÔNG chạy npm install hoặc npx tự tải):
   WRANGLER="node /home/truong/.npm/_npx/32026684e21afda6/node_modules/wrangler/bin/wrangler.js"
   $WRANGLER whoami
   /usr/local/bin/cloudflared --version
   ```

2. **Chuẩn bị file cấu hình `deploy/.env.demo` từ template**:
   ```bash
   cd /home/truong/Downloads/AssetHub

   # Copy template mà không ghi đè nếu file đã tồn tại:
   cp -n deploy/.env.demo.example deploy/.env.demo

   # Xác minh file tồn tại:
   test -f deploy/.env.demo && echo "deploy/.env.demo is present"

   # Tạo DJANGO_SECRET_KEY an toàn và ghi trực tiếp vào file (KHÔNG in bí mật ra màn hình terminal hay shell history):
   python3 -c '
import os, re, secrets, sys

env_path = "deploy/.env.demo"
if not os.path.exists(env_path):
    sys.exit(f"File not found: {env_path}")

with open(env_path, "r", encoding="utf-8") as f:
    content = f.read()

match = re.search(r"^DJANGO_SECRET_KEY=(.*?)(\r?)$", content, flags=re.MULTILINE)
placeholders = {
    "",
    "change-this-to-a-secure-random-64-character-string-for-demo!",
    "insecure-default-secret-key-for-dev-only",
}

def should_replace(val):
    v = val.strip().strip("\"'\''")
    return v in placeholders or v.startswith("placeholder")

if match:
    raw_val = match.group(1)
    if should_replace(raw_val):
        new_key = secrets.token_urlsafe(50)
        eol = match.group(2)
        content = content[:match.start()] + f"DJANGO_SECRET_KEY={new_key}{eol}" + content[match.end():]
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)
else:
    new_key = secrets.token_urlsafe(50)
    sep = "\n" if content and not content.endswith("\n") else ""
    content = content + f"{sep}DJANGO_SECRET_KEY={new_key}\n"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
'
   ```
   *Lưu ý bảo toàn bí mật:* Điền `D1_BRIDGE_SECRET_TOKEN` trong `deploy/.env.demo` bằng token đã được deploy trên Worker hiện tại; không tự ý rotate bí mật trừ khi có yêu cầu bảo mật cụ thể.

### Giai đoạn 2: Sao Lưu & Chuẩn Bị Cloud Resources (Mutation / Cloud)

3. **Ghi nhận mốc Time Travel và Sao lưu Snapshot CSDL D1**:
   ```bash
   cd /home/truong/Downloads/AssetHub

   # 3a. Ghi nhận mốc D1 Time Travel trước khi thực hiện bất kỳ thao tác mutation nào:
   $WRANGLER d1 time-travel info assethub-test-d1 --timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

   # 3b. Xuất bản snapshot SQL ngoại tuyến làm kho dự phòng thảm họa (backups/ đã được gitignore):
   mkdir -p backups
   $WRANGLER d1 export assethub-test-d1 --remote --output=./backups/d1_backup_$(date +%Y%m%d_%H%M%S).sql
   ```

4. **Đối chiếu Schema & Bảo Vệ Dữ Liệu D1 Hiện Hữu**:
   - Schema D1 `assethub-test-d1` đã có sẵn 19 bảng, triggers bảo vệ và dữ liệu đã kiểm chứng (COUNT: 3 users, 5 departments, 2 assets).
   - Tuyệt đối **KHÔNG DROP**, không reset và không chạy `migrate_pg_to_d1` đè dữ liệu.
   - Bản SQL export chỉ đóng vai trò snapshot an toàn offline, **tuyệt đối không** nạp đè SQL dump trực tiếp vào CSDL đang có bảng/dữ liệu bằng lệnh `d1 execute`.

5. **Trạng thái Cloudflare R2 (Error 10042)**:
   - R2 hiện chưa được bật trên tài khoản Cloudflare (lỗi API 10042: R2 is not enabled on this account).
   - Nếu cần kiểm thử upload đính kèm file: Người dùng cần truy cập Cloudflare Dashboard để kích hoạt R2 thủ công (kèm phương thức thanh toán nếu tài khoản yêu cầu), tạo bucket `assethub-attachments` và API Token tương ứng, sau đó điền `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` vào `deploy/.env.demo`.
   - Nếu chưa kích hoạt R2: Để trống các giá trị R2 trong `deploy/.env.demo`. Hệ thống backend và test suite sẽ tự động bỏ qua (skip gracefully) các bài test upload tệp mà không làm gián đoạn hệ thống.

6. **Deploy Workers lên Cloudflare (nếu cần cập nhật)**:
   - Sử dụng subshell để không rời khỏi thư mục repo root `/home/truong/Downloads/AssetHub`:
   ```bash
   # 6a. Deploy Bridge Worker (bảo toàn BRIDGE_SECRET_TOKEN hiện hữu):
   (cd cloudflare-worker/bridge && $WRANGLER deploy)

   # 6b. Deploy AI Worker (chỉ thực hiện nếu triển khai tính năng AI):
   (cd cloudflare-worker && $WRANGLER secret put AI_SERVICE_TOKEN && $WRANGLER deploy)
   ```

### Giai đoạn 3: Khởi Động Backend Origin & Mở Ingress Tunnel

7. **Khởi động Demo Backend Container (Cổng 8001)**:
   ```bash
   cd /home/truong/Downloads/AssetHub

   # Khởi động service backend trong stack demo (pull never, không build lại image):
   docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend

   # Kiểm tra endpoint healthcheck cục bộ:
   curl -f http://127.0.0.1:8001/api/health/
   ```
   *Lưu ý:* Service name trong compose file là `backend` (thuộc project `assethub-demo`, không phải `backend-demo`). Image sử dụng `assethub-backend:latest` với volume `backend:/app:ro`, hoàn toàn độc lập với stack dev cổng 8000.

8. **Mở Quick Tunnel qua binary host**:
   ```bash
   cd /home/truong/Downloads/AssetHub
   /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8001
   ```
   - Ghi lại URL công khai in ra trên terminal, ví dụ: `https://asset-demo-xyz.trycloudflare.com`.
   - Cập nhật URL này vào `DJANGO_ALLOWED_HOSTS` trong `deploy/.env.demo`.
   - **Tái tạo container backend** để nạp cấu hình môi trường mới (KHÔNG dùng `docker compose restart` vì lệnh restart không đọc lại file env):
     ```bash
     cd /home/truong/Downloads/AssetHub
     docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend
     ```

### Giai đoạn 4: Build & Deploy Frontend SPA lên Cloudflare Pages

9. **Build Frontend SPA với URL Backend Tunnel**:
   ```bash
   cd /home/truong/Downloads/AssetHub
   export VITE_API_BASE_URL="https://asset-demo-xyz.trycloudflare.com"
   npm --prefix frontend run build
   ```
   Xác minh thư mục `frontend/dist` đã chứa `_headers` và `_redirects`.

10. **Tạo Project và Deploy lên Cloudflare Pages**:
    ```bash
    cd /home/truong/Downloads/AssetHub

    # 10a. Do Pages project assethub chưa tồn tại trên tài khoản Cloudflare, tạo project với production branch main:
    $WRANGLER pages project create assethub --production-branch main

    # 10b. Triển khai thư mục frontend/dist lên Pages project assethub nhánh main:
    $WRANGLER pages deploy frontend/dist --project-name assethub --branch main
    ```
    - Ghi lại URL thực tế từ output deployment (ví dụ: `https://assethub.pages.dev`).
    - Cập nhật `CORS_ALLOWED_ORIGINS=https://assethub.pages.dev` trong `deploy/.env.demo`.
    - Tái tạo container backend để áp dụng cấu hình CORS mới:
      ```bash
      cd /home/truong/Downloads/AssetHub
      docker compose -p assethub-demo -f deploy/docker-compose.demo.yml --env-file deploy/.env.demo up -d --no-build --pull never backend
      ```

### Giai đoạn 5: Kiểm Thử Toàn Tuyến (End-to-End Cloud Smoke)

11. **Thực thi Cloud Smoke Test**:
    ```bash
    cd /home/truong/Downloads/AssetHub
    CLOUD_API_BASE_URL="https://asset-demo-xyz.trycloudflare.com" \
    backend/.venv/bin/python -c '
import os, sys, subprocess, dotenv

env_vals = dotenv.dotenv_values("deploy/.env.demo")
bridge_url = (env_vals.get("D1_BRIDGE_URL") or os.environ.get("D1_BRIDGE_URL") or "").strip()
token = (env_vals.get("D1_BRIDGE_SECRET_TOKEN") or os.environ.get("D1_BRIDGE_SECRET_TOKEN") or "").strip()
api_url = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.environ.get("CLOUD_API_BASE_URL") or env_vals.get("CLOUD_API_BASE_URL") or "").strip()

if not bridge_url or not bridge_url.startswith("https://"):
    sys.exit("Error: D1_BRIDGE_URL must be set and start with https://")

if not token or len(token) < 32 or token.startswith("placeholder"):
    sys.exit("Error: D1_BRIDGE_SECRET_TOKEN must be set, >= 32 chars, and not start with placeholder")

if not api_url or not api_url.startswith("https://"):
    sys.exit("Error: CLOUD_API_BASE_URL must be provided and start with https://")

env = os.environ.copy()
env.update({
    "USE_D1_DATABASE": "True",
    "RUN_CLOUD_SMOKE": "1",
    "CLOUD_API_BASE_URL": api_url,
    "D1_BRIDGE_URL": bridge_url,
    "D1_BRIDGE_SECRET_TOKEN": token,
    "D1_BRIDGE_TOKEN": token,
})

res = subprocess.run(
    ["backend/.venv/bin/pytest", "backend/tests/e2e/test_d1_cloud_smoke.py", "-m", "cloud_smoke", "-v", "-s"],
    env=env,
)
sys.exit(res.returncode)
'
    ```
    - Xác nhận toàn bộ 5 bước HTTP cốt lõi (Health, Auth JWT, Asset CRUD, Last Admin Trigger, Teardown Ownership) PASSED 100%.
    - Nếu R2 chưa kích hoạt, các kiểm tra liên quan đến R2 sẽ tự động skip an toàn (PASSED/SKIPPED).

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

### 1. Dừng Ingress & Cô Lập Ngay Lập Tức:
- Nhấn `Ctrl + C` hoặc kill tiến trình `cloudflared` Quick Tunnel -> Mọi truy cập từ Internet vào backend lập tức bị ngắt.
- Dừng container demo backend:
  ```bash
  cd /home/truong/Downloads/AssetHub
  docker compose -p assethub-demo -f deploy/docker-compose.demo.yml down
  ```

### 2. Khôi Phục Trạng Thái Frontend Pages:
> [!NOTE]
> Cloudflare Pages không hỗ trợ lệnh CLI `wrangler pages rollback`. Mọi thao tác rollback deployment giao diện được thực hiện chính thức qua Cloudflare Dashboard.
> *Lưu ý:* Đối với lần triển khai đầu tiên (first deployment), chưa tồn tại bản triển khai trước đó để rollback.

Quy trình rollback trên Cloudflare Dashboard:
1. Đăng nhập vào Cloudflare Dashboard, điều hướng đến **Workers & Pages > Pages > assethub**.
2. Tìm bản deployment ổn định trước đó trong danh sách Deployments, chọn **View details**.
3. Nhấp vào menu **Manage deployment** và chọn **Rollback to this deployment**.

### 3. Hoàn Tác CSDL D1 (D1 Time Travel & Snapshot Ngoại Tuyến):
> [!CAUTION]
> Tuyệt đối **KHÔNG** import trực tiếp file SQL dump vào CSDL D1 đang có dữ liệu bằng lệnh `d1 execute`. Việc này sẽ gây xung đột cấu trúc, trùng khóa chính và vi phạm trigger bảo vệ.

1. **Khôi phục bằng D1 Time Travel (Khuyến nghị chuẩn Cloudflare)**:
   - Sử dụng bookmark thu được trước khi thao tác:
     ```bash
     cd /home/truong/Downloads/AssetHub
     $WRANGLER d1 time-travel restore assethub-test-d1 --bookmark=<bookmark>
     ```
   - Hoặc khôi phục về mốc thời gian ISO-8601 trước khi xảy ra sự cố:
     ```bash
     cd /home/truong/Downloads/AssetHub
     $WRANGLER d1 time-travel restore assethub-test-d1 --timestamp="<ISO-8601-timestamp>"
     ```
   - Để kiểm tra các mốc thời gian và thông tin khôi phục khả dụng:
     ```bash
     cd /home/truong/Downloads/AssetHub
     $WRANGLER d1 time-travel info assethub-test-d1
     ```

2. **Snapshot SQL ngoại tuyến (`backups/d1_backup_*.sql`)**:
   - File xuất từ `$WRANGLER d1 export` được lưu trữ an toàn trong thư mục `backups/` (đã cấu hình trong `.gitignore`).
   - Đây là bản lưu trữ ngoại tuyến phục vụ khắc phục thảm họa (Disaster Recovery) trên một CSDL hoàn toàn mới hoặc để kiểm tra đối soát, không dùng để ghi đè lên CSDL đang vận hành.

### 4. Bảo Toàn CSDL Gốc Production:
- Container PostgreSQL production (`assethub-db-1` cổng 5433) và Backend chính (`assethub-backend-1` cổng 8000) hoàn toàn độc lập, không hề bị chỉnh sửa hay restart trong toàn bộ quá trình demo, duy trì hoạt động với RPO = 0s.
