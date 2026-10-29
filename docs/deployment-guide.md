# Hướng Dẫn Triển Khai Hệ Thống AssetHub (Deployment Guide)

Tài liệu hướng dẫn chi tiết quy trình triển khai, cấu hình môi trường và vận hành hệ thống quản lý tài sản doanh nghiệp **AssetHub** theo mô hình kiến trúc Hybrid kết hợp giữa máy chủ nội bộ (On-Premise / Local Server) và nền tảng đám mây Cloudflare.

---

## 1. Kiến Trúc Triển Khai Hybrid

Hệ thống AssetHub áp dụng mô hình kiến trúc **Hybrid Deployment**:
- **Edge Cloud (Cloudflare)**: Đảm nhận phân phối tĩnh (SPA Frontend), Edge Computing & AI Inference, lưu trữ tệp đối tượng (Object Storage), và đường hầm bảo mật (Zero Trust Tunnel).
- **Origin Server (Docker Compose)**: Chạy ứng dụng nghiệp vụ cốt lõi (Django REST Framework + Gunicorn) và cơ sở dữ liệu quan hệ (PostgreSQL 16) trên máy chủ riêng hoặc máy tính cá nhân.

### Bảng Phân Định Thành Phần Kiến Trúc

| Thành Phần | Công Nghệ / Nền Tảng | Nơi Thực Thi | Vai Trò & Chức Năng |
| :--- | :--- | :--- | :--- |
| **Frontend SPA** | React 18, Vite, TailwindCSS | **Cloudflare Pages** | Giao diện người dùng web, cache CDN toàn cầu, bảo vệ bằng Security Headers và SPA Rewrites. |
| **AI Microservice** | Cloudflare Workers, `@cf/meta/llama-3-8b-instruct` | **Cloudflare Workers AI** | Phân loại danh mục tài sản tự động bằng mô hình LLM tại biên (Edge). |
| **AI Logs Storage** | SQLite at Edge | **Cloudflare D1** | Lưu trữ phi tập trung lịch sử các gợi ý phân loại AI (`ai_suggestion_logs`). |
| **Object Storage** | S3-Compatible Storage | **Cloudflare R2** | Lưu trữ ảnh tài sản, avatar người dùng, tệp đính kèm với chính sách Zero Egress Fee. |
| **Secure Ingress** | Cloudflare Tunnel (`cloudflared`) | **Cloudflare Zero Trust** | Tạo kết nối mã hóa chiều ra (outbound) từ Origin Server đến Cloudflare Edge, không cần mở port mạng. |
| **Backend API** | Django 5.2, DRF, SimpleJWT, Gunicorn | **Origin Server (Docker)** | Xử lý logic nghiệp vụ, xác thực JWT, RBAC, API RESTful và kiểm soát quyền truy cập. |
| **Primary Database** | PostgreSQL 16 Alpine | **Origin Server (Docker)** | Lưu trữ toàn bộ dữ liệu nghiệp vụ chính: người dùng, phòng ban, tài sản, phiếu yêu cầu, báo cáo. |

---

## 2. Điều Kiện Hoạt Động Của Máy Chủ Khi Demo

Khi trình diễn (demo) hoặc cho phép người dùng từ bên ngoài Internet truy cập vào hệ thống:

1. **Điều kiện máy chủ Origin**:
   - Máy tính cá nhân (hoặc server on-premise) chạy Docker Compose phải ở trạng thái **Bật** và không rơi vào chế độ Sleep/Hibernate.
   - Các container Docker (`backend`, `db`, `cloudflared`) phải ở trạng thái `healthy` / `running`.
   - Kết nối mạng Internet của máy chủ phải duy trì liên tục và ổn định.
2. **Không cần IP tĩnh hay mở port (Port Forwarding)**:
   - Nhờ sử dụng **Cloudflare Tunnel (`cloudflared`)**, container chỉ thực hiện kết nối outbound (chiều ra) qua giao thức HTTP/2 hoặc QUIC tới mạng Cloudflare Anycast Edge.
   - Router NAT/firewall tại gia đình hay văn phòng không cần can thiệp mở cổng (không cần cấu hình DDNS, NAT port 80/443).
   - Địa chỉ IP thực tế của máy chủ hoàn toàn được ẩn giấu (che giấu nguồn gốc), giảm thiểu nguy cơ tấn công DDoS trực tiếp.

---

## 3. Giới Hạn Gói Miễn Phí (Cloudflare Free Tier Limits)

Hệ thống AssetHub được thiết kế tối ưu để có thể vận hành ổn định trong phạm vi gói Free Tier của Cloudflare:

| Dịch Vụ Cloudflare | Giới Hạn Gói Miễn Phí (Free Tier) | Lưu Ý Áp Dụng Cho AssetHub |
| :--- | :--- | :--- |
| **Cloudflare Pages** | - 500 builds / tháng<br>- Không giới hạn số lượng request<br>- Băng thông không giới hạn | Đủ cho quy trình CI/CD và truy cập frontend không giới hạn. |
| **Cloudflare Tunnel** | - Hoàn toàn miễn phí<br>- Không giới hạn băng thông<br>- Hỗ trợ nhiều subdomain | Định tuyến API backend từ máy local ra Internet bảo mật tuyệt đối. |
| **Cloudflare Workers AI**| - ~10,000 Neurons / ngày (miễn phí)<br>- Rate limit theo phút | Dùng cho tác vụ gợi ý danh mục tài sản khi tạo tài sản mới. Cần throttle gọi AI nếu vượt định mức. |
| **Cloudflare D1** | - 5,000,000 read rows / ngày<br>- 100,000 write rows / ngày<br>- 5 GB dung lượng tổng | Chỉ dùng lưu log phân loại AI (`ai_suggestion_logs`). Không vượt quá hạn mức hàng ngày. |
| **Cloudflare R2** | - 10 GB lưu trữ / tháng<br>- 1,000,000 Class A ops (PUT, LIST) / tháng<br>- 10,000,000 Class B ops (GET) / tháng<br>- **0đ phí tải xuống (Zero Egress Fees)** | Dùng lưu trữ avatar và ảnh thiết bị tài sản. Không tốn chi phí băng thông mạng. |

---

## 4. Các Điểm Cần Làm Rõ & Xác Nhận Với Giảng Viên (Spec §12)

Trong tài liệu đặc tả dự án (Spec §12), nhóm phát triển ghi nhận 3 vấn đề kỹ thuật quan trọng cần báo cáo và xác nhận cùng Giảng viên:

1. **Làm rõ thuật ngữ "cf CLI"**:
   - *Thực tế*: Tập đoàn Cloudflare **không phát hành CLI chính thức nào có tên là `cf`**. Công cụ dòng lệnh tiêu chuẩn duy nhất của Cloudflare cho Workers, Pages, D1, R2 là **`wrangler`**, và cho Tunnel là **`cloudflared`**. (Lệnh `cf` thường thuộc về hệ sinh thái Cloud Foundry CLI).
   - *Phương án thống nhất*: Toàn bộ kịch bản triển khai và tài liệu sử dụng chính thức lệnh `wrangler` và `cloudflared`.
2. **Xác nhận phạm vi lưu trữ Cloudflare D1**:
   - *Thực tế*: Cloudflare D1 là cơ sở dữ liệu SQLite phân tán tại Edge, thích hợp cho dữ liệu nhẹ hoặc log. Dữ liệu quan hệ nghiệp vụ phức tạp của AssetHub (tài sản, phân cấp người dùng, vòng đời phiếu mượn/trả, báo cáo kiểm kê) đòi hỏi tính toàn vẹn ACID, ràng buộc khóa ngoại chặt chẽ và truy vấn quan hệ của **PostgreSQL 16**.
   - *Phương án thống nhất*: Cloudflare D1 **chỉ sử dụng độc lập để ghi nhật ký gợi ý của Workers AI** (`ai_suggestion_logs`), không thay thế PostgreSQL quản lý dữ liệu nghiệp vụ chính.
3. **Xác nhận phương án chạy Docker Compose kết nối qua Cloudflare Tunnel**:
   - *Thực tế*: Việc đưa toàn bộ cơ sở dữ liệu PostgreSQL lên môi trường Serverless Cloudflare thuần túy không khả thi do thiếu hỗ trợ hệ quản trị CSDL quan hệ truyền thống.
   - *Phương án thống nhất*: Chạy toàn bộ backend Django + Gunicorn + PostgreSQL trong Docker Compose trên máy chủ/máy trạm của nhóm, kết nối public an toàn ra ngoài qua Cloudflare Tunnel (`api.assethub.example.com`).

---

## 5. Hướng Dẫn Cấu Hình Và Triển Khai Từng Bước

### Bước 1: Khởi Động Backend & Database Bằng Docker Compose

1. **Chuẩn bị file môi trường**:
   Sao chép file cấu hình mẫu từ thư mục `deploy/`:
   ```bash
   cp deploy/.env.demo.example deploy/.env.demo
   ```
   Chỉnh sửa các giá trị bí mật trong `deploy/.env.demo` (đặc biệt là `DJANGO_SECRET_KEY`, `DB_PASSWORD`, `DJANGO_ALLOWED_HOSTS`).

2. **Khởi động các dịch vụ bằng Docker Compose**:
   Đối với môi trường sản xuất / demo qua tunnel:
   ```bash
   docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --env-file deploy/.env.demo up -d --build
   ```
   *(Hoặc môi trường phát triển cục bộ: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`)*

3. **Chạy Migration và nạp dữ liệu mẫu**:
   ```bash
   # Áp dụng migration CSDL
   docker compose exec backend python manage.py migrate

   # Nạp dữ liệu danh mục và tài sản mẫu ban đầu
   docker compose exec backend python manage.py seed_data

   # Tạo tài khoản quản trị viên tối cao (nếu cần)
   docker compose exec backend python manage.py createsuperuser
   ```

4. **Kiểm tra trạng thái sẵn sàng**:
   ```bash
   curl -f http://127.0.0.1:8000/api/health/
   # Kết quả mong đợi: {"status": "ok", "db": "ok"}
   ```

---

### Bước 2: Thiết Lập Cloudflare Tunnel (`cloudflared`)

1. **Cài đặt tiện ích `cloudflared` trên máy chủ**:
   ```bash
   # Trên Ubuntu / Debian:
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```

2. **Đăng nhập và xác thực tài khoản Cloudflare**:
   ```bash
   cloudflared tunnel login
   # Trình duyệt sẽ mở URL để bạn cấp quyền cho tên miền quản lý trên Cloudflare.
   ```

3. **Tạo Tunnel mới cho AssetHub**:
   ```bash
   cloudflared tunnel create assethub-tunnel
   ```
   Lệnh sẽ tạo một Tunnel UUID (ví dụ: `abcd1234-5678-90ab-cdef-1234567890ab`) và file chứng thực tại `~/.cloudflared/<UUID>.json`.

4. **Đặt file thông tin xác thực vào dự án**:
   Sao chép file chứng thực JSON vào vị trí cấu hình bảo mật:
   ```bash
   mkdir -p deploy/cloudflared
   cp ~/.cloudflared/<UUID>.json deploy/cloudflared/credentials.json
   chmod 600 deploy/cloudflared/credentials.json
   ```

5. **Định tuyến bản ghi DNS**:
   Gắn tên miền API phụ tới Tunnel:
   ```bash
   cloudflared tunnel route dns assethub-tunnel api.assethub.example.com
   ```

6. **Cập nhật cấu hình Ingress**:
   Chỉnh sửa `deploy/cloudflared/config.yml` hoặc truyền biến môi trường:
   ```yaml
   tunnel: abcd1234-5678-90ab-cdef-1234567890ab
   credentials-file: /etc/cloudflared/credentials.json

   ingress:
     - hostname: api.assethub.example.com
       service: http://backend:8000
       originRequest:
         noTLSVerify: false
         httpHostHeader: api.assethub.example.com
     - service: http_status:404
   ```
   Khởi động service `cloudflared` trong Docker Compose, kiểm tra log:
   ```bash
   docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f cloudflared
   ```

---

### Bước 3: Thiết Lập Cloudflare R2 Bucket & Credentials

1. **Tạo R2 Bucket**:
   Truy cập **Cloudflare Dashboard > R2 Object Storage > Create bucket**:
   - Tên Bucket: `assethub-storage`
   - Vị trí (Location): `Automatic` hoặc `APAC (Châu Á Thái Bình Dương)`.
   *(Hoặc thực hiện qua dòng lệnh: `npx wrangler r2 bucket create assethub-storage`)*

2. **Tạo API Token cho R2**:
   - Tại trang **R2 Overview**, chọn **Manage R2 API Tokens > Create API Token**.
   - Quyền hạn (Permissions): **Object Read & Write**.
   - Phạm vi: Chỉ định cụ thể cho bucket `assethub-storage`.
   - Sao chép các thông tin:
     - `Access Key ID`
     - `Secret Access Key`
     - `Endpoint URL`: có định dạng `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

3. **Cấu hình vào Backend**:
   Điền các thông số vào file `.env` của backend:
   ```env
   R2_ACCESS_KEY_ID=<YOUR_R2_ACCESS_KEY_ID>
   R2_SECRET_ACCESS_KEY=<YOUR_R2_SECRET_ACCESS_KEY>
   R2_BUCKET_NAME=assethub-storage
   R2_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   ```

---

### Bước 4: Thiết Lập Cloudflare Worker, Workers AI & D1 (Wrangler)

1. **Cài đặt thư viện phụ thuộc**:
   ```bash
   cd cloudflare-worker
   npm install
   ```

2. **Tạo cơ sở dữ liệu D1 trên Cloudflare**:
   ```bash
   npx wrangler d1 create assethub-d1
   ```
   Ghi lại `database_id` được hiển thị và cập nhật vào file `cloudflare-worker/wrangler.toml`:
   ```toml
   [[d1_databases]]
   binding = "DB"
   database_name = "assethub-d1"
   database_id = "your-actual-d1-database-uuid"
   ```

3. **Khởi tạo bảng cơ sở dữ liệu D1 trên Cloud**:
   ```bash
   npx wrangler d1 execute assethub-d1 --file=./schema.sql --remote
   ```

4. **Thiết lập khóa bí mật chia sẻ (AI Service Token)**:
   ```bash
   npx wrangler secret put AI_SERVICE_TOKEN
   # Nhập token bảo mật (chuỗi ngẫu nhiên tối thiểu 32 ký tự, trùng khớp với AI_SERVICE_TOKEN của backend)
   ```

5. **Deploy Worker lên Cloudflare**:
   ```bash
   npx wrangler deploy
   ```
   Sau khi deploy thành công, Worker sẽ có URL công khai, ví dụ: `https://assethub-ai-worker.<subdomain>.workers.dev`.
   Cập nhật URL này vào biến `AI_WORKER_URL` trong file `.env` của backend.

---

### Bước 5: Build Và Deploy Frontend Lên Cloudflare Pages

1. **Kiểm tra cấu hình Security Headers và SPA Redirects**:
   - Header bảo mật tại `frontend/public/_headers` (bảo vệ chống Clickjacking, MIME-sniffing, áp dụng Content Security Policy nghiêm ngặt).
   - SPA route rewrites tại `frontend/public/_redirects` (`/* /index.html 200`) đảm bảo khi người dùng tải lại trang con trên React Router không bị lỗi 404 Not Found.

2. **Build mã nguồn Frontend**:
   ```bash
   # Thiết lập địa chỉ API Backend môi trường production
   export VITE_API_BASE_URL="https://api.assethub.example.com"
   npm --prefix frontend run build
   ```
   Xác minh các file cấu hình quan trọng đã nằm trong thư mục xuất bản:
   ```bash
   test -f frontend/dist/_headers && echo "Headers OK"
   test -f frontend/dist/_redirects && echo "Redirects OK"
   ```

3. **Deploy lên Cloudflare Pages**:
   - **Cách 1: Sử dụng Wrangler CLI**:
     ```bash
     npx wrangler pages deploy frontend/dist --project-name=assethub
     ```
   - **Cách 2: Kết nối GitHub Repository qua Dashboard**:
     - Vào **Cloudflare Dashboard > Workers & Pages > Create application > Pages > Connect to Git**.
     - Cấu hình Build settings:
       - Framework preset: `Vite`
       - Build command: `npm run build`
       - Build output directory: `dist`
       - Root directory: `frontend`
     - Environment variables: `VITE_API_BASE_URL=https://api.assethub.example.com`.

4. **Đồng bộ CORS trên Backend**:
   Đảm bảo tên miền của Pages (ví dụ: `https://assethub.pages.dev` hoặc tên miền riêng `https://assethub.example.com`) được khai báo trong `CORS_ALLOWED_ORIGINS` của backend. Hệ thống đã tự động chấp nhận định dạng `*.pages.dev` qua cấu hình regex `CORS_ALLOWED_ORIGIN_REGEXES`.

---

### Bước 6: Quy Trình Sao Lưu Và Phục Hồi Dữ Liệu PostgreSQL

Để đảm bảo tính liên tục của dữ liệu và phòng ngừa sự cố, định kỳ thực hiện các thao tác sau:

1. **Quy trình sao lưu (Backup)**:
   Thực hiện trích xuất dữ liệu nhị phân nén (custom format) từ container cơ sở dữ liệu:
   ```bash
   mkdir -p backups
   # Xuất bản sao lưu với timestamp
   docker compose exec -T db pg_dump -U assethub_user -F c assethub > backups/assethub_$(date +%Y%m%d_%H%M%S).dump
   ```
   *(Để xuất định dạng SQL thuần có thể đọc được: `docker compose exec -T db pg_dump -U assethub_user assethub > backups/assethub_plain_$(date +%Y%m%d).sql`)*

2. **Quy trình phục hồi (Restore)**:
   Khi cần khôi phục lại dữ liệu từ một bản backup:
   ```bash
   # 1. Tạm thời dừng kết nối từ backend để tránh ghi dữ liệu dở dang
   docker compose stop backend

   # 2. Xóa và tạo lại cơ sở dữ liệu trắng
   docker compose exec -T db dropdb -U assethub_user --if-exists assethub
   docker compose exec -T db createdb -U assethub_user assethub

   # 3. Phục hồi từ tệp .dump
   docker compose exec -T db pg_restore -U assethub_user -d assethub < backups/assethub_YYYYMMDD_HHMMSS.dump

   # 4. Khởi động lại backend
   docker compose start backend
   ```

3. **Tự động hóa sao lưu bằng Cron (Linux)**:
   Thêm tác vụ vào crontab (`crontab -e`) để tự động sao lưu lúc 02:00 sáng mỗi ngày và xóa các bản sao lưu cũ hơn 14 ngày:
   ```cron
   0 2 * * * cd /path/to/AssetHub && docker compose exec -T db pg_dump -U assethub_user -F c assethub > backups/assethub_$(date +\%Y\%m\%d_\%H\%M\%S).dump && find backups/ -type f -name "*.dump" -mtime +14 -delete
   ```

---
*Tài liệu hoàn tất bởi Đội ngũ Kỹ thuật AssetHub - Sẵn sàng cho quá trình đánh giá và nghiệm thu hệ thống.*
