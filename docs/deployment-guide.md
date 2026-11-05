# Hướng Dẫn Triển Khai Hệ Thống AssetHub (Deployment Guide)

Tài liệu hướng dẫn chi tiết quy trình chuẩn bị cấu hình, đối chiếu tài nguyên thực tế và các bước triển khai hệ thống **AssetHub** theo mô hình Hybrid Edge-Core: Backend Origin phục vụ tại cổng độc lập 8001 qua Cloudflare Tunnel kết hợp Cloudflare D1, Workers AI, R2 và Pages.

Mọi lệnh trong tài liệu này mặc định thực thi từ thư mục gốc repository: `/home/truong/Downloads/AssetHub`.

---

## 1. Bảng Đối Chiếu Tài Nguyên Thực Tế & Trạng Thái Cloudflare

Snapshot xác nhận ngày **2026-11-05** (GMT+7), gồm hai nguồn: hồ sơ nghiệm thu `docs/progress-report.md` Mục 29.6 và 30.1–30.6, và phép đo chỉ đọc từ máy backend cùng ngày. Máy backend mất điện và khởi động lại lúc 23:49 cùng ngày, nên trạng thái dưới đây đã cập nhật theo kiểm tra lúc 23:56. Đây là ảnh chụp tại thời điểm đó, không phải trạng thái realtime; đối chiếu lại trước khi thao tác.

| Thành phần | Trạng thái Cloud | Tên / Định danh thực tế | URL / Endpoint thực tế | Config sử dụng | Bí mật cần điền (Local) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cloudflare Account** | PRESENT | `Hnhoa494@gmail.com's Account`<br>`5e76c2bf4e09587a010d31ea416ec8d3` | — | `deploy/.env.demo.example` | Token OAuth Wrangler có sẵn |
| **Worker Subdomain** | PRESENT | `hnhoa494-assethub` | `*.hnhoa494-assethub.workers.dev` | `wrangler.toml` (tự động) | — |
| **D1 Database** | PRESENT | `assethub-test-d1`<br>UUID: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1` | — | `cloudflare-worker/bridge/wrangler.toml`<br>`cloudflare-worker/wrangler.toml` | — *(Dữ liệu demo đang dùng, không phải dữ liệu sản xuất thực, nhưng vẫn PHẢI BẢO TỒN; không tự ý xóa/reset. Số liệu thay đổi theo thời gian, không dùng số ghi trong tài liệu)* |
| **D1 Bridge Worker** | PRESENT | `assethub-test-worker` | `https://assethub-test-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/bridge/wrangler.toml` | `D1_BRIDGE_SECRET_TOKEN` *(Khớp `BRIDGE_SECRET_TOKEN` đã deploy trên Worker; bảo toàn bí mật, không rotate không cần thiết)* |
| **AI Worker** | DEPLOYED | `assethub-ai-worker` | `https://assethub-ai-worker.hnhoa494-assethub.workers.dev` | `cloudflare-worker/wrangler.toml` | `AI_SERVICE_TOKEN` (tối thiểu 32 ký tự) *(Nghiệm thu suy luận theo hồ sơ Mục 29; phép đo 2026-11-05 chỉ xác nhận host phản hồi, không gọi AI)* |
| **R2 Storage** | ENABLED | `assethub-attachments` *(private bucket, APAC)* | `https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com` | `deploy/.env.demo.example` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` *(Theo hồ sơ Mục 30.1 và 30.4; bucket không được đo lại trong phép đo 2026-11-05)* |
| **Demo Backend Origin**| RUNNING (LOCAL, sau khởi động lại)| Service `backend`<br>(Container cổng 8001, mount `ro`, pull `never`)| `http://127.0.0.1:8001` | `deploy/docker-compose.demo.yml`<br>`deploy/.env.demo` | `DJANGO_SECRET_KEY` (sinh ngẫu nhiên 64 ký tự, ghi trực tiếp vào file không in ra terminal) |
| **Cloudflare Tunnel** | DOWN (sau mất điện) | Quick Tunnel qua binary host `/usr/local/bin/cloudflared` | **Chưa có URL hoạt động.** URL trước mất điện `https://away-server-pot-catalogue.trycloudflare.com` chỉ là snapshot cũ, hiện không phân giải DNS | `deploy/.env.demo` | — |
| **Frontend Pages** | DEPLOYED (API chưa phục hồi) | Project `assethub`, production branch `main` | `https://assethub-97m.pages.dev` *(chưa redeploy; bản build đang trỏ tới URL tunnel cũ)* | `frontend/dist` | — |

### Phạm vi đã có bằng chứng và giới hạn hiện tại

- **Sau mất điện (đo trong khoảng 23:49–23:56 ngày 2026-11-05):** container backend 8000, 8001 và PostgreSQL tự khởi động và healthy; `/api/health/` qua 8000 và 8001 trả 200 (0,016 và 0,167 giây); Pages trả 200 (0,405 giây). Đây chỉ là một mẫu, không chứng minh đã hết chậm.
- **API công khai chưa phục hồi:** không còn tiến trình `cloudflared` và chưa có URL tunnel mới. Giao diện Pages có thể tải được, nhưng đăng nhập và thao tác cần API sẽ không hoạt động cho đến khi mở lại tunnel và redeploy Pages (bước 8, 9, 10b).
- **Phép đo trước mất điện:** Pages trả 200; `/api/auth/profile/` không token trả 401 qua cả cổng 8001 và Quick Tunnel; `/health` của bridge trả 200; `/api/health/` trả 200 nhưng chậm (mẫu 4,37 giây).
- **Đính kèm tệp:** đã kiểm chứng qua HTTP API tới R2 và D1 (Mục 30.4). **Chưa kiểm tay thao tác đính kèm trên giao diện** (Mục 30.5).
- **Quick Tunnel là tạm thời:** mỗi lần `cloudflared` khởi động lại sẽ có URL mới, và tiến trình này không tự chạy lại sau khi máy khởi động. Cần làm lại bước 8, 9 và 10b vì URL backend được build vào frontend.
- **Demo phụ thuộc máy backend:** Pages vẫn tải được, nhưng đăng nhập và mọi API cần máy này bật, cùng container `backend` và tiến trình `cloudflared` đang chạy.
- **Độ trễ tới D1 bridge đang được chẩn đoán:** trong phép đo trước mất điện, `/health` của bridge không chạm DB nhưng dao động từ 0,18 đến 9,99 giây, và healthcheck của container có lần quá 5 giây. Chưa kết luận nguyên nhân là ISP, WiFi, Worker hay D1. Không hardcode IP của bridge để né lỗi.
- **Tối ưu truy vấn chưa phải bằng chứng hết chậm:** số query của báo cáo mới được giảm và kiểm tra trên PostgreSQL local, chưa deploy và chưa đo trên D1.
- **Presigned URL R2:** hết hạn sau 900 giây, client cần xin URL mới.

---

## 2. Thứ Tự Triển Khai Theo Phụ Thuộc (Dependency Sequence)

> [!IMPORTANT]
> **Ranh giới an toàn:** Tất cả các lệnh deploy cloud dưới đây CHỈ được thực thi sau khi Codex kiểm duyệt và người dùng chấp thuận. Hệ thống demo Tuần 4 đã được triển khai (xem Mục 1), nên các lệnh dưới đây là hướng dẫn dựng lại hoặc cập nhật, không phải việc đang chờ chạy.
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
   - D1 `assethub-test-d1` đã có schema 19 bảng, triggers bảo vệ theo hồ sơ Mục 30.6 và đang chứa dữ liệu demo cần bảo toàn.
   - Tuyệt đối **KHÔNG DROP**, không reset và không chạy `migrate_pg_to_d1` đè dữ liệu.
   - Bản SQL export chỉ đóng vai trò snapshot an toàn offline, **tuyệt đối không** nạp đè SQL dump trực tiếp vào CSDL đang có bảng/dữ liệu bằng lệnh `d1 execute`.

5. **Trạng thái Cloudflare R2**:
   - R2 đã được bật và bucket `assethub-attachments` đã có theo hồ sơ Mục 30.1. Các bước dưới đây chỉ dành cho khi dựng trên tài khoản khác.
   - Nếu tài khoản báo lỗi 10042 (R2 chưa bật): Người dùng cần truy cập Cloudflare Dashboard để kích hoạt R2 thủ công (kèm phương thức thanh toán nếu tài khoản yêu cầu), tạo bucket `assethub-attachments` và API Token tương ứng, sau đó điền `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` vào `deploy/.env.demo`.
   - Nếu tài khoản chưa kích hoạt R2: Để trống các giá trị R2 trong `deploy/.env.demo`. Hệ thống backend và test suite sẽ tự động bỏ qua (skip gracefully) các bài test upload tệp mà không làm gián đoạn hệ thống.

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
   - Ghi lại URL công khai in ra trên terminal, ví dụ: `https://asset-demo-xyz.trycloudflare.com`. Hiện chưa có URL hoạt động (xem Mục 1), nên đây là bước cần làm để phục hồi API công khai, sau khi được chấp thuận.
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

10. **Deploy lên Cloudflare Pages**:
    ```bash
    cd /home/truong/Downloads/AssetHub

    # 10a. Chỉ khi dựng trên tài khoản chưa có project assethub (project hiện tại đã tồn tại, bỏ qua bước này):
    $WRANGLER pages project create assethub --production-branch main

    # 10b. Triển khai thư mục frontend/dist lên Pages project assethub nhánh main:
    $WRANGLER pages deploy frontend/dist --project-name assethub --branch main
    ```
    - Ghi lại URL thực tế từ output deployment; project hiện tại dùng `https://assethub-97m.pages.dev`.
    - `CORS_ALLOWED_ORIGINS` trong `deploy/.env.demo` phải khớp URL đó.
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
for k, v in env_vals.items():
    if v is not None:
        env[k] = v

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
