-- Verified existing D1 schema baseline; timestamps record metadata reconciliation, not historical DDL execution.
-- AssetHub Week 5: Idempotent D1 Migration Metadata Baseline Synchronization Plan
-- Purpose: Synchronize django_migrations metadata without modifying any schema or existing data.
-- Safety: Pure INSERT with WHERE NOT EXISTS. Re-entrant, non-destructive, zero DDL, zero secrets.
-- Total migrations: 25 (topologically ordered).

--  1. contenttypes.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'contenttypes', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'contenttypes' AND name = '0001_initial'
);

--  2. departments.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'departments', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'departments' AND name = '0001_initial'
);

--  3. contenttypes.0002_remove_content_type_name
INSERT INTO django_migrations (app, name, applied)
SELECT 'contenttypes', '0002_remove_content_type_name', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'contenttypes' AND name = '0002_remove_content_type_name'
);

--  4. auth.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0001_initial'
);

--  5. auth.0002_alter_permission_name_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0002_alter_permission_name_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0002_alter_permission_name_max_length'
);

--  6. auth.0003_alter_user_email_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0003_alter_user_email_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0003_alter_user_email_max_length'
);

--  7. auth.0004_alter_user_username_opts
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0004_alter_user_username_opts', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0004_alter_user_username_opts'
);

--  8. auth.0005_alter_user_last_login_null
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0005_alter_user_last_login_null', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0005_alter_user_last_login_null'
);

--  9. auth.0006_require_contenttypes_0002
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0006_require_contenttypes_0002', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0006_require_contenttypes_0002'
);

-- 10. auth.0007_alter_validators_add_error_messages
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0007_alter_validators_add_error_messages', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0007_alter_validators_add_error_messages'
);

-- 11. auth.0008_alter_user_username_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0008_alter_user_username_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0008_alter_user_username_max_length'
);

-- 12. auth.0009_alter_user_last_name_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0009_alter_user_last_name_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0009_alter_user_last_name_max_length'
);

-- 13. auth.0010_alter_group_name_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0010_alter_group_name_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0010_alter_group_name_max_length'
);

-- 14. auth.0011_update_proxy_permissions
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0011_update_proxy_permissions', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0011_update_proxy_permissions'
);

-- 15. auth.0012_alter_user_first_name_max_length
INSERT INTO django_migrations (app, name, applied)
SELECT 'auth', '0012_alter_user_first_name_max_length', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'auth' AND name = '0012_alter_user_first_name_max_length'
);

-- 16. authentication.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'authentication', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'authentication' AND name = '0001_initial'
);

-- 17. admin.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'admin', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'admin' AND name = '0001_initial'
);

-- 18. admin.0002_logentry_remove_auto_add
INSERT INTO django_migrations (app, name, applied)
SELECT 'admin', '0002_logentry_remove_auto_add', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'admin' AND name = '0002_logentry_remove_auto_add'
);

-- 19. admin.0003_logentry_add_action_flag_choices
INSERT INTO django_migrations (app, name, applied)
SELECT 'admin', '0003_logentry_add_action_flag_choices', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'admin' AND name = '0003_logentry_add_action_flag_choices'
);

-- 20. departments.0002_category
INSERT INTO django_migrations (app, name, applied)
SELECT 'departments', '0002_category', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'departments' AND name = '0002_category'
);

-- 21. assets.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'assets', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'assets' AND name = '0001_initial'
);

-- 22. assistant.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'assistant', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'assistant' AND name = '0001_initial'
);

-- 23. authentication.0002_alter_user_email
INSERT INTO django_migrations (app, name, applied)
SELECT 'authentication', '0002_alter_user_email', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'authentication' AND name = '0002_alter_user_email'
);

-- 24. requests.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'requests', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'requests' AND name = '0001_initial'
);

-- 25. sessions.0001_initial
INSERT INTO django_migrations (app, name, applied)
SELECT 'sessions', '0001_initial', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM django_migrations WHERE app = 'sessions' AND name = '0001_initial'
);
