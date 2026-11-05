"""Unit and integration tests for AI Worker Gateway."""
from __future__ import annotations

import http.server
import json
import socket
import threading
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import pytest
from django.test import override_settings

from apps.assistant.contracts import AssistantProviderUnavailable, PlannerDecision
from apps.assistant.gateway import (
    MAX_RESPONSE_BYTES,
    USER_AGENT,
    NoRedirectHandler,
    get_opener,
    plan_question,
)


class MockAIServerState:
    """Thread-safe state holder for mock AI HTTP server."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response_status: int = 200
        self.response_headers: dict[str, str] = {"Content-Type": "application/json"}
        self.response_body: bytes = b""
        self.redirect_target_hit: bool = False
        self.delay_seconds: float = 0.0


class MockAIHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Ephemeral HTTP handler simulating Workers AI endpoints."""

    state: MockAIServerState

    def do_POST(self) -> None:
        if self.state.delay_seconds > 0:
            import time
            time.sleep(self.state.delay_seconds)

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        parsed_json = None
        if body:
            try:
                parsed_json = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        self.state.requests.append({
            "path": self.path,
            "headers": dict(self.headers),
            "raw_body": body,
            "json": parsed_json,
        })

        if self.state.response_status in (301, 302, 303, 307, 308):
            self.send_response(self.state.response_status)
            for k, v in self.state.response_headers.items():
                self.send_header(k, v)
            self.end_headers()
            return

        self.send_response(self.state.response_status)
        for k, v in self.state.response_headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(self.state.response_body)

    def do_GET(self) -> None:
        if self.path == "/redirected-target":
            self.state.redirect_target_hit = True
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"decision": null}')
            return
        self.send_response(405)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass  # Suppress console logging during test runs


@pytest.fixture(autouse=True)
def default_gateway_test_settings():
    """Ensure tests run with explicit settings and never rely on hardcoded defaults."""
    with override_settings(
        AI_SERVICE_TOKEN="test-service-token-abc",
        ASSISTANT_AI_TIMEOUT_SECONDS=10,
    ):
        yield


@pytest.fixture
def mock_ai_server():
    """Spin up an ephemeral loopback HTTP server and tear down after test."""
    state = MockAIServerState()

    class BoundHandler(MockAIHTTPHandler):
        pass

    BoundHandler.state = state

    server = http.server.HTTPServer(("127.0.0.1", 0), BoundHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{server.server_port}"
    state.base_url = base_url

    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)


# 1. NoRedirectHandler Unit Tests

def test_no_redirect_handler_returns_none() -> None:
    handler = NoRedirectHandler()
    req = urllib.request.Request("http://127.0.0.1:8787/api/assistant/plan")
    for code in (301, 302, 303, 307, 308):
        result = handler.redirect_request(
            req=req,
            fp=None,
            code=code,
            msg="Redirect",
            headers={},
            newurl="http://other-host.internal/api/assistant/plan",
        )
        assert result is None, f"Handler must return None for redirect code {code}"


# 2. Configuration & URL Validation Tests (No network call)

@pytest.mark.parametrize("bad_url", ["", None, "   "])
def test_missing_or_empty_worker_url_raises_without_network(bad_url: Any) -> None:
    with override_settings(AI_WORKER_URL=bad_url):
        with pytest.raises(AssistantProviderUnavailable, match="AI Worker URL is not configured"):
            plan_question(question="Test question")


@pytest.mark.parametrize("bad_token", ["", None, "   "])
def test_missing_or_empty_service_token_raises_without_network(bad_token: Any) -> None:
    with override_settings(AI_WORKER_URL="http://127.0.0.1:8787", AI_SERVICE_TOKEN=bad_token):
        with pytest.raises(AssistantProviderUnavailable, match="AI Service Token is not configured"):
            plan_question(question="Test question")


def test_insecure_remote_http_url_rejected_without_network() -> None:
    with override_settings(
        AI_WORKER_URL="http://remote-cloud-worker.example.com",
        AI_SERVICE_TOKEN="token",
    ):
        with pytest.raises(AssistantProviderUnavailable, match="HTTP scheme is only permitted for loopback hosts"):
            plan_question(question="Test question")


def test_url_with_credentials_rejected() -> None:
    with override_settings(
        AI_WORKER_URL="https://user:pass@worker.assethub.dev",
        AI_SERVICE_TOKEN="token",
    ):
        with pytest.raises(AssistantProviderUnavailable, match="must not contain user credentials"):
            plan_question(question="Test question")


def test_url_with_fragment_rejected() -> None:
    with override_settings(
        AI_WORKER_URL="https://worker.assethub.dev/api/assistant/plan#section",
        AI_SERVICE_TOKEN="token",
    ):
        with pytest.raises(AssistantProviderUnavailable, match="must not contain URL fragment"):
            plan_question(question="Test question")


@pytest.mark.parametrize("bad_timeout", [-1, 0, -10.5, "not-a-number", float("inf"), float("nan")])
def test_invalid_timeout_rejected(bad_timeout: Any) -> None:
    with override_settings(
        AI_WORKER_URL="http://127.0.0.1:8787",
        AI_SERVICE_TOKEN="token",
        ASSISTANT_AI_TIMEOUT_SECONDS=bad_timeout,
    ):
        with pytest.raises(AssistantProviderUnavailable, match="ASSISTANT_AI_TIMEOUT_SECONDS must be a positive finite number"):
            plan_question(question="Test question")


# 3. Successful Plan Dispatch & Header Verification

def test_plan_question_success_tools(mock_ai_server: MockAIServerState) -> None:
    expected_decision: PlannerDecision = {
        "kind": "tools",
        "calls": [
            {
                "name": "assets.search",
                "arguments": {"query": "MacBook Pro", "limit": 5},
            }
        ],
        "clarification": None,
        "reason": None,
    }
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = json.dumps({"decision": expected_decision}).encode("utf-8")

    with override_settings(
        AI_WORKER_URL=mock_ai_server.base_url,
        AI_SERVICE_TOKEN="test-token-xyz",
        ASSISTANT_AI_TIMEOUT_SECONDS=10,
    ):
        decision = plan_question(
            question="Tìm laptop MacBook Pro",
            context=[{"question": "hello", "refs": [{"asset_code": "LAP001"}]}],
            scope={"role": "USER", "department_id": 5},
        )

    assert decision == expected_decision
    assert len(mock_ai_server.requests) == 1
    req = mock_ai_server.requests[0]
    assert req["path"] == "/api/assistant/plan"
    assert req["headers"].get("Authorization") == "Bearer test-token-xyz"
    assert req["headers"].get("Content-Type") == "application/json"
    assert req["headers"].get("User-Agent") == USER_AGENT
    assert req["headers"].get("User-Agent") == "AssetHub-Backend/1.0"

    body_json = req["json"]
    assert body_json["question"] == "Tìm laptop MacBook Pro"
    assert body_json["context"] == [{"question": "hello", "refs": [{"asset_code": "LAP001"}]}]
    assert body_json["scope"]["role"] == "USER"
    assert body_json["scope"]["department_id"] == 5
    assert body_json["scope"]["display_timezone"] == "Asia/Ho_Chi_Minh"

    # Server UTC clock must be present and parseable
    now_utc_str = body_json["scope"]["now_utc"]
    parsed_now = datetime.fromisoformat(now_utc_str)
    assert parsed_now.tzinfo is not None
    assert (datetime.now(timezone.utc) - parsed_now).total_seconds() < 10


@pytest.mark.parametrize(
    "decision_payload",
    [
        {
            "kind": "clarify",
            "calls": [],
            "clarification": "Bạn muốn tìm thiết bị nào trong phòng IT?",
            "reason": None,
        },
        {
            "kind": "handoff",
            "calls": [],
            "clarification": None,
            "reason": "missing_knowledge",
        },
        {
            "kind": "handoff",
            "calls": [],
            "clarification": None,
            "reason": "unsupported_asset_question",
        },
        {
            "kind": "out_of_scope",
            "calls": [],
            "clarification": None,
            "reason": None,
        },
    ],
)
def test_plan_question_success_other_kinds(
    mock_ai_server: MockAIServerState, decision_payload: dict[str, Any]
) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = json.dumps({"decision": decision_payload}).encode("utf-8")

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        decision = plan_question(question="Câu hỏi mẫu", context=[], scope={})

    assert decision == decision_payload


# 4. Scope Security: Client Cannot Forge Server Clock

def test_scope_server_clock_overrides_client_supplied_clock(
    mock_ai_server: MockAIServerState,
) -> None:
    expected_decision: PlannerDecision = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = json.dumps({"decision": expected_decision}).encode("utf-8")

    forged_scope = {
        "now_utc": "1999-01-01T00:00:00Z",
        "display_timezone": "America/New_York",
        "role": "MANAGER",
    }

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        plan_question(question="test", context=[], scope=forged_scope)

    req = mock_ai_server.requests[0]
    body_json = req["json"]
    # Client cannot override server clock and VN timezone
    assert body_json["scope"]["now_utc"] != "1999-01-01T00:00:00Z"
    assert body_json["scope"]["display_timezone"] == "Asia/Ho_Chi_Minh"


# 5. Upstream HTTP Errors

@pytest.mark.parametrize(
    "status_code",
    [400, 401, 403, 405, 413, 429, 500, 502, 503, 504],
)
def test_upstream_http_errors_raise_unavailable(
    mock_ai_server: MockAIServerState, status_code: int
) -> None:
    mock_ai_server.response_status = status_code
    mock_ai_server.response_body = b'{"error": "Upstream error"}'

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test question")

    assert str(status_code) in str(exc_info.value)


# 6. Network Connection and Timeout Errors

def test_connection_refused_raises_unavailable() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]

    with override_settings(AI_WORKER_URL=f"http://127.0.0.1:{closed_port}"):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test question")

    assert "connection" in str(exc_info.value).lower() or "refused" in str(exc_info.value).lower()


def test_timeout_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.delay_seconds = 0.5

    with override_settings(
        AI_WORKER_URL=mock_ai_server.base_url,
        ASSISTANT_AI_TIMEOUT_SECONDS=0.1,
    ):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test question")

    assert "timed out" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()


# 7. Non-JSON, Malformed or Invalid Responses

def test_non_json_response_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = b"<!DOCTYPE html><html><body>502 Bad Gateway</body></html>"

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "invalid json" in str(exc_info.value).lower()


def test_empty_response_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = b""

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "invalid json" in str(exc_info.value).lower()


def test_non_object_root_json_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = b'["item1", "item2"]'

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "json object" in str(exc_info.value).lower()


def test_missing_decision_key_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = b'{"result": "success", "status": "ok"}'

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "decision" in str(exc_info.value).lower()


# 8. Schema Validation and Strict Contract Enforcement

@pytest.mark.parametrize(
    "invalid_decision",
    [
        # Missing kind
        {"calls": [], "clarification": None, "reason": None},
        # Invalid kind
        {"kind": "unknown_kind", "calls": [], "clarification": None, "reason": None},
        # Extra root key (version not allowed)
        {"kind": "out_of_scope", "calls": [], "clarification": None, "reason": None, "version": "1.0"},
        # tools kind with 0 calls
        {"kind": "tools", "calls": [], "clarification": None, "reason": None},
        # tools kind with >3 calls
        {
            "kind": "tools",
            "calls": [
                {"name": "assets.search", "arguments": {}},
                {"name": "assets.search", "arguments": {}},
                {"name": "assets.search", "arguments": {}},
                {"name": "assets.search", "arguments": {}},
            ],
            "clarification": None,
            "reason": None,
        },
        # Unknown tool name
        {
            "kind": "tools",
            "calls": [{"name": "unknown_tool", "arguments": {}}],
            "clarification": None,
            "reason": None,
        },
        # Boolean limit
        {
            "kind": "tools",
            "calls": [{"name": "assets.search", "arguments": {"limit": True}}],
            "clarification": None,
            "reason": None,
        },
        # Out of bounds limit
        {
            "kind": "tools",
            "calls": [{"name": "assets.search", "arguments": {"limit": 50}}],
            "clarification": None,
            "reason": None,
        },
        # ORM dunder lookup injection
        {
            "kind": "tools",
            "calls": [{"name": "assets.search", "arguments": {"query": "user__department"}}],
            "clarification": None,
            "reason": None,
        },
        # assets.summary with forbidden limit
        {
            "kind": "tools",
            "calls": [{"name": "assets.summary", "arguments": {"limit": 5}}],
            "clarification": None,
            "reason": None,
        },
        # assets.history without asset_code or mine
        {
            "kind": "tools",
            "calls": [{"name": "assets.history", "arguments": {}}],
            "clarification": None,
            "reason": None,
        },
        # assets.history with inverted dates
        {
            "kind": "tools",
            "calls": [
                {
                    "name": "assets.history",
                    "arguments": {
                        "mine": True,
                        "since": "2026-11-05T00:00:00Z",
                        "until": "2026-11-01T00:00:00Z",
                    },
                }
            ],
            "clarification": None,
            "reason": None,
        },
        # clarify with non-empty calls
        {
            "kind": "clarify",
            "calls": [{"name": "assets.search", "arguments": {}}],
            "clarification": "clarify?",
            "reason": None,
        },
        # handoff with invalid reason
        {
            "kind": "handoff",
            "calls": [],
            "clarification": None,
            "reason": "invalid_reason",
        },
    ],
)
def test_schema_violation_in_decision_raises_unavailable(
    mock_ai_server: MockAIServerState, invalid_decision: dict[str, Any]
) -> None:
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = json.dumps({"decision": invalid_decision}).encode("utf-8")

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "invalid planner decision" in str(exc_info.value).lower()


# 9. Response Size Limit (64 KiB)

def test_response_exceeding_64kib_raises_unavailable(mock_ai_server: MockAIServerState) -> None:
    large_clarification = "x" * (MAX_RESPONSE_BYTES + 100)
    raw_body = json.dumps({
        "decision": {
            "kind": "clarify",
            "calls": [],
            "clarification": large_clarification,
            "reason": None,
        }
    }).encode("utf-8")

    mock_ai_server.response_status = 200
    mock_ai_server.response_body = raw_body

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    assert "exceeded maximum allowed size" in str(exc_info.value).lower()


def test_response_just_under_64kib_accepted(mock_ai_server: MockAIServerState) -> None:
    valid_clarification = "Xin làm rõ yêu cầu tra cứu của bạn"
    raw_body = json.dumps({
        "decision": {
            "kind": "clarify",
            "calls": [],
            "clarification": valid_clarification,
            "reason": None,
        }
    }).encode("utf-8")

    mock_ai_server.response_status = 200
    mock_ai_server.response_body = raw_body

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        decision = plan_question(question="test")

    assert decision["kind"] == "clarify"
    assert decision["clarification"] == valid_clarification


# 10. Security: Redirect Handler Blocks Token Leaks

def test_redirect_handler_blocks_redirection(mock_ai_server: MockAIServerState) -> None:
    redirect_target = f"{mock_ai_server.base_url}/redirected-target"
    mock_ai_server.response_status = 302
    mock_ai_server.response_headers = {"Location": redirect_target}

    with override_settings(
        AI_WORKER_URL=mock_ai_server.base_url,
        AI_SERVICE_TOKEN="secret-token-must-not-leak",
    ):
        with pytest.raises(AssistantProviderUnavailable) as exc_info:
            plan_question(question="test")

    # Redirect target must never have been requested
    assert not mock_ai_server.redirect_target_hit
    assert "302" in str(exc_info.value)


# 11. Zero Retry Invariant

def test_zero_retry_on_failure(mock_ai_server: MockAIServerState) -> None:
    mock_ai_server.response_status = 503
    mock_ai_server.response_body = b'{"error": "Unavailable"}'

    with override_settings(AI_WORKER_URL=mock_ai_server.base_url):
        with pytest.raises(AssistantProviderUnavailable):
            plan_question(question="test retry")

    # Strict invariant: exactly 1 request made, zero retry attempts
    assert len(mock_ai_server.requests) == 1


# 12. URL Formatting Variations

def test_plan_question_url_formatting(mock_ai_server: MockAIServerState) -> None:
    expected_decision: PlannerDecision = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    mock_ai_server.response_status = 200
    mock_ai_server.response_body = json.dumps({"decision": expected_decision}).encode("utf-8")

    # Test when AI_WORKER_URL includes /api/assistant/plan
    full_url = f"{mock_ai_server.base_url}/api/assistant/plan"
    with override_settings(AI_WORKER_URL=full_url):
        decision = plan_question(question="test")
        assert decision["kind"] == "out_of_scope"

    # Test when AI_WORKER_URL has a trailing slash
    slash_url = f"{mock_ai_server.base_url}/"
    with override_settings(AI_WORKER_URL=slash_url):
        decision = plan_question(question="test")
        assert decision["kind"] == "out_of_scope"


# 13. Mock Opener / urlopen Compatibility Test

def test_plan_question_with_mock_opener() -> None:
    valid_decision: PlannerDecision = {
        "kind": "out_of_scope",
        "calls": [],
        "clarification": None,
        "reason": None,
    }
    response_bytes = json.dumps({"decision": valid_decision}).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_resp

    with override_settings(
        AI_WORKER_URL="http://127.0.0.1:8787",
        AI_SERVICE_TOKEN="explicit-test-token",
    ):
        with patch("apps.assistant.gateway.get_opener", return_value=mock_opener):
            result = plan_question(question="Hôm nay trời nắng không?")

    assert result == valid_decision
    assert mock_opener.open.call_count == 1
    call_args, call_kwargs = mock_opener.open.call_args
    req = call_args[0]
    assert req.headers.get("Authorization") == "Bearer explicit-test-token"
    assert req.headers.get("Content-type") == "application/json"
    assert req.headers.get("User-agent") == USER_AGENT
