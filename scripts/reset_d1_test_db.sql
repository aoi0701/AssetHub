-- D1 Testbed Reset Script: Drops all triggers and tables for clean test initialization
-- DANGER: ONLY RUN ON TESTBED DATABASE (assethub-test-d1)

DROP TRIGGER IF EXISTS protect_last_admin_before_update;
DROP TRIGGER IF EXISTS protect_last_admin_before_delete;
DROP TABLE IF EXISTS ai_suggestion_logs;
DROP TABLE IF EXISTS _idempotency_log;
DROP TABLE IF EXISTS _assert_guard;
DROP TABLE IF EXISTS assets_assethistorylog;
DROP TABLE IF EXISTS requests_assetrequest;
DROP TABLE IF EXISTS assets_asset;
DROP TABLE IF EXISTS assets_identifiersequence;
DROP TABLE IF EXISTS django_session;
DROP TABLE IF EXISTS django_migrations;
DROP TABLE IF EXISTS django_admin_log;
DROP TABLE IF EXISTS authentication_user_user_permissions;
DROP TABLE IF EXISTS authentication_user_groups;
DROP TABLE IF EXISTS auth_group_permissions;
DROP TABLE IF EXISTS authentication_user;
DROP TABLE IF EXISTS auth_group;
DROP TABLE IF EXISTS auth_permission;
DROP TABLE IF EXISTS django_content_type;
DROP TABLE IF EXISTS departments_category;
DROP TABLE IF EXISTS departments_department;
