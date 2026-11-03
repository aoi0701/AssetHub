-- Official Cloudflare D1 Production Schema for AssetHub (19 Tables, Constraints & Protection Triggers)
-- Safe & Idempotent: No DROP statements. Re-entrant execution preserves all existing data.

-- 1. Departments (matches apps.departments.models.Department)
CREATE TABLE IF NOT EXISTS departments_department (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- 2. Categories (matches apps.departments.models.Category)
CREATE TABLE IF NOT EXISTS departments_category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- 3. Django Content Types (required by Django ORM internals)
CREATE TABLE IF NOT EXISTS django_content_type (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_label TEXT NOT NULL,
    model TEXT NOT NULL,
    CONSTRAINT django_content_type_app_label_model_uniq UNIQUE (app_label, model)
);

-- 4. Auth Permissions (matches django.contrib.auth)
CREATE TABLE IF NOT EXISTS auth_permission (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content_type_id INTEGER NOT NULL REFERENCES django_content_type(id),
    codename TEXT NOT NULL,
    CONSTRAINT unique_content_type_codename UNIQUE (content_type_id, codename)
);

-- 5. Auth Groups (matches django.contrib.auth)
CREATE TABLE IF NOT EXISTS auth_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- 6. Auth Group Permissions
CREATE TABLE IF NOT EXISTS auth_group_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES auth_group(id),
    permission_id INTEGER NOT NULL REFERENCES auth_permission(id),
    CONSTRAINT unique_group_permission UNIQUE (group_id, permission_id)
);

-- 7. Users (matches apps.authentication.models.User with AbstractUser parity)
CREATE TABLE IF NOT EXISTS authentication_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    password TEXT NOT NULL,
    last_login TEXT,
    is_superuser INTEGER NOT NULL DEFAULT 0 CHECK(is_superuser IN (0, 1)),
    username TEXT UNIQUE NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT UNIQUE NOT NULL,
    is_staff INTEGER NOT NULL DEFAULT 0 CHECK(is_staff IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    date_joined TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MANAGER', 'USER')),
    full_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    department_id INTEGER REFERENCES departments_department(id),
    auth_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- 8. Authentication User Groups
CREATE TABLE IF NOT EXISTS authentication_user_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    group_id INTEGER NOT NULL REFERENCES auth_group(id),
    CONSTRAINT unique_user_group UNIQUE (user_id, group_id)
);

-- 9. Authentication User Permissions
CREATE TABLE IF NOT EXISTS authentication_user_user_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    permission_id INTEGER NOT NULL REFERENCES auth_permission(id),
    CONSTRAINT unique_user_permission UNIQUE (user_id, permission_id)
);

-- 10. Django Admin Log (required by Django ORM user cascade)
CREATE TABLE IF NOT EXISTS django_admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_time TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    user_id INTEGER NOT NULL REFERENCES authentication_user(id),
    content_type_id INTEGER REFERENCES django_content_type(id),
    object_id TEXT,
    object_repr TEXT NOT NULL DEFAULT '',
    action_flag INTEGER NOT NULL,
    change_message TEXT NOT NULL DEFAULT ''
);

-- 11. Django Migrations tracking
CREATE TABLE IF NOT EXISTS django_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    name TEXT NOT NULL,
    applied TEXT NOT NULL
);

-- 12. Django Session storage
CREATE TABLE IF NOT EXISTS django_session (
    session_key TEXT PRIMARY KEY,
    session_data TEXT NOT NULL,
    expire_date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS django_session_expire_date_idx ON django_session(expire_date);

-- 13. Identifier Sequences (matches apps.assets.models.IdentifierSequence)
CREATE TABLE IF NOT EXISTS assets_identifiersequence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('AST', 'REQ')),
    year INTEGER NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT unique_identifier_sequence_kind_year UNIQUE (kind, year)
);

-- 14. Assets (matches apps.assets.models.Asset with 6 strict statuses and holder checks)
CREATE TABLE IF NOT EXISTS assets_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES departments_category(id),
    serial_number TEXT NOT NULL DEFAULT '',
    specification TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    department_id INTEGER REFERENCES departments_department(id),
    assigned_to_user_id INTEGER REFERENCES authentication_user(id),
    image TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL DEFAULT '',
    purchase_date TEXT,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    CONSTRAINT chk_asset_status CHECK(status IN ('AVAILABLE', 'IN_USE', 'DAMAGED_PENDING_REPAIR', 'UNDER_MAINTENANCE', 'PENDING_DISPOSAL', 'DISPOSED')),
    CONSTRAINT chk_asset_available_no_holder CHECK((status = 'AVAILABLE' AND assigned_to_user_id IS NULL) OR (status != 'AVAILABLE')),
    CONSTRAINT chk_asset_no_holder_when_available_or_disposal CHECK(NOT (status IN ('AVAILABLE', 'PENDING_DISPOSAL', 'DISPOSED') AND assigned_to_user_id IS NOT NULL)),
    CONSTRAINT chk_asset_no_holder_when_unallocated CHECK(NOT (department_id IS NULL AND assigned_to_user_id IS NOT NULL))
);

-- 15. Requests (matches apps.requests.models.AssetRequest)
CREATE TABLE IF NOT EXISTS requests_assetrequest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_code TEXT UNIQUE NOT NULL,
    request_type TEXT NOT NULL CHECK(request_type IN ('ALLOCATE', 'RETURN', 'DAMAGE_REPORT')),
    requester_id INTEGER NOT NULL REFERENCES authentication_user(id),
    asset_id INTEGER REFERENCES assets_asset(id),
    requested_category_id INTEGER REFERENCES departments_category(id),
    department_id INTEGER NOT NULL REFERENCES departments_department(id),
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED')),
    reviewed_by_id INTEGER REFERENCES authentication_user(id),
    reviewed_at TEXT,
    rejection_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_pending_return_or_damage_per_user_asset 
ON requests_assetrequest(requester_id, request_type, asset_id) 
WHERE status = 'PENDING' AND request_type IN ('RETURN', 'DAMAGE_REPORT') AND asset_id IS NOT NULL;

-- 16. Asset History Audit Trail (matches apps.assets.models.AssetHistoryLog)
CREATE TABLE IF NOT EXISTS assets_assethistorylog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets_asset(id),
    actor_id INTEGER NOT NULL REFERENCES authentication_user(id),
    action_type TEXT NOT NULL CHECK(action_type IN ('CREATE', 'ALLOCATE', 'RETURN', 'DAMAGE', 'MAINTENANCE_START', 'MAINTENANCE_END', 'TRANSFER', 'DISPOSAL_PROPOSE', 'DISPOSAL_CONFIRM')),
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    from_user_id INTEGER REFERENCES authentication_user(id),
    to_user_id INTEGER REFERENCES authentication_user(id),
    from_department_id INTEGER REFERENCES departments_department(id),
    to_department_id INTEGER REFERENCES departments_department(id),
    reason TEXT NOT NULL DEFAULT '',
    asset_code_snapshot TEXT NOT NULL,
    asset_name_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- 17. Assertion guard table for batch atomicity
CREATE TABLE IF NOT EXISTS _assert_guard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rows_affected INTEGER NOT NULL CHECK(rows_affected > 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 18. Idempotency table for network retry safety
CREATE TABLE IF NOT EXISTS _idempotency_log (
    idempotency_key TEXT PRIMARY KEY,
    payload_hash TEXT,
    request_hash TEXT,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    response_body TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 19. AI Suggestion Audit Logs for Workers AI & Django
CREATE TABLE IF NOT EXISTS ai_suggestion_logs (
    id TEXT PRIMARY KEY,
    asset_name TEXT NOT NULL,
    description TEXT NOT NULL,
    suggested_category_id INTEGER,
    suggested_category_name TEXT,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Trigger: Protect Last Active Admin from being de-activated or demoted
CREATE TRIGGER IF NOT EXISTS protect_last_admin_before_update
BEFORE UPDATE OF role, is_active ON authentication_user
FOR EACH ROW
WHEN OLD.role = 'ADMIN' AND OLD.is_active = 1
     AND (NEW.role != 'ADMIN' OR NEW.is_active != 1)
BEGIN
    SELECT RAISE(ABORT, 'Cannot demote or deactivate the last active Admin')
    WHERE (
        SELECT COUNT(*) FROM authentication_user 
        WHERE role = 'ADMIN' AND is_active = 1 AND id != OLD.id
    ) = 0;
END;

-- Trigger: Protect Last Active Admin from being deleted
CREATE TRIGGER IF NOT EXISTS protect_last_admin_before_delete
BEFORE DELETE ON authentication_user
FOR EACH ROW
WHEN OLD.role = 'ADMIN' AND OLD.is_active = 1
BEGIN
    SELECT RAISE(ABORT, 'Cannot delete the last active Admin')
    WHERE (
        SELECT COUNT(*) FROM authentication_user 
        WHERE role = 'ADMIN' AND is_active = 1 AND id != OLD.id
    ) = 0;
END;
