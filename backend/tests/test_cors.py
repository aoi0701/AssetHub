from pathlib import Path
import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from corsheaders.middleware import CorsMiddleware
from rest_framework import status


@pytest.fixture
def cors_handler():
    """Returns a callable that passes a RequestFactory request through CorsMiddleware."""
    rf = RequestFactory()
    mw = CorsMiddleware(lambda req: HttpResponse(b"OK"))

    def _invoke(method, path="/api/health/", origin=None, **headers):
        extra = {}
        if origin:
            extra["HTTP_ORIGIN"] = origin
        extra.update(headers)
        if method.upper() == "OPTIONS":
            request = rf.options(path, **extra)
        elif method.upper() == "POST":
            request = rf.post(path, **extra)
        else:
            request = rf.get(path, **extra)
        return mw(request)

    return _invoke


def test_cors_preflight_allowed_origin(cors_handler):
    """Preflight OPTIONS request from allowed origin returns 200 and Access-Control-Allow-Origin."""
    response = cors_handler(
        "OPTIONS",
        origin="http://localhost:5173",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.get("Access-Control-Allow-Origin") == "http://localhost:5173"


def test_cors_exact_pages_dev_origin_allowed(cors_handler):
    """OPTIONS and GET requests from exact assethub.pages.dev return Access-Control-Allow-Origin."""
    # Preflight OPTIONS
    options_res = cors_handler(
        "OPTIONS",
        origin="https://assethub.pages.dev",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert options_res.status_code == status.HTTP_200_OK
    assert options_res.get("Access-Control-Allow-Origin") == "https://assethub.pages.dev"

    # Simple GET
    get_res = cors_handler("GET", origin="https://assethub.pages.dev")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.get("Access-Control-Allow-Origin") == "https://assethub.pages.dev"


def test_cors_preview_pages_dev_subdomain_allowed(cors_handler):
    """OPTIONS and GET requests from preview subdomain return Access-Control-Allow-Origin."""
    # Preflight OPTIONS
    options_res = cors_handler(
        "OPTIONS",
        origin="https://preview-123.assethub.pages.dev",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert options_res.status_code == status.HTTP_200_OK
    assert options_res.get("Access-Control-Allow-Origin") == "https://preview-123.assethub.pages.dev"

    # Simple GET
    get_res = cors_handler("GET", origin="https://preview-123.assethub.pages.dev")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.get("Access-Control-Allow-Origin") == "https://preview-123.assethub.pages.dev"


def test_cors_attacker_prefix_rejected(cors_handler):
    """Origin with prefix assethub-* (e.g. assethub-attacker.pages.dev) is REJECTED."""
    # Preflight OPTIONS
    options_res = cors_handler(
        "OPTIONS",
        origin="https://assethub-attacker.pages.dev",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert "Access-Control-Allow-Origin" not in options_res

    # Simple GET
    get_res = cors_handler("GET", origin="https://assethub-attacker.pages.dev")
    assert "Access-Control-Allow-Origin" not in get_res


def test_cors_arbitrary_pages_dev_rejected(cors_handler):
    """Arbitrary pages.dev origin (e.g. evil.pages.dev) is REJECTED."""
    # Preflight OPTIONS
    options_res = cors_handler(
        "OPTIONS",
        origin="https://evil.pages.dev",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert "Access-Control-Allow-Origin" not in options_res

    # Simple GET
    get_res = cors_handler("GET", origin="https://evil.pages.dev")
    assert "Access-Control-Allow-Origin" not in get_res


def test_cors_preflight_disallowed_origin(cors_handler):
    """Preflight OPTIONS request from disallowed origin does NOT return Access-Control-Allow-Origin."""
    response = cors_handler(
        "OPTIONS",
        origin="https://malicious-site.com",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
    )
    assert response.get("Access-Control-Allow-Origin") != "https://malicious-site.com"
    assert "Access-Control-Allow-Origin" not in response


def test_cors_allow_credentials_disabled(cors_handler):
    """Access-Control-Allow-Credentials must not be True (V1 constraint)."""
    response = cors_handler(
        "OPTIONS",
        origin="http://localhost:5173",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )
    assert response.status_code == status.HTTP_200_OK
    allow_credentials = response.get("Access-Control-Allow-Credentials")
    assert allow_credentials is None or allow_credentials.lower() == "false"


def test_cors_preflight_allowed_headers(cors_handler):
    """Preflight headers must allow Authorization and Content-Type."""
    response = cors_handler(
        "OPTIONS",
        origin="http://localhost:5173",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization, content-type",
    )
    assert response.status_code == status.HTTP_200_OK
    allow_headers = response.get("Access-Control-Allow-Headers", "").lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers


def test_cors_regression_from_demo_env_example():
    """
    Regression test reading actual deploy/.env.demo.example configuration:
    - Parses CORS_ALLOWED_ORIGINS and CORS_EXTRA_REGEX.
    - Uses RequestFactory and CorsMiddleware with HttpResponse(b'OK') without DB.
    - Verifies:
        * https://assethub.pages.dev -> ALLOWED
        * https://preview-123.assethub.pages.dev -> ALLOWED
        * https://unrelated-project.pages.dev -> REJECTED
        * https://assethub-attacker.pages.dev -> REJECTED
        * https://evil.pages.dev -> REJECTED
    """
    env_path = settings.BASE_DIR.parent / "deploy" / ".env.demo.example"
    if not env_path.exists():
        env_path = Path("/workspace/deploy/.env.demo.example")
    if not env_path.exists():
        pytest.skip(f"Demo env file not found at {env_path}")

    cors_allowed = []
    cors_extra = ""
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("CORS_ALLOWED_ORIGINS="):
                val = line.split("=", 1)[1].strip()
                cors_allowed = [origin.strip() for origin in val.split(",") if origin.strip()]
            elif line.startswith("CORS_EXTRA_REGEX="):
                cors_extra = line.split("=", 1)[1].strip()

    # Replicate assethub_api.settings regex logic:
    # Default regex matches assethub.pages.dev and its preview subdomains
    regexes = [
        r"^https:\/\/(?:[a-z0-9-]+\.)?assethub\.pages\.dev$",
    ]
    if cors_extra:
        regexes.append(cors_extra.strip())

    rf = RequestFactory()
    mw = CorsMiddleware(lambda req: HttpResponse(b"OK"))

    with override_settings(
        CORS_ALLOWED_ORIGINS=cors_allowed,
        CORS_ALLOWED_ORIGIN_REGEXES=regexes,
        CORS_ALLOW_CREDENTIALS=False,
    ):
        test_origins = [
            ("https://assethub.pages.dev", True),
            ("https://preview-123.assethub.pages.dev", True),
            ("https://unrelated-project.pages.dev", False),
            ("https://assethub-attacker.pages.dev", False),
            ("https://evil.pages.dev", False),
        ]

        for origin, allowed in test_origins:
            # 1. GET request check
            req_get = rf.get("/api/health/", HTTP_ORIGIN=origin)
            res_get = mw(req_get)
            if allowed:
                assert res_get.get("Access-Control-Allow-Origin") == origin
            else:
                assert "Access-Control-Allow-Origin" not in res_get

            # 2. Preflight OPTIONS request check
            req_opt = rf.options(
                "/api/health/",
                HTTP_ORIGIN=origin,
                HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
                HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
            )
            res_opt = mw(req_opt)
            if allowed:
                assert res_opt.status_code == status.HTTP_200_OK
                assert res_opt.get("Access-Control-Allow-Origin") == origin
            else:
                assert "Access-Control-Allow-Origin" not in res_opt
