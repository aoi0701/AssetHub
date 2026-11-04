import importlib
import os
import re
from unittest.mock import patch
import pytest
from django.core.exceptions import ImproperlyConfigured
import assethub_api.settings as settings


@pytest.fixture(autouse=True)
def restore_settings():
    """Ensure settings are restored to clean PostgreSQL default after each test."""
    yield
    for env_var in [
        'USE_D1_DATABASE',
        'D1_DATABASE_NAME',
        'D1_BRIDGE_URL',
        'D1_BRIDGE_SECRET_TOKEN',
        'D1_TIMEOUT',
        'POSTGRES_DB',
        'POSTGRES_RO_USER',
        'POSTGRES_RO_PASSWORD',
        'POSTGRES_HOST',
        'POSTGRES_PORT',
        'DJANGO_ALLOWED_HOSTS',
        'CORS_ALLOWED_ORIGINS',
        'CORS_EXTRA_REGEX',
    ]:
        os.environ.pop(env_var, None)
    try:
        importlib.reload(settings)
    except Exception:
        pass


def test_use_d1_database_false_sets_postgresql():
    """USE_D1_DATABASE=False keeps PostgreSQL for default database."""
    with patch.dict(os.environ, {'USE_D1_DATABASE': 'False'}, clear=False):
        importlib.reload(settings)
        assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'
        assert 'postgres' not in settings.DATABASES


def test_use_d1_database_default_is_postgresql():
    """Default without USE_D1_DATABASE env var is PostgreSQL."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('USE_D1_DATABASE', None)
        importlib.reload(settings)
        assert settings.USE_D1_DATABASE is False
        assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'


def test_use_d1_database_true_sets_d1_engine():
    """USE_D1_DATABASE=True configures D1 engine and read-only PostgreSQL source."""
    env = {
        'USE_D1_DATABASE': 'true',
        'D1_BRIDGE_URL': 'https://assethub-bridge.workers.dev',
        'D1_BRIDGE_SECRET_TOKEN': 'super-secret-token',
        'D1_DATABASE_NAME': 'custom-d1-db',
        'D1_TIMEOUT': '60.0',
        'POSTGRES_DB': 'assethub_pg',
        'POSTGRES_RO_USER': 'pg_ro_user',
        'POSTGRES_RO_PASSWORD': 'pg_ro_password',
        'POSTGRES_HOST': 'postgres_host',
        'POSTGRES_PORT': '5433',
    }
    with patch.dict(os.environ, env, clear=False):
        importlib.reload(settings)
        assert settings.USE_D1_DATABASE is True
        default_db = settings.DATABASES['default']
        assert default_db['ENGINE'] == 'assethub_api.backends.d1'
        assert default_db['NAME'] == 'custom-d1-db'
        assert default_db['BRIDGE_URL'] == 'https://assethub-bridge.workers.dev'
        assert default_db['BRIDGE_TOKEN'] == 'super-secret-token'
        assert default_db['TIMEOUT'] == 60.0
        assert default_db['TIME_ZONE'] is None
        assert default_db['CONN_MAX_AGE'] == 0
        assert default_db['CONN_HEALTH_CHECKS'] is False
        assert default_db['AUTOCOMMIT'] is True
        assert default_db['ATOMIC_REQUESTS'] is False

        # Read-only postgres source
        assert 'postgres' in settings.DATABASES
        pg_db = settings.DATABASES['postgres']
        assert pg_db['ENGINE'] == 'django.db.backends.postgresql'
        assert pg_db['NAME'] == 'assethub_pg'
        assert pg_db['USER'] == 'pg_ro_user'
        assert pg_db['PASSWORD'] == 'pg_ro_password'
        assert pg_db['HOST'] == 'postgres_host'
        assert pg_db['PORT'] == '5433'


@pytest.mark.parametrize('missing_env', [
    {},
    {'D1_BRIDGE_URL': 'https://bridge.workers.dev'},
    {'D1_BRIDGE_SECRET_TOKEN': 'secret'},
    {'D1_BRIDGE_URL': '', 'D1_BRIDGE_SECRET_TOKEN': ''},
    {'D1_BRIDGE_URL': 'https://bridge.workers.dev', 'D1_BRIDGE_SECRET_TOKEN': ''},
    {'D1_BRIDGE_URL': '', 'D1_BRIDGE_SECRET_TOKEN': 'secret'},
])
def test_use_d1_database_true_without_credentials_raises_improperly_configured(missing_env):
    """Missing or empty bridge URL / secret token raises ImproperlyConfigured."""
    with patch.dict(os.environ, {'USE_D1_DATABASE': 'true', **missing_env}, clear=False):
        if 'D1_BRIDGE_URL' not in missing_env:
            os.environ.pop('D1_BRIDGE_URL', None)
        if 'D1_BRIDGE_SECRET_TOKEN' not in missing_env:
            os.environ.pop('D1_BRIDGE_SECRET_TOKEN', None)

        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.reload(settings)

        assert 'D1_BRIDGE_URL and D1_BRIDGE_SECRET_TOKEN must be set when USE_D1_DATABASE=True' in str(exc_info.value)


def test_allowed_hosts_default_does_not_contain_wildcard():
    """Default ALLOWED_HOSTS contains specific hosts, never wildcard '*'."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('DJANGO_ALLOWED_HOSTS', None)
        importlib.reload(settings)
        assert '*' not in settings.ALLOWED_HOSTS
        assert settings.ALLOWED_HOSTS == ['localhost', '127.0.0.1', 'backend']


def test_allowed_hosts_custom_env():
    """DJANGO_ALLOWED_HOSTS overrides default ALLOWED_HOSTS."""
    with patch.dict(os.environ, {'DJANGO_ALLOWED_HOSTS': 'api.example.com, test.assethub.io'}, clear=False):
        importlib.reload(settings)
        assert settings.ALLOWED_HOSTS == ['api.example.com', 'test.assethub.io']
        assert '*' not in settings.ALLOWED_HOSTS


def test_cors_regex_matches_and_rejects():
    """CORS regex matches assethub.pages.dev and subdomains, rejects evil origins."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('CORS_EXTRA_REGEX', None)
        importlib.reload(settings)

        pattern = settings.CORS_ALLOWED_ORIGIN_REGEXES[0]

        # Valid origins: exact root, subdomains, and project suffixes
        assert re.match(pattern, 'https://assethub.pages.dev') is not None
        assert re.match(pattern, 'https://assethub-demo.pages.dev') is not None
        assert re.match(pattern, 'https://feat-demo.assethub.pages.dev') is not None
        assert re.match(pattern, 'https://pr-42.assethub.pages.dev') is not None

        # Rejected origins: evil or unmatching
        assert re.match(pattern, 'https://evil.pages.dev') is None
        assert re.match(pattern, 'https://evil-assethub.pages.dev') is None
        assert re.match(pattern, 'https://other-domain.com') is None
        assert re.match(pattern, 'http://assethub.pages.dev') is None
        assert re.match(pattern, 'https://assethub.pages.dev.attacker.com') is None


def test_cors_extra_regex_and_credentials():
    """CORS_EXTRA_REGEX adds extra pattern and CORS_ALLOW_CREDENTIALS is False."""
    extra_pattern = r'^https:\/\/[a-z0-9-]+\.trycloudflare\.com$'
    with patch.dict(os.environ, {'CORS_EXTRA_REGEX': extra_pattern}, clear=False):
        importlib.reload(settings)
        assert extra_pattern in settings.CORS_ALLOWED_ORIGIN_REGEXES
        assert settings.CORS_ALLOW_CREDENTIALS is False


def test_cors_allowed_origins_parsing():
    """CORS_ALLOWED_ORIGINS parses comma-separated origins."""
    origins = 'http://localhost:3000,https://app.example.com'
    with patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': origins}, clear=False):
        importlib.reload(settings)
        assert settings.CORS_ALLOWED_ORIGINS == ['http://localhost:3000', 'https://app.example.com']
