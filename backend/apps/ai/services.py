import json
import socket
import time
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError

from apps.ai.exceptions import GatewayTimeout, ServiceUnavailable
from apps.authentication.models import User
from apps.departments.models import Category


class AiProxyService:
    _failure_count = 0
    _tripped_until = 0

    @classmethod
    def is_circuit_open(cls) -> bool:
        now = time.time()
        if cls._tripped_until > now:
            return True
        if cls._tripped_until != 0 and cls._tripped_until <= now:
            cls._tripped_until = 0
            cls._failure_count = 0
        return False

    @classmethod
    def record_failure(cls) -> None:
        cls._failure_count += 1
        if cls._failure_count >= 3:
            cls._tripped_until = time.time() + 30

    @classmethod
    def record_success(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def reset_circuit_breaker(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def check_rate_limit(cls, user_id) -> None:
        now = time.time()
        cache_key = f"ai_rate_limit_{user_id}"
        req_times = cache.get(cache_key, [])
        req_times = [t for t in req_times if now - t < 60]
        if len(req_times) >= 10:
            raise Throttled(detail="Rate limit exceeded. Maximum 10 requests per minute.")
        req_times.append(now)
        cache.set(cache_key, req_times, timeout=60)

    @classmethod
    def reset_rate_limit(cls, user_id=None) -> None:
        if user_id is not None:
            cache.delete(f"ai_rate_limit_{user_id}")
        else:
            cache.clear()

    @classmethod
    def _post_request(cls, url: str, payload: dict, headers: dict, timeout: float = 6.0) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw_body = response.read().decode("utf-8")
                return json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            cls.record_failure()
            if exc.code == 401 or exc.code == 403:
                raise ServiceUnavailable("AI worker authentication failed.")
            if exc.code >= 500:
                raise ServiceUnavailable(f"AI worker error ({exc.code}).")
            raise ServiceUnavailable(f"AI worker unexpected response ({exc.code}).")
        except (TimeoutError, socket.timeout) as exc:
            cls.record_failure()
            raise GatewayTimeout("AI worker timed out after 6.0 seconds.")
        except urllib.error.URLError as exc:
            cls.record_failure()
            reason_str = str(getattr(exc, "reason", exc)).lower()
            if (
                isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
                or "timed out" in reason_str
            ):
                raise GatewayTimeout("AI worker timed out after 6.0 seconds.")
            raise ServiceUnavailable(f"Failed to connect to AI worker: {exc}")
        except Exception as exc:
            cls.record_failure()
            raise ServiceUnavailable(f"Unexpected error communicating with AI worker: {exc}")

    @classmethod
    def suggest_category(cls, actor, name: str, description: str = "") -> dict:
        # 1. RBAC Check: Only ADMIN
        if not actor or getattr(actor, "role", None) != User.Role.ADMIN:
            raise PermissionDenied("Only administrators can request AI suggestions.")

        # 2. Input validation
        if not name or not isinstance(name, str) or not name.strip():
            raise ValidationError({"name": ["Tên tài sản không được để trống."]})

        cleaned_name = name.strip()
        if len(cleaned_name) > 200:
            raise ValidationError({"name": ["Tên tài sản không được vượt quá 200 ký tự."]})

        cleaned_description = str(description or "").strip()
        if len(cleaned_description) > 2000:
            raise ValidationError({"description": ["Mô tả không được vượt quá 2000 ký tự."]})

        # 3. Rate limiting (10 req/min per user)
        cls.check_rate_limit(getattr(actor, "id", "anonymous"))

        # 4. Circuit breaker check
        if cls.is_circuit_open():
            raise ServiceUnavailable("AI service circuit breaker is open. Please try again later.")

        # 5. Fetch active categories
        categories = list(Category.objects.values("id", "name"))
        if not categories:
            raise ServiceUnavailable("No categories available in the system.")

        # 6. Prepare worker request
        request_id = str(uuid.uuid4())
        worker_url = settings.AI_WORKER_URL.rstrip("/") + "/api/suggest-category"
        headers = {
            "Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "request_id": request_id,
            "name": cleaned_name,
            "description": cleaned_description,
            "categories": categories,
        }

        # 7. Execute request
        data = cls._post_request(worker_url, payload, headers, timeout=6.0)

        # 8. Success handling
        cls.record_success()

        suggested_id = data.get("suggested_category_id")
        category = Category.objects.filter(id=suggested_id).first()
        if not category:
            raise ServiceUnavailable("AI suggested an unrecognized category.")

        return {
            "request_id": data.get("request_id", request_id),
            "suggested_category_id": category.id,
            "suggested_category_name": category.name,
            "explanation": str(data.get("explanation", ""))[:500],
            "log_saved": bool(data.get("log_saved", False)),
        }
