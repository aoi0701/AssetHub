"""AI Worker Gateway for AssetHub Assistant.

Dispatches natural language questions and turn context to Workers AI planner
endpoint with strict HTTP client security, zero redirects, 64 KiB response cap,
and schema validation.
"""
from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from django.conf import settings

from apps.assistant.contracts import (
    AssistantProviderUnavailable,
    PlannerDecision,
    PlannerProtocolError,
    validate_decision,
)

MAX_RESPONSE_BYTES = 65536  # 64 KiB limit for planner responses
USER_AGENT = "AssetHub-Backend/1.0"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_ERROR_BODY_BYTES = 1024  # Worker error bodies are tiny JSON objects
# The Worker's own error codes; anything else in an error body is never echoed.
WORKER_ERROR_CODES = frozenset({"INVALID_PLAN", "UNAVAILABLE", "3036"})


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that rejects all redirects to prevent credential leakage."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def get_opener() -> urllib.request.OpenerDirector:
    """Build an OpenerDirector configured with NoRedirectHandler."""
    return urllib.request.build_opener(NoRedirectHandler)


def _validate_and_format_endpoint(raw_url: str | None) -> str:
    """Validate AI Worker URL and return canonical endpoint path.

    Requires HTTPS for remote cloud hosts, restricts HTTP exclusively to loopback,
    and rejects userinfo, fragment, or unsupported schemes.
    """
    if not raw_url or not str(raw_url).strip():
        raise AssistantProviderUnavailable("AI Worker URL is not configured")

    clean_url = str(raw_url).strip()
    try:
        parsed = urllib.parse.urlsplit(clean_url)
    except Exception as exc:
        raise AssistantProviderUnavailable(f"Malformed AI Worker URL: {exc}") from exc

    if not parsed.scheme or not parsed.netloc:
        raise AssistantProviderUnavailable(f"Incomplete AI Worker URL: {clean_url}")

    if parsed.username or parsed.password:
        raise AssistantProviderUnavailable("AI Worker URL must not contain user credentials")

    if parsed.fragment:
        raise AssistantProviderUnavailable("AI Worker URL must not contain URL fragment")

    scheme = parsed.scheme.lower()
    if scheme == "https":
        pass  # HTTPS permitted for any host
    elif scheme == "http":
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOOPBACK_HOSTS:
            raise AssistantProviderUnavailable(
                f"HTTP scheme is only permitted for loopback hosts, rejected host: {hostname}"
            )
    else:
        raise AssistantProviderUnavailable(f"Unsupported scheme for AI Worker URL: {scheme}")

    if clean_url.endswith("/api/assistant/plan"):
        return clean_url
    return clean_url.rstrip("/") + "/api/assistant/plan"


def _validate_timeout(raw_timeout: Any) -> float:
    """Validate ASSISTANT_AI_TIMEOUT_SECONDS as a positive finite number."""
    try:
        timeout = float(raw_timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
        return timeout
    except (TypeError, ValueError) as exc:
        raise AssistantProviderUnavailable(
            f"ASSISTANT_AI_TIMEOUT_SECONDS must be a positive finite number, got: {raw_timeout}"
        ) from exc


def _worker_error_code(exc: urllib.error.HTTPError) -> str | None:
    """A known Worker error code from a small JSON error body; None for anything else."""
    try:
        raw = exc.read(MAX_ERROR_BODY_BYTES + 1)
        if len(raw) > MAX_ERROR_BODY_BYTES:
            return None
        code = str(json.loads(raw.decode("utf-8")).get("code"))
    except Exception:
        return None
    return code if code in WORKER_ERROR_CODES else None


def plan_question(
    *,
    question: str,
    context: list[dict[str, Any]] | None = None,
    scope: dict[str, Any] | None = None,
) -> PlannerDecision:
    """Send question, context, and scope to AI Worker and return validated PlannerDecision.

    Resolves worker endpoint and credentials from Django settings, enriches scope
    with server UTC clock and Vietnam display timezone, prevents redirect token leakage,
    enforces 64 KiB response limits, and validates output against planner contract schema.

    Raises AssistantProviderUnavailable on network failure, timeout, HTTP error,
    oversized response, or invalid JSON/schema. Never retries.
    """
    # 1. Validate configuration without silent fallback
    raw_worker_url = getattr(settings, "AI_WORKER_URL", None)
    worker_url = _validate_and_format_endpoint(raw_worker_url)

    raw_token = getattr(settings, "AI_SERVICE_TOKEN", None)
    if not raw_token or not str(raw_token).strip():
        raise AssistantProviderUnavailable("AI Service Token is not configured")
    service_token = str(raw_token).strip()

    raw_timeout = getattr(settings, "ASSISTANT_AI_TIMEOUT_SECONDS", 30)
    timeout = _validate_timeout(raw_timeout)

    # 2. Enrich scope with server authority
    augmented_scope = dict(scope) if scope is not None else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    augmented_scope["now_utc"] = now_iso
    augmented_scope["now"] = now_iso
    augmented_scope["display_timezone"] = "Asia/Ho_Chi_Minh"

    payload = {
        "question": question,
        "context": list(context) if context is not None else [],
        "scope": augmented_scope,
    }

    try:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AssistantProviderUnavailable(f"Cannot serialize plan request to JSON: {exc}") from exc

    headers = {
        "Authorization": f"Bearer {service_token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    req = urllib.request.Request(worker_url, data=body_bytes, headers=headers, method="POST")
    opener = get_opener()

    try:
        with opener.open(req, timeout=timeout) as resp:
            raw_data = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        worker_code = _worker_error_code(exc)
        suffix = f" (worker code {worker_code})" if worker_code else ""
        raise AssistantProviderUnavailable(
            f"AI Worker returned HTTP {exc.code}: {exc.reason}{suffix}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AssistantProviderUnavailable(
            f"AI Worker connection failed: {exc.reason}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AssistantProviderUnavailable(
            f"AI Worker request timed out: {exc}"
        ) from exc
    except Exception as exc:
        raise AssistantProviderUnavailable(
            f"AI Worker request failed unexpectedly: {exc}"
        ) from exc

    if len(raw_data) > MAX_RESPONSE_BYTES:
        raise AssistantProviderUnavailable(
            f"AI Worker response exceeded maximum allowed size ({MAX_RESPONSE_BYTES} bytes)"
        )

    try:
        raw_text = raw_data.decode("utf-8")
        data = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssistantProviderUnavailable(
            f"AI Worker returned invalid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise AssistantProviderUnavailable("AI Worker response must be a JSON object")

    if "decision" not in data:
        raise AssistantProviderUnavailable("AI Worker response missing 'decision' key")

    try:
        return validate_decision(data["decision"])
    except PlannerProtocolError as exc:
        raise AssistantProviderUnavailable(
            f"AI Worker returned invalid planner decision: {exc}"
        ) from exc
