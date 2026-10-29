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
def test_cors_preflight_pages_dev_origin(api_client):
    """Preflight OPTIONS request from Cloudflare Pages (*.pages.dev) matches regex."""
    response = api_client.options(
        '/api/health/',
        HTTP_ORIGIN='https://assethub-demo.pages.dev',
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.get('Access-Control-Allow-Origin') == 'https://assethub-demo.pages.dev'


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
