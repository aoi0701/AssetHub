# Notes Agent 4: Runtime, R2, AI & Deployment Plan

Review and resolution for Codex review points 3 & 5 (/home/truong/Downloads/AssetHub-review-plan-2026-11-04.md).
Target branch: feat/cloudflare-d1.
Role: Agent 4 (Runtime, R2, AI & Deployment Plan Specialist).
Status: PROPOSED PLAN AMENDMENTS FOR COORDINATOR SYNTHESIS.

---

## 1. Executive Summary

Current plan (2026-11-03-cloudflare-d1-implementation.md) has six critical gaps in runtime, storage, AI, and deploy:
1. No explicit runtime switch from PostgreSQL to D1 as `DATABASES['default']`.
2. Docker Compose and backend settings miss required bridge and R2/AI environment variables.
3. Insecure wildcard Pages CORS (`r"^https:\/\/.*\.pages\.dev$"`) and wildcard `ALLOWED_HOSTS`.
4. Overwrites existing `backend/apps/common/storage_backends.py` (which already has 13 passing tests).
5. References deprecated AI model (`@cf/meta/llama-3.1-8b-instruct`) instead of active FP8 model (`@cf/meta/llama-3.1-8b-instruct-fp8`).
6. End-of-plan verification lists only 22 adapter unit tests; misses full regression on D1, frontend build, and cloud smoke tests.

Below are exact plan revisions for the coordinator.

---

## 2. Runtime D1 Configuration (Task 1 & Task 6 Resolution - Codex Point 3)

### 2.1. Switch `DATABASES['default']` to D1 with Zero Silent Fallback

Location: `backend/assethub_api/settings.py`.
Ownership: Task 6 (with connection params defined in Task 1).

#### Design Rule
- Control active database through explicit environment flag: `USE_D1_DATABASE=true|false`.
- When `USE_D1_DATABASE=True`:
  - `DATABASES['default']` MUST use `assethub_api.backends.d1`.
  - `DATABASES['postgres']` preserved as secondary alias for 1:1 data reconciliation and instant disaster rollback.
  - ZERO silent fallback to PostgreSQL. If D1 bridge returns error, query MUST fail immediately with `DatabaseError` or `OperationalError`. Never catch D1 error to query PostgreSQL behind caller's back.
- When `USE_D1_DATABASE=False` (pre-cutover development/staging):
  - `DATABASES['default']` remains PostgreSQL.
  - `DATABASES['d1']` available for migration commands and explicit D1 tests (`using='d1'`).

#### Code Specification for `backend/assethub_api/settings.py`
```python
# Database configuration - Hybrid PostgreSQL & Cloudflare D1
USE_D1_DATABASE = os.environ.get('USE_D1_DATABASE', 'False').lower() in ('true', '1', 'yes')

D1_BRIDGE_URL = os.environ.get('D1_BRIDGE_URL', '').rstrip('/')
D1_BRIDGE_SECRET_TOKEN = os.environ.get('D1_BRIDGE_SECRET_TOKEN') or os.environ.get('D1_BRIDGE_TOKEN', '')
D1_DATABASE_NAME = os.environ.get('D1_DATABASE_NAME', 'assethub-test-d1')
D1_TIMEOUT_SECONDS = float(os.environ.get('D1_TIMEOUT_SECONDS', '15.0'))

D1_DATABASE_CONFIG = {
    'ENGINE': 'assethub_api.backends.d1',
    'NAME': D1_DATABASE_NAME,
    'BRIDGE_URL': D1_BRIDGE_URL,
    'BRIDGE_TOKEN': D1_BRIDGE_SECRET_TOKEN,
    'TIMEOUT': D1_TIMEOUT_SECONDS,
    'TEST': {
        'NAME': D1_DATABASE_NAME,
    },
}

POSTGRES_DATABASE_CONFIG = {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': os.environ.get('DB_NAME', 'assethub'),
    'USER': os.environ.get('DB_USER', 'assethub_user'),
    'PASSWORD': os.environ.get('DB_PASSWORD', 'assethub_password'),
    'HOST': os.environ.get('DB_HOST', 'db'),
    'PORT': os.environ.get('DB_PORT', '5432'),
    'TEST': {
        'NAME': os.environ.get('TEST_DB_NAME', 'test_assethub'),
    },
}

if USE_D1_DATABASE:
    DATABASES = {
        'default': D1_DATABASE_CONFIG,
        'postgres': POSTGRES_DATABASE_CONFIG,  # Explicit reconciliation / rollback alias only
    }
else:
    DATABASES = {
        'default': POSTGRES_DATABASE_CONFIG,
        'd1': D1_DATABASE_CONFIG,  # Migration source/target and isolated testing
    }
```

### 2.2. Environment Variable Wiring: Compose, Env Examples, Settings

#### Configuration Ownership Matrix
| File | Owning Task | Variables Managed |
| :--- | :--- | :--- |
| `docker-compose.yml` | Task 6 | Inject D1, R2, AI, SSL, and CORS vars into `backend` container |
| `docker-compose.dev.yml` | Task 6 | Maintain dev defaults for local testing |
| `.env.example` | Task 6 | Document all Cloudflare & D1 variables with secure defaults |
| `backend/assethub_api/settings.py` | Task 6 | Parse and validate all runtime variables |

#### Required Variables in `docker-compose.yml` (`services.backend.environment`)
```yaml
      # Database Mode Switch
      USE_D1_DATABASE: ${USE_D1_DATABASE:-false}
      D1_BRIDGE_URL: ${D1_BRIDGE_URL:-https://assethub-test-worker.hnhoa494-assethub.workers.dev}
      D1_BRIDGE_SECRET_TOKEN: ${D1_BRIDGE_SECRET_TOKEN:-}
      D1_DATABASE_NAME: ${D1_DATABASE_NAME:-assethub-test-d1}
      D1_TIMEOUT_SECONDS: ${D1_TIMEOUT_SECONDS:-15.0}

      # Cloudflare R2 Storage
      R2_ACCESS_KEY_ID: ${R2_ACCESS_KEY_ID:-}
      R2_SECRET_ACCESS_KEY: ${R2_SECRET_ACCESS_KEY:-}
      R2_BUCKET_NAME: ${R2_BUCKET_NAME:-assethub-media}
      R2_ENDPOINT_URL: ${R2_ENDPOINT_URL:-https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com}
      R2_PRESIGNED_EXPIRATION_SECONDS: ${R2_PRESIGNED_EXPIRATION_SECONDS:-900}

      # Cloudflare Workers AI
      AI_WORKER_URL: ${AI_WORKER_URL:-http://127.0.0.1:8787}
      AI_SERVICE_TOKEN: ${AI_SERVICE_TOKEN:-dev-ai-secret-token-2026}
      AI_MODEL: ${AI_MODEL:-@cf/meta/llama-3.1-8b-instruct-fp8}

      # Reverse Proxy & Ingress
      SECURE_PROXY_SSL_HEADER: ${SECURE_PROXY_SSL_HEADER:-HTTP_X_FORWARDED_PROTO,https}
      CORS_ALLOWED_ORIGIN_REGEXES: ${CORS_ALLOWED_ORIGIN_REGEXES:-}
```

#### Required Additions to `.env.example`
```bash
# Cloudflare D1 Bridge Database Configuration
USE_D1_DATABASE=false
D1_BRIDGE_URL=https://assethub-test-worker.hnhoa494-assethub.workers.dev
D1_BRIDGE_SECRET_TOKEN=<D1_BRIDGE_SECRET_TOKEN_REDACTED>
D1_DATABASE_NAME=assethub-test-d1
D1_TIMEOUT_SECONDS=15.0

# Cloudflare R2 Object Storage Credentials (Private Bucket)
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=assethub-media
R2_ENDPOINT_URL=https://5e76c2bf4e09587a010d31ea416ec8d3.r2.cloudflarestorage.com
R2_PRESIGNED_EXPIRATION_SECONDS=900

# Cloudflare Workers AI Microservice
AI_WORKER_URL=https://assethub-ai-worker.hnhoa494-assethub.workers.dev
AI_SERVICE_TOKEN=dev-ai-secret-token-2026
AI_MODEL=@cf/meta/llama-3.1-8b-instruct-fp8

# Ingress & Security Headers
SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,backend,api.assethub.vn
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### 2.3. Security Hardening: `ALLOWED_HOSTS` & Specific Pages CORS Regex

#### The Vulnerability
Current `backend/assethub_api/settings.py`:
- Line 25: `DJANGO_ALLOWED_HOSTS` defaults to `*`. Insecure for production ingress.
- Line 208: `CORS_ALLOWED_ORIGIN_REGEXES = [r"^https:\/\/.*\.pages\.dev$"]`.
- Hazard: ANY malicious project hosted on `*.pages.dev` can make cross-origin authenticated requests to AssetHub API.

#### The Hardened Settings
```python
# Security Hardening: Restrict ALLOWED_HOSTS
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        'DJANGO_ALLOWED_HOSTS',
        'localhost,127.0.0.1,backend'
    ).split(',')
    if host.strip()
]

# Security Hardening: Strict Approved Pages Domain Regex (No Wildcard Pages)
# Matches: https://assethub.pages.dev and https://<branch-or-commit>.assethub.pages.dev
# Rejects: https://malicious.pages.dev
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$",
]
custom_cors_regex = os.environ.get('CORS_ALLOWED_ORIGIN_REGEXES')
if custom_cors_regex:
    CORS_ALLOWED_ORIGIN_REGEXES.extend([
        r.strip() for r in custom_cors_regex.split(',') if r.strip()
    ])

CORS_ALLOW_CREDENTIALS = False

CSRF_TRUSTED_ORIGINS = [
    'https://assethub.pages.dev',
    'https://*.assethub.pages.dev',
]
```

### 2.4. Canonical Worker Directory & Deploy Workflow

To eliminate ambiguity between test spike and production paths:

#### 1. Staging / Test Bed Worker
- Path: `spikes/cf-d1-spike/bridge-worker/`
- Configuration: `spikes/cf-d1-spike/bridge-worker/wrangler.jsonc`
  - `name`: `assethub-test-worker`
  - `database_name`: `assethub-test-d1`
  - `database_id`: `3850e3a1-7c57-42f4-afd6-1fa1655bbcf1`
- Scope: Sole canonical location for Task 0 protocol fixes, Node regression tests, and cloud test verification.
- Deploy Command:
  ```bash
  cd spikes/cf-d1-spike/bridge-worker && npx wrangler deploy
  ```

#### 2. Production Bridge Worker
- Path: `cloudflare-worker/bridge/`
- Configuration: `cloudflare-worker/bridge/wrangler.toml` (created in Task 2)
  - `name`: `assethub-bridge-worker`
  - DDL schema source: `cloudflare-worker/bridge/schema.sql`
- Status: Production deployment is LOCKED. No production deployment allowed until explicit cutover milestone approval.

#### 3. AI Proxy Worker
- Path: `cloudflare-worker/ai/` (or `cloudflare-worker/`)
- Configuration: `cloudflare-worker/wrangler.toml`
  - `name`: `assethub-ai-worker`
  - `binding`: `AI`
- Model: `@cf/meta/llama-3.1-8b-instruct-fp8`

---

## 3. Task 5 & Task 6 Inheritance and Scope (Codex Point 5)

### 3.1. Modify Existing Storage Backend (`backend/apps/common/storage_backends.py`)

Current plan wrongly listed `Create: backend/apps/common/storage_backends.py`.
File ALREADY exists (213 lines) with 13 passing unit tests in `backend/tests/test_storage.py`.

#### Strict Rules for Task 5
- Action MUST be `Modify: backend/apps/common/storage_backends.py`. DO NOT overwrite or delete existing code.
- MUST preserve:
  - `AttachmentStorage` polymorphic factory (`__new__` dispatch).
  - `LocalDevAttachmentStorage` for offline dev/test runs.
  - `generate_local_presigned_url` and `verify_local_presigned_url` HMAC-SHA256 signatures.
  - `MediaDownloadView` signed URL verification and file download response.
  - Existing upload security validation: Pillow image integrity, pypdf document integrity, 5 MiB / 10 MiB limits, role-based upload permissions.
- Modifications for Task 5:
  - Make `expire_seconds` configurable with default `900` (15 minutes):
    ```python
    def get_presigned_download_url(
        self,
        name: str,
        expire_seconds: int = None,
        filename: str = None,
        as_attachment: bool = False
    ) -> str:
        if expire_seconds is None:
            expire_seconds = int(getattr(settings, 'R2_PRESIGNED_EXPIRATION_SECONDS', 900))
        ...
    ```
  - In `backend/apps/assets/views.py`: use `settings.R2_PRESIGNED_EXPIRATION_SECONDS` (default 900s) for asset attachment download endpoint.
  - Ensure existing tests in `backend/tests/test_storage.py` continue to pass (e.g. `test_r2_storage_presigned_url_generation` passes explicit `expire_seconds=60`).

### 3.2. Workers AI Model: Active Model Verification

#### Model Research Findings
- `@cf/meta/llama-3.1-8b-instruct` redirects to unavailable on Cloudflare documentation.
- Documented active model:
  `@cf/meta/llama-3.1-8b-instruct-fp8`
  Source: https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/
  Specs: 8-bit floating point quantization, 128,000 token context window, optimized for JSON schema output, low neuron footprint (~250-350 neurons per category suggestion).
- Fallback model:
  `@cf/meta/llama-3-8b-instruct` (8,192 token context, low latency).

#### Implementation Contract
- Default model in Worker: `env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct-fp8'`.
- Default in backend `settings.py`: `AI_MODEL = os.environ.get('AI_MODEL', '@cf/meta/llama-3.1-8b-instruct-fp8')`.
- Worker script catches Cloudflare error 10016 (Neurons limit exceeded) and returns HTTP 429 `QUOTA_EXHAUSTED`.
- Django `AiProxyService` activates Tier 3 Heuristic Keyword Matching Fallback without raising 500 error to user.

### 3.3. Strict Execution Boundaries for Task 5

Can develop in parallel with Task 3 & 4 under strict boundaries:
- Code, unit tests, and mock tests run locally without cloud mutation.
- STRICTLY EXCLUDED:
  - No R2 checkout or paid card activations.
  - No production media bucket mutations.
  - No domain or DNS record changes.
  - Cloud validation ONLY uses approved test resources (`assethub-test-worker`, `assethub-test-d1`).

### 3.4. Comprehensive End-of-Plan Verification Gate

Current plan gate only checked 22 adapter unit tests. That is insufficient.
Full verification suite must execute all four verification tiers:

```
[ Tier 1: Unit & Protocol ]
- 22+ tests in assethub_api/backends/d1/tests/
- Node tests: spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs

[ Tier 2: Storage & AI Unit/Integration ]
- 13 storage tests: tests/test_storage.py
- AI proxy tests: tests/test_ai_proxy.py

[ Tier 3: Existing Core Business & Security on D1 ]
- Asset business integrity: apps/assets/tests/
- Requests workflow: apps/requests/tests/
- Authentication & RBAC: apps/authentication/tests/

[ Tier 4: Frontend & Cloud Smoke Verification ]
- Frontend build: npm --prefix frontend run build (checks _headers, _redirects, zero errors)
- Cloud test smoke: test_d1_cloud_smoke.py against approved test resources
```

#### Detailed Smoke Verification Checklist
1. Worker bridge `/health` endpoint responds HTTP 200 on `assethub-test-worker`.
2. D1 test database executes queries without Postgres fallback.
3. Django backend starts with `USE_D1_DATABASE=True` and passes health check.
4. R2 presigned URL generator produces valid SigV4 signature for 900 seconds.
5. Workers AI category suggester succeeds on `@cf/meta/llama-3.1-8b-instruct-fp8`, or cleanly falls back to heuristic matching with `is_fallback: True` on HTTP 429.
6. Admin trigger prevents deletion of last active admin.

Evidence Rule: Mock tests prove internal branch logic; real cloud evidence requires actual command execution output on authorized test resources.

---

## 4. Suggested Direct Modifications for Master Plan

Coordinator should apply these changes to `docs/superpowers/plans/2026-11-03-cloudflare-d1-implementation.md`:

### 1. In "Tech Stack" (Line 9)
- Replace `@cf/meta/llama-3.1-8b-instruct` with `@cf/meta/llama-3.1-8b-instruct-fp8`.

### 2. In "Bảng Phân Quyền Tệp Tin" (File Ownership Matrix, Lines 124-154)
- Line 144: Change `backend/apps/common/storage_backends.py` from `Create` to `Modify` (Task 5).
- Line 148: Change `frontend/public/_headers` & `_redirects` from `Create` to `Verify/Modify` (Task 6).
- Line 151: Change `backend/assethub_api/settings.py` ownership: Shared by Task 1 (D1 DB engine config) and Task 6 (USE_D1_DATABASE switch, CORS regex, ALLOWED_HOSTS).
- Add: `docker-compose.yml` and `.env.example` owned by Task 6.

### 3. In "Task 1" (Line 228)
- Add Step: Define `D1_DATABASE_CONFIG` in `backend/assethub_api/settings.py` while keeping `USE_D1_DATABASE=False` until Task 6.

### 4. In "Task 5" (Lines 437-485)
- Header: Update model to `@cf/meta/llama-3.1-8b-instruct-fp8`.
- Files: Change `backend/apps/common/storage_backends.py` to `Modify` (preserve existing 13 tests).
- Step 3: Implement 900s presigned URLs without breaking existing callers passing explicit `expire_seconds`.
- Step 3: Configure Worker AI with `env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct-fp8'`.
- Note boundary: Local mock testing only; no paid activation, no domain/DNS changes.

### 5. In "Task 6" (Lines 489-542)
- Step 3:
  - Add explicit step: Switch `USE_D1_DATABASE=True` in runtime environment.
  - Wire `D1_BRIDGE_URL`, `D1_BRIDGE_SECRET_TOKEN`, `R2_*`, and `AI_*` in `docker-compose.yml` and `.env.example`.
  - Tighten CORS regex to `r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$"`.
  - Restrict `ALLOWED_HOSTS` from wildcard `*` to specific hosts.
  - Verify `frontend/public/_headers` and `frontend/public/_redirects` remain intact during `npm run build`.

### 6. In "Tiêu Chí Nghiệm Thu Toàn Diện" (End-of-Plan Gate, Lines 545-555)
- Replace item 2 with the four-tier verification suite:
  1. D1 backend tests: `pytest assethub_api/backends/d1/tests/ -v` (22+ passed).
  2. Storage & AI tests: `pytest tests/test_storage.py tests/test_ai_proxy.py -v` (passed).
  3. Core business & RBAC tests on D1: `pytest apps/assets/tests/ apps/requests/tests/ apps/authentication/tests/ -v`.
  4. Frontend SPA build: `npm --prefix frontend run build` (dist/ generated with _headers and _redirects, exit 0).
  5. E2E Cloud smoke: `pytest tests/e2e/test_d1_cloud_smoke.py -v` against approved test resources.
