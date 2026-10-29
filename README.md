# AssetHub - Enterprise Physical Asset Management Platform

[![CI/CD Verification](https://img.shields.io/badge/Verification-100%25%20Passed-brightgreen.svg)]()
[![Backend Tests](https://img.shields.io/badge/pytest-171%20passed-blue.svg)]()
[![Frontend Tests](https://img.shields.io/badge/vitest-35%20passed-blue.svg)]()
[![E2E Tests](https://img.shields.io/badge/Playwright-Passed-success.svg)]()
[![Postman Tests](https://img.shields.io/badge/Newman-86%2F86%20passed-orange.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

**AssetHub** is a production-ready, full-stack enterprise physical asset management system designed to track IT hardware, monitors, laptops, office equipment, and peripherals through their complete lifecycle: acquisition, department allocation, employee custody, damage reporting, maintenance, transfer, and disposal.

Built with a **Hybrid Edge-Core Architecture** combining Cloudflare's serverless edge infrastructure with a containerized Django & PostgreSQL backend core.

---

## 🏗 Architecture Overview

```
                                [ Cloudflare Edge Network ]
                                              │
         ┌────────────────────────────────────┼────────────────────────────────────┐
         │                                    │                                    │
         ▼                                    ▼                                    ▼
[ Cloudflare Pages ]                [ Cloudflare Worker ]                 [ Cloudflare R2 ]
React 18 + Vite SPA                  AI Microservice Proxy                 Private Object Storage
(Zero Token Storage in localStorage) (Workers AI Llama 3.3 + D1 Logs)      (HMAC Presigned URLs, 60s TTL)
         │                                    │                                    │
         └──────────────────────────┬─────────┴────────────────────────────────────┘
                                    │ Cloudflare Tunnel / HTTPS
                                    ▼
                      [ Docker Compose Core Services ]
                                    │
                   ┌────────────────┴────────────────┐
                   ▼                                 ▼
      [ Django 5.2 + DRF Core ]             [ PostgreSQL 16 DB ]
     - Custom Stateless JWT Auth            - Relational schema & constraints
     - auth_version instant revocation      - Concurrency safe (select_for_update)
     - Scoped Rate Throttling               - Monotonic sequence generation
     - Pillow/pypdf magic bytes validator   - ACID compliant transactions
```

### Key Architectural Highlights
- **Stateless In-Memory Token Security**: Access and refresh tokens are strictly maintained in browser application memory (`AuthContext`) and never stored in `localStorage` or `sessionStorage`, mitigating token theft via XSS.
- **Immediate Session Revocation**: Every user record contains an `auth_version` integer. Logging out, changing passwords, or administrator privilege adjustments immediately increment `auth_version`, rendering existing tokens unusable across all sessions within sub-seconds.
- **Strict Role-Based Department Scoping**:
  - `ADMIN`: System-wide governance, department & category management, user activation/role management, reports, and asset lifecycle control.
  - `MANAGER`: Departmental asset management, approving allocation/return/damage requests for own department, read-only employee roster.
  - `USER`: Self-service portal for requesting equipment, viewing held assets, submitting returns/damage reports, and tracking personal history.
- **Defense in Depth**: Real magic-byte validation (Pillow & pypdf), strict parameterized ORM queries, rate-limited public endpoints, and comprehensive Content Security Policy (CSP).

---

## 📋 Prerequisites

Before running AssetHub, ensure the following tools are installed:

- **Docker & Docker Compose**: v24.0+ / Compose v2.20+
- **Node.js**: v22.x LTS or higher (with `npm` v10+)
- **Python**: v3.12+ (optional for local running outside Docker)
- **Git**

---

## 🚀 Quick Start Guide

### 1. Clone & Configure Environment
```bash
git clone https://github.com/your-org/assethub.git
cd AssetHub

# Copy sample environment configuration
cp .env.example .env
```

### 2. Start Services with Docker Compose
Start PostgreSQL and the Django backend container:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d db backend
```

Check health status:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
```

### 3. Apply Database Migrations
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py migrate --noinput
```

### 4. Seed Demonstration Data
Seed default departments, categories, demonstration users, and realistic asset lifecycles:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T \
  -e ALLOW_DEMO_SEED=true \
  -e DEMO_SEED_PASSWORD=DemoPassword123! \
  backend python manage.py seed_data --demo
```

### 5. Launch Frontend Development Server
```bash
npm --prefix frontend install
npm --prefix frontend run dev
```
The React frontend is accessible at `http://127.0.0.1:5173`.

---

## 👤 Default Demonstration Accounts

All demonstration accounts are pre-configured with the default seed password (`DemoPassword123!`):

| Role | Username | Password | Access Scope |
|---|---|---|---|
| **System Admin** | `admin` | `DemoPassword123!` | Full system administration, user management, global reports |
| **IT Manager** | `manager_it` | `DemoPassword123!` | IT Department asset inventory & request approval inbox |
| **HR Manager** | `manager_hr` | `DemoPassword123!` | HR Department asset inventory & request approval inbox |
| **IT Staff (User)** | `user_it_1` | `DemoPassword123!` | Self-service equipment portal & personal asset history |
| **HR Staff (User)** | `user_hr_1` | `DemoPassword123!` | Self-service equipment portal & personal asset history |

*(Note: In automated seed tests, users can also be customized with any secure passphrase via the `DEMO_SEED_PASSWORD` environment variable).*

---

## 🧪 Running Automated Tests

AssetHub is backed by a 100% passing test matrix spanning unit, integration, microservice, API, and end-to-end tests:

### 1. Backend Core Test Suite (pytest)
Runs 171 automated unit and integration tests with PostgreSQL:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```

### 2. Migration & OpenAPI Schema Validation
Verifies no missing migrations and checks OpenAPI 3.0 schema strictly:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py spectacular --file docs/openapi-schema.yml --validate --fail-on-warn
```

### 3. Frontend Unit & Integration Tests (Vitest)
Runs 35 tests covering all components, auth flows, and MSW API mocks:
```bash
npm --prefix frontend test
```

### 4. Production Frontend Build
Verifies bundling and tree-shaking:
```bash
npm --prefix frontend run build
```

### 5. Cloudflare Worker AI Tests (Vitest)
Validates worker routing, token authorization, D1 logging, and error handling:
```bash
npm --prefix cloudflare-worker test
```

### 6. Postman / Newman API Collection Run
Runs 48 requests and 86 assertions covering full lifecycle against live backend:
```bash
npm --prefix tools/postman run test:api
```

### 7. Playwright E2E End-to-End Tests
Executes realistic user journey in headless Chromium against live services:
```bash
npm --prefix frontend run test:e2e
```

---

## 📡 API Endpoints Summary

| Endpoint | Method | Role | Description |
|---|:---:|:---:|---|
| `/api/health/` | `GET` | Public | System & PostgreSQL connectivity health check |
| `/api/schema/` | `GET` | Public | OpenAPI 3.0 YAML/JSON specification |
| `/api/docs/` | `GET` | Public | Interactive Swagger UI API documentation |
| `/api/auth/register/` | `POST` | Public | Account registration (rate-limited, pending activation) |
| `/api/auth/login/` | `POST` | Public | JWT login returning access & refresh tokens |
| `/api/auth/logout/` | `POST` | Auth | Revokes all active sessions across all devices |
| `/api/auth/profile/` | `GET/PATCH` | Auth | Current authenticated user profile |
| `/api/auth/change-password/` | `POST` | Auth | Change password and invalidate older sessions |
| `/api/users/` | `GET/POST` | Admin/Manager | User roster management (scoping enforced) |
| `/api/departments/` | `GET/POST/PATCH` | Admin | Department management (no hard delete) |
| `/api/categories/` | `GET/POST/PATCH` | Admin | Asset category management |
| `/api/assets/` | `GET/POST/PATCH` | Scoped | Asset inventory (filter by category, dept, status) |
| `/api/assets/{id}/allocate/` | `POST` | Admin/Manager | Allocate asset to employee |
| `/api/assets/{id}/revoke/` | `POST` | Admin/Manager | Revoke asset back to available status |
| `/api/assets/{id}/history/` | `GET` | Admin/Manager | Full historical lifecycle audit log |
| `/api/requests/` | `GET/POST` | Scoped | Asset request workflow (allocate, return, damage) |
| `/api/requests/{id}/approve/` | `POST` | Admin/Manager | Approve request with stock assignment |
| `/api/requests/{id}/reject/` | `POST` | Admin/Manager | Reject request with reason |
| `/api/me/asset-history/` | `GET` | User | Personal sanitized asset event history |
| `/api/reports/kpis/` | `GET` | Admin/Manager | Inventory KPI counts and distributions |
| `/api/reports/charts/` | `GET` | Admin/Manager | Monthly status distribution and category breakdown |
| `/api/ai/suggest-category/` | `POST` | Admin/Manager | AI category classification microservice proxy |

---

## 📚 Project Documentation

- [Deployment Guide (Cloudflare Pages, Worker, R2, Docker)](file:///home/truong/Downloads/AssetHub/docs/deployment-guide.md): Complete setup, Cloudflare Tunnel configuration, DNS, and production zero-cost guidelines.
- [Security Testing & Practice Report](file:///home/truong/Downloads/AssetHub/docs/security-check.md): 10 OWASP attack vectors, Caido / ZAP / Burp testing methodologies, and defensive mechanisms.
- [Comprehensive Verification Report](file:///home/truong/Downloads/AssetHub/docs/verification-report.md): Full acceptance audit log across all 13 tasks and verification commands.
- [System Design Specification](file:///home/truong/Downloads/AssetHub/docs/superpowers/specs/2026-10-29-assethub-design.md): Deep architectural specification and business rules.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
