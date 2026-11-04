import socket
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.ai.services import AiProxyService
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category


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
        assert response.data["is_fallback"] is False
        assert mock_post.called


@pytest.mark.django_db
def test_non_admin_calls_proxy_forbidden(manager_user, regular_user, api_client):
    # Manager -> 403
    manager_client = auth_client_for(manager_user)
    res_mgr = manager_client.post(
        "/api/ai/suggest-category/",
        data={"name": "Dell Latitude"},
        format="json",
    )
    assert res_mgr.status_code == 403

    # Regular User -> 403
    user_client = auth_client_for(regular_user)
    res_user = user_client.post(
        "/api/ai/suggest-category/",
        data={"name": "Dell Latitude"},
        format="json",
    )
    assert res_user.status_code == 403

    # Unauthenticated -> 401
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
def test_rate_limit_exceeded_returns_429(admin_user, sample_categories):
    client = auth_client_for(admin_user)
    target_category = sample_categories[0]

    mock_worker_response = {
        "request_id": "test-uuid-rate-limit",
        "suggested_category_id": target_category.id,
        "suggested_category_name": target_category.name,
        "explanation": "Valid category suggestion",
        "log_saved": False,
    }

    with patch.object(AiProxyService, "_post_request", return_value=mock_worker_response):
        for _ in range(10):
            res = client.post(
                "/api/ai/suggest-category/",
                data={"name": "Dell XPS 13"},
                format="json",
            )
            assert res.status_code == 200

        # 11th request must raise Throttled (429), NOT swallowed by fallback
        res_throttled = client.post(
            "/api/ai/suggest-category/",
            data={"name": "Dell XPS 13"},
            format="json",
        )
        assert res_throttled.status_code == 429


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
        assert "authentication failed" in str(response.data)
        assert AiProxyService._failure_count == 0
        assert not AiProxyService.is_circuit_open()


@pytest.mark.django_db
def test_worker_401_consecutive_calls_never_trips_circuit_breaker(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"error":"Unauthorized"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        for i in range(5):
            response = client.post(
                "/api/ai/suggest-category/",
                data={"name": f"ThinkPad X1 Carbon {i}"},
                format="json",
            )
            assert response.status_code == 503
            assert "authentication failed" in str(response.data)
            assert AiProxyService._failure_count == 0
            assert not AiProxyService.is_circuit_open()
            assert not (response.status_code == 200 and response.data.get("is_fallback") is True)


@pytest.mark.django_db
def test_worker_403_consecutive_calls_never_trips_circuit_breaker(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"error":"Forbidden"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        for i in range(5):
            response = client.post(
                "/api/ai/suggest-category/",
                data={"name": f"Dell Latitude {i}"},
                format="json",
            )
            assert response.status_code == 503
            assert "authentication failed" in str(response.data)
            assert AiProxyService._failure_count == 0
            assert not AiProxyService.is_circuit_open()
            assert not (response.status_code == 200 and response.data.get("is_fallback") is True)


def test_post_request_direct_401_403_raises_service_unavailable():
    for code in (401, 403):
        http_error = urllib.error.HTTPError(
            url="http://127.0.0.1:8787/api/suggest-category",
            code=code,
            msg="Auth error",
            hdrs=None,
            fp=MagicMock(read=lambda: b'{"error":"Auth failure"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(Exception) as exc_info:
                AiProxyService._post_request("http://test.url", {}, {})
            assert "AI worker authentication failed." in str(exc_info.value)
            assert AiProxyService._failure_count == 0
            assert not AiProxyService.is_circuit_open()


@pytest.mark.django_db
def test_worker_quota_429_code_3036_triggers_fallback(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"code": 3036, "error": "Free quota exceeded"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "ThinkPad X1 Carbon", "description": "Laptop doanh nhân"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[0].id
        assert response.data["suggested_category_name"] == "Laptop"
        assert response.data["log_saved"] is False
        assert "Gợi ý tự động từ từ khóa phù hợp 'Laptop'" in response.data["explanation"]


@pytest.mark.django_db
def test_worker_capacity_429_code_3040_triggers_fallback(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"code": 3040, "error": "Capacity constrained"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "Dell UltraSharp U2723QE", "description": "Màn hình 4K IPS Black"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[1].id
        assert response.data["suggested_category_name"] == "Màn hình"
        assert response.data["log_saved"] is False
        assert "Gợi ý tự động từ từ khóa phù hợp 'Màn hình'" in response.data["explanation"]


@pytest.mark.django_db
def test_worker_timeout_triggers_fallback(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    with patch("urllib.request.urlopen", side_effect=socket.timeout("Request timed out")):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "MacBook Air M2"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[0].id
        assert response.data["suggested_category_name"] == "Laptop"


@pytest.mark.django_db
def test_worker_connection_error_triggers_fallback(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "Màn hình LG 27UP850"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[1].id
        assert response.data["suggested_category_name"] == "Màn hình"


@pytest.mark.django_db
def test_fallback_with_non_matching_keywords_raises_503(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    http_503_error = urllib.error.HTTPError(
        url="http://127.0.0.1:8787/api/suggest-category",
        code=503,
        msg="Service Unavailable",
        hdrs=None,
        fp=MagicMock(read=lambda: b'{"error":"Worker temporarily unavailable"}'),
    )

    with patch("urllib.request.urlopen", side_effect=http_503_error):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "Vật dụng bí ẩn XYZ-999", "description": "Không rõ danh mục nào"},
            format="json",
        )
        # Must raise controlled 503 ServiceUnavailable, NOT default to categories[0]
        assert response.status_code == 503
        assert "Không thể tự động phân loại danh mục. Vui lòng chọn thủ công." in str(response.data)


@pytest.mark.django_db
def test_circuit_breaker_open_uses_fallback_without_calling_urlopen(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    # Trip the circuit breaker
    AiProxyService._failure_count = 3
    AiProxyService._tripped_until = time.time() + 30
    assert AiProxyService.is_circuit_open()

    with patch("urllib.request.urlopen") as mock_urlopen:
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "ThinkPad X1 Carbon"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[0].id
        assert response.data["suggested_category_name"] == "Laptop"
        assert not mock_urlopen.called


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
        # 1st failure on non-matching keyword -> 503
        r1 = client.post("/api/ai/suggest-category/", data={"name": "Device 1"}, format="json")
        assert r1.status_code == 503
        assert AiProxyService._failure_count == 1
        assert not AiProxyService.is_circuit_open()

        # 2nd failure on non-matching keyword -> 503
        r2 = client.post("/api/ai/suggest-category/", data={"name": "Device 2"}, format="json")
        assert r2.status_code == 503
        assert AiProxyService._failure_count == 2
        assert not AiProxyService.is_circuit_open()

        # 3rd failure on non-matching keyword -> 503 and circuit trips
        r3 = client.post("/api/ai/suggest-category/", data={"name": "Device 3"}, format="json")
        assert r3.status_code == 503
        assert AiProxyService._failure_count == 3
        assert AiProxyService.is_circuit_open()

    # 4th call: Circuit is open -> immediate fallback on matching keyword without calling urlopen
    with patch("urllib.request.urlopen") as mock_urlopen_trip:
        r4 = client.post("/api/ai/suggest-category/", data={"name": "MacBook Pro 16"}, format="json")
        assert r4.status_code == 200
        assert r4.data["is_fallback"] is True
        assert r4.data["suggested_category_name"] == "Laptop"
        assert not mock_urlopen_trip.called

    # 5th call: Circuit is open -> non-matching keyword raises controlled 503 without calling urlopen
    with patch("urllib.request.urlopen") as mock_urlopen_trip_2:
        r5 = client.post("/api/ai/suggest-category/", data={"name": "Device 4"}, format="json")
        assert r5.status_code == 503
        assert "Không thể tự động phân loại danh mục. Vui lòng chọn thủ công." in str(r5.data)
        assert not mock_urlopen_trip_2.called


@pytest.mark.django_db
def test_empty_categories_in_db_raises_503(admin_user):
    client = auth_client_for(admin_user)
    Category.objects.all().delete()

    response = client.post(
        "/api/ai/suggest-category/",
        data={"name": "MacBook Pro 16"},
        format="json",
    )
    assert response.status_code == 503
    assert "Không thể tự động phân loại danh mục. Vui lòng chọn thủ công." in str(response.data)


@pytest.mark.django_db
def test_worker_returns_unrecognized_category_triggers_fallback(admin_user, sample_categories):
    client = auth_client_for(admin_user)

    mock_invalid_response = {
        "request_id": "test-uuid-unrecognized",
        "suggested_category_id": 999999,  # Non-existent in DB
        "suggested_category_name": "Ghost Category",
        "explanation": "Invalid category returned by worker",
        "log_saved": True,
    }

    with patch.object(AiProxyService, "_post_request", return_value=mock_invalid_response):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "MacBook Air M2"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[0].id
        assert response.data["suggested_category_name"] == "Laptop"


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_id", ["not-a-number", [1, 2], True, -5, {}])
def test_invalid_suggested_category_id_types_trigger_fallback(admin_user, sample_categories, invalid_id):
    client = auth_client_for(admin_user)

    mock_worker_response = {
        "request_id": f"test-invalid-type-{type(invalid_id).__name__}",
        "suggested_category_id": invalid_id,
        "suggested_category_name": "Unknown",
        "explanation": "Worker returned non-integer category id",
        "log_saved": False,
    }

    with patch.object(AiProxyService, "_post_request", return_value=mock_worker_response):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "ThinkPad X1 Carbon", "description": "Laptop doanh nhân"},
            format="json",
        )
        # Never raises ValueError / HTTP 500
        assert response.status_code == 200
        assert response.data["is_fallback"] is True
        assert response.data["suggested_category_id"] == sample_categories[0].id
        assert response.data["suggested_category_name"] == "Laptop"


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_id", ["not-a-number", [1, 2], True, -5, {}])
def test_invalid_suggested_category_id_types_non_matching_raises_503(admin_user, sample_categories, invalid_id):
    client = auth_client_for(admin_user)

    mock_worker_response = {
        "request_id": f"test-invalid-type-non-match-{type(invalid_id).__name__}",
        "suggested_category_id": invalid_id,
        "suggested_category_name": "Unknown",
        "explanation": "Worker returned non-integer category id",
        "log_saved": False,
    }

    with patch.object(AiProxyService, "_post_request", return_value=mock_worker_response):
        response = client.post(
            "/api/ai/suggest-category/",
            data={"name": "Vật dụng bí ẩn XYZ-999", "description": "Không khớp từ khóa nào"},
            format="json",
        )
        # Never raises ValueError / HTTP 500, raises controlled 503
        assert response.status_code == 503
        assert "Không thể tự động phân loại danh mục. Vui lòng chọn thủ công." in str(response.data)
