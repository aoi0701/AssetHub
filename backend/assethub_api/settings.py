"""
Django settings for assethub_api project.
"""

from pathlib import Path
import os
from datetime import timedelta
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present
load_dotenv(BASE_DIR.parent / '.env')
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-assethub-dev-bootstrap-key-do-not-use-in-prod-32chars!'
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

allowed_hosts_env = os.environ.get('DJANGO_ALLOWED_HOSTS')
if allowed_hosts_env:
    ALLOWED_HOSTS = [
        host.strip()
        for host in allowed_hosts_env.split(',')
        if host.strip()
    ]
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'backend']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',
    'corsheaders',
    # Local apps
    'apps.common.apps.CommonConfig',
    'apps.departments.apps.DepartmentsConfig',
    'apps.authentication.apps.AuthenticationConfig',
    'apps.assets.apps.AssetsConfig',
    'apps.requests.apps.RequestsConfig',
    'apps.reports.apps.ReportsConfig',
    'apps.ai.apps.AiConfig',
    'apps.assistant.apps.AssistantConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'assethub_api.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'assethub_api.wsgi.application'

# Custom User Model
AUTH_USER_MODEL = 'authentication.User'

# Database configuration & runtime switching (PostgreSQL vs Cloudflare D1)
USE_D1_DATABASE = os.environ.get('USE_D1_DATABASE', 'False').lower() in ('true', '1', 'yes')

if USE_D1_DATABASE:
    d1_bridge_url = os.environ.get('D1_BRIDGE_URL')
    d1_bridge_token = os.environ.get('D1_BRIDGE_SECRET_TOKEN')
    if not d1_bridge_url or not d1_bridge_token:
        raise ImproperlyConfigured(
            "D1_BRIDGE_URL and D1_BRIDGE_SECRET_TOKEN must be set when USE_D1_DATABASE=True"
        )
    DATABASES = {
        'default': {
            'ENGINE': 'assethub_api.backends.d1',
            'NAME': os.environ.get('D1_DATABASE_NAME', 'assethub-test-d1'),
            'BRIDGE_URL': d1_bridge_url,
            'BRIDGE_TOKEN': d1_bridge_token,
            'TIMEOUT': float(os.environ.get('D1_TIMEOUT', '45.0')),
            'TIME_ZONE': None,
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': False,
            'OPTIONS': {},
            'AUTOCOMMIT': True,
            'ATOMIC_REQUESTS': False,
        },
    }
    postgres_ro_user = os.environ.get('POSTGRES_RO_USER')
    postgres_ro_password = os.environ.get('POSTGRES_RO_PASSWORD')
    if postgres_ro_user and postgres_ro_password:
        DATABASES['postgres'] = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', os.environ.get('DB_NAME', 'assethub')),
            'USER': postgres_ro_user,
            'PASSWORD': postgres_ro_password,
            'HOST': os.environ.get('POSTGRES_HOST', os.environ.get('DB_HOST', 'db')),
            'PORT': os.environ.get('POSTGRES_PORT', os.environ.get('DB_PORT', '5432')),
            'OPTIONS': {
                'options': '-c default_transaction_read_only=on',
            },
        }
else:
    DATABASES = {
        'default': {
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
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Cloudflare R2 / S3 Storage Credentials
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
R2_PRESIGNED_URL_EXPIRE_SECONDS = int(os.environ.get('R2_PRESIGNED_URL_EXPIRE_SECONDS', '900'))

# Cloudflare Worker AI microservice configuration
AI_SERVICE_TOKEN = os.environ.get('AI_SERVICE_TOKEN', 'dev-ai-secret-token-2026')
AI_WORKER_URL = os.environ.get('AI_WORKER_URL', 'http://127.0.0.1:8787')
AI_MODEL = os.environ.get('AI_MODEL', '@cf/meta/llama-3.1-8b-instruct-fp8')
ASSISTANT_AI_TIMEOUT_SECONDS = int(os.environ.get('ASSISTANT_AI_TIMEOUT_SECONDS', '30'))
ASSISTANT_ENABLED = os.environ.get('ASSISTANT_ENABLED', 'False').lower() in ('true', '1')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cache configuration
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': '/tmp/django_cache',
    }
}

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'apps.authentication.authentication.CustomJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '1000/minute',
        'auth_login': os.environ.get('AUTH_LOGIN_THROTTLE_RATE', '10/minute'),
        'auth_register': os.environ.get('AUTH_REGISTER_THROTTLE_RATE', '10/minute'),
        'assistant': '10/minute',
    },
}

# SimpleJWT configuration
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=10),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
}

# OpenAPI Spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'AssetHub API',
    'DESCRIPTION': 'AssetHub physical asset management REST API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# Proxy SSL header when behind reverse proxy / Cloudflare Tunnel
secure_proxy_ssl_header_env = os.environ.get('SECURE_PROXY_SSL_HEADER')
if secure_proxy_ssl_header_env:
    parts = [p.strip() for p in secure_proxy_ssl_header_env.split(',')]
    if len(parts) == 2:
        SECURE_PROXY_SSL_HEADER = (parts[0], parts[1])

# CORS settings
cors_origins_env = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:5173,http://127.0.0.1:5173'
)
CORS_ALLOWED_ORIGINS = [
    origin.strip() for origin in cors_origins_env.split(',') if origin.strip()
]
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$",
]
cors_extra_regex = os.environ.get('CORS_EXTRA_REGEX')
if cors_extra_regex:
    CORS_ALLOWED_ORIGIN_REGEXES.append(cors_extra_regex.strip())
CORS_ALLOW_CREDENTIALS = False

