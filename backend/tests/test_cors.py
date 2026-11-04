import pytest
from rest_framework import status


@pytest.mark.django_db
def test_cors_preflight_allowed_origin(api_client):
    """Preflight OPTIONS request from allowed origin returns 200 and Access-Control-Allow-Origin."""
    response = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='http://localhost:5173',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.get('Access-Control-Allow-Origin') == 'http://localhost:5173'


@pytest.mark.django_db
def test_cors_exact_pages_dev_origin_allowed(api_client):
    """OPTIONS and GET requests from exact assethub.pages.dev return Access-Control-Allow-Origin."""
    # Preflight OPTIONS
    options_res = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://assethub.pages.dev',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert options_res.status_code == status.HTTP_200_OK
    assert options_res.get('Access-Control-Allow-Origin') == 'https://assethub.pages.dev'

    # Simple GET
    get_res = api_client.get('/api/health/', HTTP_ORIGIN='https://assethub.pages.dev')
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.get('Access-Control-Allow-Origin') == 'https://assethub.pages.dev'


@pytest.mark.django_db
def test_cors_preview_pages_dev_subdomain_allowed(api_client):
    """OPTIONS and GET requests from preview subdomain return Access-Control-Allow-Origin."""
    # Preflight OPTIONS
    options_res = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://preview-123.assethub.pages.dev',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert options_res.status_code == status.HTTP_200_OK
    assert options_res.get('Access-Control-Allow-Origin') == 'https://preview-123.assethub.pages.dev'

    # Simple GET
    get_res = api_client.get('/api/health/', HTTP_ORIGIN='https://preview-123.assethub.pages.dev')
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.get('Access-Control-Allow-Origin') == 'https://preview-123.assethub.pages.dev'


@pytest.mark.django_db
def test_cors_attacker_prefix_rejected(api_client):
    """Origin with prefix assethub-* (e.g. assethub-attacker.pages.dev) is REJECTED."""
    # Preflight OPTIONS
    options_res = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://assethub-attacker.pages.dev',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert 'Access-Control-Allow-Origin' not in options_res

    # Simple GET
    get_res = api_client.get('/api/health/', HTTP_ORIGIN='https://assethub-attacker.pages.dev')
    assert 'Access-Control-Allow-Origin' not in get_res


@pytest.mark.django_db
def test_cors_arbitrary_pages_dev_rejected(api_client):
    """Arbitrary pages.dev origin (e.g. evil.pages.dev) is REJECTED."""
    # Preflight OPTIONS
    options_res = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://evil.pages.dev',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert 'Access-Control-Allow-Origin' not in options_res

    # Simple GET
    get_res = api_client.get('/api/health/', HTTP_ORIGIN='https://evil.pages.dev')
    assert 'Access-Control-Allow-Origin' not in get_res


@pytest.mark.django_db
def test_cors_preflight_disallowed_origin(api_client):
    """Preflight OPTIONS request from disallowed origin does NOT return Access-Control-Allow-Origin."""
    response = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://malicious-site.com',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert response.get('Access-Control-Allow-Origin') != 'https://malicious-site.com'
    assert 'Access-Control-Allow-Origin' not in response


@pytest.mark.django_db
def test_cors_allow_credentials_disabled(api_client):
    """Access-Control-Allow-Credentials must not be True (V1 constraint)."""
    response = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='http://localhost:5173',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
    )
    assert response.status_code == status.HTTP_200_OK
    allow_credentials = response.get('Access-Control-Allow-Credentials')
    assert allow_credentials is None or allow_credentials.lower() == 'false'


@pytest.mark.django_db
def test_cors_preflight_allowed_headers(api_client):
    """Preflight headers must allow Authorization and Content-Type."""
    response = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='http://localhost:5173',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization, content-type',
    )
    assert response.status_code == status.HTTP_200_OK
    allow_headers = response.get('Access-Control-Allow-Headers', '').lower()
    assert 'authorization' in allow_headers
    assert 'content-type' in allow_headers
