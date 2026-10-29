# AssetHub - Asset Management System

AssetHub is a physical asset management platform for organizations, tracking hardware, monitors, printers, and office equipment through lifecycles: allocation, return, maintenance, transfer, and disposal.

## Tech Stack

- **Backend:** Python 3.12, Django 5.2, Django REST Framework, SimpleJWT, drf-spectacular, psycopg3, Gunicorn
- **Database:** PostgreSQL 16
- **Frontend (Upcoming):** React, Vite, Tailwind CSS, Recharts
- **Containerization:** Docker Compose
- **Testing:** pytest, pytest-django

## Quickstart (Development)

### 1. Environment configuration
```bash
cp .env.example .env
```

### 2. Start services with Docker Compose
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d db backend
```

### 3. Run database migrations
```bash
docker compose exec -T backend python manage.py migrate --noinput
```

### 4. Verify system check
```bash
docker compose exec -T backend python manage.py check
```

### 5. Run automated test suite
```bash
docker compose exec -T backend pytest -q
```

## API Endpoints

- **Health Check:** `GET /api/health/` (Public)
- **API Schema (OpenAPI):** `GET /api/schema/` (Public)
- **Swagger Documentation:** `GET /api/docs/` (Public)

## Specifications & Documentation

- System Design: `docs/superpowers/specs/2026-10-29-assethub-design.md`
- Implementation Plan: `docs/superpowers/plans/2026-10-29-assethub.md`
- Verification Report: `docs/verification-report.md`
