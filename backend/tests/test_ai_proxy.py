import pytest
from unittest.mock import MagicMock, patch
import urllib.error
import socket

from rest_framework.test import APIClient

from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category
from apps.ai.services import AiProxyService


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.fixture(autouse=True)
def clean_ai_service_state():
    AiProxyService.reset_circuit_breaker()
    AiProxyService.reset_rate_limit()
    yield
    AiProxyService.reset_circuit_breaker()
    AiProxyService.reset_rate_limit()


@pytest.fixture
def sample_categories(db):
    cat1 = Category.objects.create(name="Laptop", description="Máy tính xách tay")
    cat2 = Category.objects.create(name="Màn hình", description="Màn hình máy tính")
    return [cat1, cat2]


@pytest.mark.django_db
def test_admin_calls_proxy_success(admin_user, sample_categories):
    client = auth_client_for(admin_user)
    target_category = sample_categories[0]

    mock_worker_response = {
        "request_id": "test-uuid-1234",
        "suggested_category_id": target_category.id,
        "suggested_category_name": target_category.name,
        "explanation": "Tài sản này là máy tính xách tay cao cấp phù hợp danh mục Laptop.",
        "log_saved": True,
    }

    with patch.object(AiProxyService, "_post_request", return_value=mock_worker_response) as mock_post:
        response = client.post(
            "/api/ai/suggest-category/",
            data={
                "name": "MacBook Pro 16 M3 Max",
                "description": "Máy tính cấu hình mạnh",
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["suggested_category_id"] == target_category.id
        assert response.data["suggested_category_name"] == target_category.name
        assert response.data["explanation"] == mock_worker_response["explanation"]
        assert response.data["log_saved"] is True
        assert mock_post.called


@pytest.mark.django_db
def test_non_admin_calls_proxy_forbidden(manager_user, regular_user, api_client):
    # Manager
    manager_client = auth_client_for(manager_user)
    res_mgr = manager_client.post(
        "/api/ai/suggest-category/",
        data={"name": "Dell Latitude"},
        format="json",
    )
    assert res_mgr.status_code == 403

    # Regular User
    user_client = auth_client_for(regular_user)
    res_user = user_client.post(
        "/api/ai/suggest-category/",
        data={"name": "Dell Latitude"},
        format="json",
    )
    assert res_user.status_code == 403

    # Unauthenticated
    res_unauth = api_client.post(
        "/api/ai/suggest-category/",
        data={"name": "Dell Latitude"},
        format="json",
    )
    assert res_unauth.status_code == 401


@pytest.mark.django_db
def test_validation_empty_or_oversized(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    # Empty name
    res_empty = client.post(
        "/api/ai/suggest-category/",
        data={"name": "   ", "description": "Some description"},
        format="json",
    )
    assert res_empty.status_code == 400

    # Oversized name (> 200 chars)
    res_long_name = client.post(
        "/api/ai/suggest-category/",
        data={"name": "A" * 201, "description": "Valid description"},
        format="json",
    )
    assert res_long_name.status_code == 400

    # Oversized description (> 2000 chars)
    res_long_desc = client.post(
        "/api/ai/suggest-category/",
        data={"name": "Valid Name", "description": "B" * 2001},
        format="json",
    )
    assert res_long_desc.status_code == 400


@pytest.mark.django_db
def test_worker_returns_401_maps_to_503(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"error":"Unauthorized"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "ThinkPad X1 Carbon"},
            format="json",
        )
        assert response.status_code == 503


@pytest.mark.django_db
def test_worker_timeout_maps_to_504(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    with patch("urllib.request.urlopen", side_effect=socket.timeout("Request timed out")):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "MacBook Air M2"},
            format="json",
        )
        assert response.status_code == 504


@pytest.mark.django_db
def test_worker_500_and_circuit_breaker(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_500_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=500,
        msg="Internal Server Error",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"error":"Worker crashed"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_500_error) as mock_urlopen:
        # 1st failure -> 503
        r1 = client.post("/api/ai/suggest-category/", data={"name": "Device 1"}, format="json")
        assert r1.status_code == 503
        assert AiProxyService._failure_count == 1
        assert not AiProxyService.is_circuit_open()

        # 2nd failure -> 503
        r2 = client.post("/api/ai/suggest-category/", data={"name": "Device 2"}, format="json")
        assert r2.status_code == 503
        assert AiProxyService._failure_count == 2
        assert not AiProxyService.is_circuit_open()

        # 3rd failure -> 503 and circuit trips
        r3 = client.post("/api/ai/suggest-category/", data={"name": "Device 3"}, format="json")
        assert r3.status_code == 503
        assert AiProxyService._failure_count == 3
        assert AiProxyService.is_circuit_open()

    # 4th call: Circuit is open -> fast fails with 503 without calling urlopen
    with patch("urllib.request.urlopen") as mock_urlopen_trip:
        r4 = client.post("/api/ai/suggest-category/", data={"name": "Device 4"}, format="json")
        assert r4.status_code == 503
        assert "circuit breaker is open" in str(r4.data)
        assert not mock_urlopen_trip.called
