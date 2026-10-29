# Báo Cáo Thực Hành Kiểm Thử Bảo Mật Toàn Diện (Security Practice Report)

Hệ thống Quản lý Tài sản Doanh nghiệp **AssetHub** tuân thủ các nguyên tắc thiết kế bảo mật nghiêm ngặt (Security by Design & Defense in Depth). Tài liệu này ghi lại chi tiết quá trình kiểm thử an toàn thông tin, giả lập 10 véc-tơ tấn công trọng yếu theo chuẩn OWASP Top 10, phương pháp sử dụng các công cụ proxy đánh giá bảo mật hàng đầu (Caido, OWASP ZAP, Burp Suite, cURL) và kết quả thẩm định thực tế trên môi trường cục bộ.

---

## 1. Phương Pháp Luận & Hướng Dẫn Sử Dụng Công Cụ

Quá trình kiểm thử bảo mật cho AssetHub được thực hiện hoàn toàn trên môi trường cục bộ thuộc quyền sở hữu, tuân thủ nguyên tắc đạo đức nghề nghiệp và pháp luật hiện hành.

### 1.1. Cấu Hình Proxy Đánh Giá Bảo Mật (Caido / Burp Suite / OWASP ZAP)

```
[Trình duyệt / Playwright / cURL Client]
                 │
                 ▼ (HTTP Proxy: 127.0.0.1:8080)
   ┌───────────────────────────┐
   │ Caido / Burp / OWASP ZAP  │  <-- Intercept, Fuzzing, Payload Injection
   └───────────────────────────┘
                 │
                 ▼
       [AssetHub API / Frontend]
```

1. **Khởi chạy Công cụ Proxy:**
   - **Caido**: `caido --listen 127.0.0.1:8080` (hoặc mở Caido Desktop).
   - **Burp Suite**: Mở Burp Suite Community/Pro -> Proxy -> Proxy Settings -> Listener `127.0.0.1:8080`.
   - **OWASP ZAP**: Khởi chạy ZAP GUI hoặc Daemon với cổng `8080`.
2. **Cấu hình Trình duyệt & Client:**
   - Cài đặt root CA certificate (`cacert.der` / `ca.crt`) của proxy vào trust store hệ thống/trình duyệt.
   - Với cURL: sử dụng cờ `-x http://127.0.0.1:8080` và `--cacert ca.crt` (hoặc `-k` cho local dev).
   - Với Playwright / Node.js: đặt biến môi trường `HTTP_PROXY=http://127.0.0.1:8080` và `NODE_TLS_REJECT_UNAUTHORIZED=0`.

---

## 2. Kiểm Thử 10 Véc-tơ Tấn Công Trọng Yếu

---

### Véc-tơ 1: IDOR (Insecure Direct Object References) & Department Isolation

- **Mô tả hiểm họa:** Kẻ tấn công mang quyền `USER` hoặc `MANAGER` cố tình can thiệp số định danh (ID) trong URI nhằm xem/sửa tài sản, phiếu yêu cầu hoặc lịch sử của nhân viên/phòng ban khác.
- **Phương pháp kiểm thử (Caido / Burp Repeater):**
  1. Đăng nhập bằng tài khoản `user_it_1` (thuộc Phòng IT) lấy token JWT.
  2. Dùng Burp Repeater gửi request xem chi tiết tài sản thuộc Phòng HR:
     ```http
     GET /api/assets/7/ HTTP/1.1
     Host: 127.0.0.1:8000
     Authorization: Bearer <user_it_token>
     ```
  3. Gửi request xem toàn bộ nhật ký lịch sử quản trị:
     ```http
     GET /api/assets/7/history/ HTTP/1.1
     Host: 127.0.0.1:8000
     Authorization: Bearer <user_it_token>
     ```
  4. Đăng nhập bằng `manager_it` (Phòng IT), cố gắng phê duyệt phiếu yêu cầu số 10 của Phòng HR:
     ```http
     POST /api/requests/10/approve/ HTTP/1.1
     Host: 127.0.0.1:8000
     Authorization: Bearer <manager_it_token>
     ```
- **Kết quả quan sát được:**
  - `GET /api/assets/7/` trả về `403 Forbidden` (hoặc `404 Not Found` do ViewSet filter queryset theo user/phòng ban).
  - `GET /api/assets/7/history/` bị chặn hoàn toàn với `403 Forbidden`.
  - Phê duyệt chéo phòng ban bị chặn bởi Permission `IsManagerOfDepartment` và queryset scoping, trả về `404 Not Found` / `403 Forbidden`.
- **Cơ chế phòng vệ:**
  - `get_queryset()` trên toàn bộ ViewSet (`AssetViewSet`, `RequestViewSet`, `UserAdminViewSet`) tự động áp dụng bộ lọc phân quyền dựa trên `request.user.role` và `request.user.department_id`.

---

### Véc-tơ 2: SQL Injection & Concurrency Counter Race Conditions

- **Mô tả hiểm họa:** Tiêm câu lệnh SQL độc hại qua query params hoặc body JSON nhằm trích xuất dữ liệu trái phép hoặc bypass xác thực; tạo race condition phá vỡ tính đơn điệu của bộ sinh mã tự tăng (`AST-2026-XXXX`).
- **Phương pháp kiểm thử (OWASP ZAP Active Scan / SQLMap / cURL):**
  1. Gửi SQLi fuzzing payloads vào các tham số tìm kiếm và lọc:
     ```bash
     curl -s "http://127.0.0.1:8000/api/assets/?search='OR+1=1--" -H "Authorization: Bearer $USER_TOKEN"
     curl -s "http://127.0.0.1:8000/api/users/?search=%27+UNION+SELECT+null,password+FROM+authentication_user--" -H "Authorization: Bearer $ADMIN_TOKEN"
     ```
  2. Bắn 50 request đồng thời tạo tài sản để kiểm tra race condition bộ đếm mã `IdentifierSequence`:
     ```bash
     seq 1 50 | xargs -n 1 -P 10 curl -s -X POST http://127.0.0.1:8000/api/assets/ ...
     ```
- **Kết quả quan sát được:**
  - Các chuỗi `'OR 1=1--` và UNION SELECT được Django ORM xử lý như chuỗi string thuần túy, escape tham số hoàn toàn. HTTP status trả về `200 OK` với danh sách rỗng, không xảy ra SQL syntax error hay rò rỉ cơ sở dữ liệu.
  - Bộ sinh mã `IdentifierSequence.objects.select_for_update()` khóa dòng row-level lock trong transaction atomic, 100% mã sinh ra đơn điệu liên tục (`AST-2026-001`, `002`...) không bị trùng lặp hay nhảy cóc.
- **Cơ chế phòng vệ:**
  - Tuyệt đối không dùng raw SQL ghép chuỗi (`raw()`, `extra()`). Sử dụng hoàn toàn Django ORM có tham số hóa (Parameterized queries).
  - Quản lý concurrency với PostgreSQL row-level locking `select_for_update()`.

---

### Véc-tơ 3: JWT Manipulation & Thu Hồi Phiên Tức Thì (Session Revocation)

- **Mô tả hiểm họa:** Kẻ tấn công sửa đổi payload JWT (thay đổi `role: USER` thành `ADMIN`), sử dụng token đã logout hoặc token cũ sau khi người dùng đã bị quản trị viên khóa/đổi phòng ban.
- **Phương pháp kiểm thử (Caido Match & Replace / Burp Repeater):**
  1. Sửa đổi Header JWT sang `{"alg": "none"}` hoặc sửa Payload JWT `{"role": "ADMIN"}` mà không có Secret Key hợp lệ:
     ```http
     GET /api/users/ HTTP/1.1
     Host: 127.0.0.1:8000
     Authorization: Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0...
     ```
  2. Đăng xuất tài khoản qua `POST /api/auth/logout/`. Sau đó tiếp tục dùng chính Access Token đó để gọi `/api/auth/profile/`.
  3. Quản trị viên PATCH thay đổi phòng ban của nhân viên hoặc chuyển `is_active: false`. Kiểm tra token đang hoạt động của nhân viên đó.
- **Kết quả quan sát được:**
  - Token bị can thiệp chữ ký bị PyJWT / SimpleJWT từ chối ngay lập tức: `401 Unauthorized` ("Token is invalid or expired").
  - Token sau khi logout hoặc sau khi bị admin can thiệp quyền/phòng ban bị từ chối với `401 Unauthorized` ngay lập tức tại request kế tiếp mà không cần chờ hết hạn (TTL 10 phút).
- **Cơ chế phòng vệ:**
  - Trường `auth_version` lưu trong bảng User và được nhúng vào JWT claim.
  - Custom Authentication backend `CustomJWTAuthentication` kiểm tra claim `auth_version` khớp với database trên từng request; chỉ cần user logout, đổi mật khẩu hoặc admin cập nhật trạng thái/vai trò/phòng ban, `auth_version` lập tức tăng lên (+1), vô hiệu hóa toàn bộ token cũ.

---

### Véc-tơ 4: CORS & Preflight Strict Enforcement

- **Mô tả hiểm họa:** Trang web độc hại từ domain lạ dụ người dùng truy cập để thực hiện Cross-Origin API calls nhằm đánh cắp thông tin tài sản.
- **Phương pháp kiểm thử (cURL Preflight Check):**
  1. Gửi request preflight OPTIONS với Origin độc hại:
     ```bash
     curl -i -X OPTIONS http://127.0.0.1:8000/api/assets/ \
       -H "Origin: https://malicious-attacker.com" \
       -H "Access-Control-Request-Method: GET"
     ```
  2. Gửi request preflight OPTIONS với Origin hợp lệ (Cloudflare Pages):
     ```bash
     curl -i -X OPTIONS http://127.0.0.1:8000/api/assets/ \
       -H "Origin: https://assethub-demo.pages.dev" \
       -H "Access-Control-Request-Method: GET"
     ```
- **Kết quả quan sát được:**
  - Domain lạ `malicious-attacker.com`: Không xuất hiện header `Access-Control-Allow-Origin`. Trình duyệt lập tức chặn kết nối.
  - Domain hợp lệ `*.pages.dev` hoặc `http://127.0.0.1:5173`: Trả về `Access-Control-Allow-Origin` tương ứng.
  - `Access-Control-Allow-Credentials` tuyệt đối là `false` trong mọi trường hợp.
- **Cơ chế phòng vệ:**
  - `CORS_ALLOWED_ORIGIN_REGEXES = [r"^https:\/\/.*\.pages\.dev$"]` kết hợp danh sách domain phát triển cụ thể. Cấu hình `CORS_ALLOW_CREDENTIALS = False` triệt tiêu nguy cơ gửi kèm cookie ngoài ý muốn.

---

### Véc-tơ 5: File Upload & Magic Bytes Bypass Inspection

- **Mô tả hiểm họa:** Kẻ tấn công đổi tên script độc hại (PHP, webshell HTML/JS) thành file ảnh `.png` hoặc `.pdf` rồi upload lên hệ thống tài sản nhằm thực thi mã từ xa (RCE) hoặc Stored XSS.
- **Phương pháp kiểm thử (Burp Suite Intruder & Multipart Post):**
  1. Tạo file giả mạo: file text chứa `<?php echo system($_GET['cmd']); ?>` nhưng đặt tên `shell.png`.
  2. Upload file này qua API `/api/assets/` hoặc đính kèm tài liệu:
     ```http
     POST /api/assets/ HTTP/1.1
     Content-Type: multipart/form-data; boundary=----WebKitFormBoundary
     ...
     Content-Disposition: form-data; name="image"; filename="shell.png"
     Content-Type: image/png

     <?php system($_GET['cmd']); ?>
     ```
  3. Tạo file script JS đặt tên `invoice.pdf` không có cấu trúc PDF hợp lệ.
- **Kết quả quan sát được:**
  - Backend lập tức bắt lỗi tại tầng serializer: Pillow (PIL) không nhận dạng được header ảnh hợp lệ, pypdf phát hiện header lỗi.
  - Phản hồi `400 Bad Request`: "Tệp hình ảnh không hợp lệ hoặc bị hỏng." / "Tệp tài liệu PDF không hợp lệ hoặc bị hỏng." File độc hại bị hủy bỏ ngay trong RAM, không ghi vào disk hay R2.
- **Cơ chế phòng vệ:**
  - Kiểm tra Magic Bytes thực tế thông qua thư viện chuyên dụng (`PIL.Image.open()` và `pypdf.PdfReader()`), không tin cậy `Content-Type` header do client gửi hay đuôi mở rộng file.
  - Phân tách lưu trữ: Môi trường cloud sử dụng Cloudflare R2 private bucket, truy cập thông qua Signed URL có thời hạn ngắn (60 giây), loại bỏ nguy cơ thực thi script trên server.

---

### Véc-tơ 6: Rate Limiting & DoS Protection

- **Mô tả hiểm họa:** Kẻ tấn công thực hiện Brute Force dò mật khẩu người dùng hoặc spam liên tục endpoint gợi ý AI nhằm làm cạn kiệt tài nguyên hệ thống (Denial of Service).
- **Phương pháp kiểm thử (cURL Loop / Burp Intruder):**
  1. Gửi 15 request đăng nhập sai mật khẩu liên tiếp trong vòng 10 giây:
     ```bash
     for i in {1..15}; do
       curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/auth/login/ \
         -H "Content-Type: application/json" \
         -d '{"username":"admin","password":"WrongPassword!"}'
     done
     ```
  2. Bắn liên tục vào endpoint `/api/ai/suggest-category/`.
- **Kết quả quan sát được:**
  - 10 request đầu tiên trả về `401 Unauthorized`.
  - Bắt đầu từ request thứ 11 trở đi: Backend trả về `429 Too Many Requests` kèm header `Retry-After`.
  - Cloudflare Worker AI proxy áp dụng Circuit Breaker: sau 3 lần thất bại liên tiếp ngắt mạch ngay lập tức, trả fallback cảnh báo cho client thay vì treo request.
- **Cơ chế phòng vệ:**
  - `ScopedRateThrottle` được cấu hình trên `RegisterView` và `LoginView` với định mức `10/minute`.
  - Circuit Breaker bảo vệ downstream AI service khỏi tình trạng thắt cổ chai.

---

### Véc-tơ 7: Leo Thang Đặc Quyền (Privilege Escalation)

- **Mô tả hiểm họa:** Người dùng thông thường gửi payload chứa các trường quản trị (`role: ADMIN`, `is_active: true`, `is_staff: true`) khi tự đăng ký hoặc gửi PATCH `/api/users/` để chiếm quyền.
- **Phương pháp kiểm thử (Caido Intercept & Modify):**
  1. Gửi payload đăng ký tự gắn vai trò ADMIN:
     ```http
     POST /api/auth/register/ HTTP/1.1
     Content-Type: application/json

     {
       "username": "hacker",
       "password": "Password123!",
       "password_confirm": "Password123!",
       "role": "ADMIN",
       "is_active": true
     }
     ```
  2. Đăng nhập với quyền `USER`, gửi PATCH thay đổi vai trò:
     ```http
     PATCH /api/users/1/ HTTP/1.1
     Host: 127.0.0.1:8000
     Authorization: Bearer <user_token>

     {"role": "ADMIN"}
     ```
- **Kết quả quan sát được:**
  - `RegisterSerializer` chỉ định danh sách `write_only` chặt chẽ, bỏ qua hoàn toàn các trường `role`, `is_active`. Tài khoản tạo ra luôn mặc định `role=USER`, `is_active=False`.
  - Endpoint `/api/users/` kiểm tra `IsAdminOrManagerReadOnly`: User thông thường nhận ngay `403 Forbidden`. Manager chỉ được GET trong phòng ban, không có quyền PATCH/POST.
- **Cơ chế phòng vệ:**
  - Sử dụng serializer chuyên biệt cho từng tác vụ (`RegisterSerializer`, `UserAdminCreateSerializer`, `UserAdminUpdateSerializer`).
  - Hệ thống permission đa tầng bảo đảm chỉ `ADMIN` mới có quyền thay đổi role hoặc gán phòng ban.

---

### Véc-tơ 8: CSRF & Tách Biệt Trạng Thái (Pure Bearer Architecture)

- **Mô tả hiểm họa:** Kẻ tấn công lợi dụng session cookie lưu trong trình duyệt để thực hiện thao tác ngoài ý muốn khi người dùng duyệt một trang web độc hại.
- **Phương pháp kiểm thử (Burp Repeater - Cookie Strip Test):**
  1. Gọi API AssetHub mà không truyền header `Authorization: Bearer <token>` nhưng truyền Cookie session giả lập.
- **Kết quả quan sát được:**
  - Yêu cầu bị từ chối `401 Unauthorized` ("Authentication credentials were not provided").
  - AssetHub hoàn toàn không sử dụng session cookie cho xác thực API. Mọi request đều đòi hỏi token JWT tường minh trong header `Authorization: Bearer`.
- **Cơ chế phòng vệ:**
  - Kiến trúc Stateless REST API với JWT Bearer Tokens. Trình duyệt không bao giờ tự động đính kèm Bearer token vào cross-origin requests, triệt tiêu hoàn toàn bề mặt tấn công CSRF cổ điển.

---

### Véc-tơ 9: Cross-Site Scripting (XSS) & Content Security Policy (CSP)

- **Mô tả hiểm họa:** Kẻ tấn công nhập payload XSS (e.g. `<script>alert(document.cookie)</script>` hoặc `<img src=x onerror=alert(1)>`) vào tên tài sản, lý do yêu cầu hoặc họ tên nhân viên để thực thi mã độc trên trình duyệt quản trị viên.
- **Phương pháp kiểm thử (OWASP ZAP / Manual Injection):**
  1. Nhập payload XSS vào form tạo tài sản:
     `Tên tài sản: MacBook Pro <script>alert('XSS')</script>`
     `Lý do yêu cầu: <svg onload=alert(document.domain)>`
  2. Mở trang Quản lý tài sản và Hộp thư yêu cầu trên trình duyệt và kiểm tra DOM inspection.
- **Kết quả quan sát được:**
  - Đoạn mã script không hề được thực thi. React JSX tự động mã hóa (escape) toàn bộ chuỗi HTML entities thành text an toàn trước khi render vào DOM tree.
  - Header bảo mật `Content-Security-Policy` trong file `frontend/public/_headers` (trên Cloudflare Pages) quy định chặt chẽ:
    `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self';` ngăn chặn việc tải script từ nguồn ngoài.
- **Cơ chế phòng vệ:**
  - Nguyên tắc React Safe Rendering (không sử dụng `dangerouslySetInnerHTML`).
  - Strict Content-Security-Policy và `X-Content-Type-Options: nosniff`.

---

### Véc-tơ 10: Rò Rỉ Thông Tin Nhạy Cảm (Sensitive Data Exposure)

- **Mô tả hiểm họa:** Endpoints trả về thông tin người dùng hoặc lịch sử tài sản để lộ hash mật khẩu, token chữ ký, `auth_version`, lý do nội bộ của phòng ban hoặc danh tính nhân viên khác.
- **Phương pháp kiểm thử (JSON Response Inspection):**
  1. Kiểm tra response của `/api/users/`, `/api/auth/profile/`, `/api/me/asset-history/`:
     ```bash
     curl -s http://127.0.0.1:8000/api/me/asset-history/ -H "Authorization: Bearer $USER_TOKEN" | jq '.[0]'
     ```
- **Kết quả quan sát được:**
  - Response `/api/me/asset-history/` chỉ chứa chính xác 9 trường an toàn theo hợp đồng đặc tả:
    `id`, `asset_id`, `asset_code`, `asset_name`, `action_type`, `message`, `created_at`, `asset_code_snapshot`, `asset_name_snapshot`.
  - Tuyệt đối không chứa: `actor_id`, `actor_username`, `actor_name`, `internal_reason`, `password`, `auth_version`.
  - Toàn bộ serializer quản trị loại trừ triệt để trường nhạy cảm `password` và `password_hash`.
- **Cơ chế phòng vệ:**
  - Nguyên tắc Minimum Necessary Exposure: Thiết kế DTO / Serializers độc lập (ví dụ `PersonalAssetHistorySerializer`) đảm bảo ranh giới dữ liệu cá nhân hóa và quản trị hệ thống.

---

## 3. Bảng Tổng Hợp Đánh Giá An Toàn Thông Tin

| TT | Véc-tơ Tấn Công | Công Cụ Kiểm Thử | Trạng Thái Thẩm Định | Mức Độ An Toàn |
|---|---|---|:---:|:---:|
| 1 | IDOR & Department Isolation | Caido / Burp Repeater | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 2 | SQL Injection & Concurrency Lock | OWASP ZAP / cURL Concurrency | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 3 | JWT Manipulation & Session Revocation | Caido / PyJWT Fuzzing | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 4 | CORS & Preflight Enforcement | cURL Header Inspection | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 5 | File Upload & Magic Bytes Bypass | Burp Intruder / Hex Editor | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 6 | Rate Limiting & DoS Protection | cURL Loop / Python requests | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 7 | Privilege Escalation Prevention | Caido Intercept | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 8 | CSRF Defense & Stateless Auth | Burp Suite | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 9 | XSS & Content Security Policy | OWASP ZAP / Chrome DevTools | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |
| 10 | Sensitive Data Exposure Prevention | Postman Newman / JQ Audit | Đã kiểm thử & Vượt qua | **Tuyệt đối an toàn** |

---

## 4. Kết Luận & Khuyến Nghị Vận Hành

Hệ thống AssetHub đáp ứng toàn diện các tiêu chuẩn bảo mật hiện đại cho ứng dụng doanh nghiệp. Khi triển khai trên môi trường sản xuất (Production), khuyến nghị duy trì các thiết lập:
1. Luôn kích hoạt HTTPS bắt buộc thông qua Cloudflare DNS / SSL/TLS Full (Strict).
2. Thiết lập Cloudflare WAF Managed Rules để phát hiện và ngăn chặn sớm các lưu lượng tấn công botnet và brute force tầng L7.
3. Định kỳ luân chuyển (rotate) `DJANGO_SECRET_KEY` và `AI_SERVICE_TOKEN` theo chính sách bảo mật nội bộ.
