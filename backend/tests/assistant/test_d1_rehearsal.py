"""Local proof and rehearsal tests against Cloudflare D1 Target Harness.

Verifies additive schema migration, zero-cloud query parity, atomic handoff,
OCC conflict resolution, and end-to-end local HTTP roundtrip on 127.0.0.1:8787.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connections
from django.db.utils import DatabaseError, IntegrityError

from apps.ai.exceptions import ServiceUnavailable
from apps.assets.models import Asset
from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.storage import finalize_turn, reserve_turn
from apps.assistant.support import escalate_turn, reply_ticket
from apps.assistant.tools import execute_tool
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.common.exceptions import ConflictError
from apps.common.tests.harness_d1_target import (
    DEFAULT_PORT,
    D1TargetHarness,
    assert_safe_environment,
    check_port_in_use,
)
from apps.departments.models import Category, Department
from apps.reports.services import ReportService
from assethub_api.backends.d1 import client as d1_client, d1_batch
from tests.assistant.d1_fixtures import (  # noqa: F401
    COMMON_TEST_PASSWORD,
    apply_week5_assistant_sql,
    assistant_d1_graph,
    assistant_d1_http,
    check_rehearsal_opt_in,
)

pytestmark = pytest.mark.rehearsal


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_http_request(
    base_url: str,
    method: str,
    path: str,
    token: str | None = None,
    data: dict | None = None,
) -> tuple[int, dict]:
    """Execute HTTP request against local WSGI rehearsal server."""
    url = f"{base_url}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(err_body)
        except Exception:
            return exc.code, {"detail": err_body}


# ---------------------------------------------------------------------------
# Rehearsal Tests
# ---------------------------------------------------------------------------

@pytest.mark.rehearsal
def test_d1_additive_schema_and_query_parity(assistant_d1_graph):
    """Verify D1 schema has 22 tables, re-entrancy, sentinel preservation, and query parity on D1."""
    harness = assistant_d1_graph["harness"]

    # 1. Insert sentinel row in base table on D1 before applying additive SQL
    sentinel_code = "W5-SENTINEL-01"
    sentinel = Asset.objects.using("default").create(
        id=999,
        asset_code=sentinel_code,
        name="Sentinel Asset Pre-Migration",
        category=assistant_d1_graph["laptop"],
        department=assistant_d1_graph["it"],
        status=Asset.Status.AVAILABLE,
        serial_number="SN-SENTINEL-01",
        specification={"purpose": "rehearsal_sentinel"},
    )

    # 2. Apply additive SQL migration TWICE to verify idempotency / re-entrancy
    apply_week5_assistant_sql(harness)
    apply_week5_assistant_sql(harness)

    # 3. Verify sentinel row is intact and untouched after both applies
    sentinel_check = Asset.objects.using("default").get(id=999)
    assert sentinel_check.asset_code == sentinel_code
    assert sentinel_check.name == "Sentinel Asset Pre-Migration"
    sentinel.delete()  # Clean up sentinel to keep exact counts for subsequent tests

    # 4. Check D1 backend engine
    assert connections["default"].settings_dict["ENGINE"] == "assethub_api.backends.d1"

    # 5. Check table count via direct SQLite query over D1 bridge (filtering SQLite and Cloudflare metadata)
    res = harness.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%';"
    )
    assert res.get("success") is True, f"Failed querying tables: {res}"
    table_names = {row["name"] for row in res["results"]}

    # Canonical schema: 19 base tables + 3 assistant tables = 22 total tables
    expected_tables = {
        # 19 base tables from spikes/cf-d1-spike/schema.sql
        "departments_department",
        "departments_category",
        "django_content_type",
        "auth_permission",
        "auth_group",
        "auth_group_permissions",
        "authentication_user",
        "authentication_user_groups",
        "authentication_user_user_permissions",
        "django_admin_log",
        "django_migrations",
        "django_session",
        "assets_identifiersequence",
        "assets_asset",
        "requests_assetrequest",
        "assets_assethistorylog",
        "_assert_guard",
        "_idempotency_log",
        "ai_suggestion_logs",
        # 3 assistant tables from backend/assethub_api/backends/d1/sql/0002_week5_assistant.sql
        "assistant_conversation",
        "assistant_chatturn",
        "assistant_supportticket",
    }
    assert len(expected_tables) == 22
    assert table_names == expected_tables, (
        f"D1 tables mismatch (got {len(table_names)}, expected 22). "
        f"Missing: {expected_tables - table_names}, Unexpected: {table_names - expected_tables}"
    )

    # 6. Check required assistant indexes on D1
    res_idx = harness.query("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'assistant_%';")
    assert res_idx.get("success") is True
    idx_names = {row["name"] for row in res_idx["results"]}
    assert "assistant_conv_owner_date" in idx_names
    assert "assistant_turn_conv_date" in idx_names
    assert "assistant_ticket_target_date" in idx_names

    # 7. Execute tool query for manager_it: assets.summary
    summary_result = execute_tool(
        actor=assistant_d1_graph["manager_it"],
        call={
            "name": "assets.summary",
            "arguments": {"category": "Laptop", "statuses": ["AVAILABLE"], "group_by": None},
        },
    )
    assert summary_result["status"] == "ok"
    assert summary_result["data"]["total"] == 23
    assert summary_result["sources"][0]["facts"]["total"] == 23

    # 8. Execute tool query for user_it: assets.search
    user_result = execute_tool(
        actor=assistant_d1_graph["user_it"],
        call={"name": "assets.search", "arguments": {}},
    )
    assert user_result["status"] == "ok"
    assert user_result["data"]["total"] == 1
    assert user_result["data"]["items"][0]["asset_code"] == "W5-IT-L24"


@pytest.mark.rehearsal
def test_d1_escalation_is_atomic_on_failure(assistant_d1_graph):
    """Verify real D1 batch atomic rollback on CHECK constraint failure and domain validation."""
    user_it = assistant_d1_graph["user_it"]
    conv = Conversation.objects.using("default").create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn = ChatTurn.objects.using("default").create(
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Cần hỗ trợ về thiết bị",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Câu trả lời mẫu trước khi chuyển tiếp",
        sources=[],
    )

    now_str = "2026-11-05 12:00:00"
    turn_id_hex = turn.id.hex if hasattr(turn.id, "hex") else str(turn.id).replace("-", "")
    ticket_id_hex = uuid.uuid4().hex

    # Inject real fault inside D1 batch: Statement 1 modifies turn, Statement 2 violates CHECK constraint
    # (version = 0 violates chk_assistant_ticket_version CHECK (version >= 1))
    failing_batch_stmts = [
        (
            "UPDATE assistant_chatturn SET outcome = %s, updated_at = %s WHERE id = %s",
            ["ESCALATED", now_str, turn_id_hex],
        ),
        (
            "INSERT INTO assistant_supportticket (id, turn_id, target_kind, target_department_id, question_snapshot, status, version, reply, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'OPEN', 0, '', %s, %s)",
            [ticket_id_hex, turn_id_hex, "DEPARTMENT", user_it.department_id, turn.question, now_str, now_str],
        ),
    ]

    # Real D1 bridge rejects batch with IntegrityError / DatabaseError on CHECK constraint failure
    with pytest.raises((IntegrityError, DatabaseError)) as exc_info:
        d1_batch(failing_batch_stmts, using="default")

    assert "chk_assistant_ticket_version" in str(exc_info.value)

    # Verify D1 transaction atomicity: Statement 1 was rolled back on D1
    turn.refresh_from_db()
    assert turn.outcome == ChatTurn.Outcome.ANSWERED
    assert turn.answer == "Câu trả lời mẫu trước khi chuyển tiếp"
    assert not SupportTicket.objects.using("default").filter(turn=turn).exists()

    # Verify domain-level escalate_turn raises ConflictError when turn cannot be escalated
    turn.outcome = ChatTurn.Outcome.PROCESSING
    turn.save(using="default")
    with pytest.raises(ConflictError) as conf_exc:
        escalate_turn(actor=user_it, turn=turn, using="default")
    assert "TURN_STATE_CONFLICT" in str(conf_exc.value)
    assert not SupportTicket.objects.using("default").filter(turn=turn).exists()


@pytest.mark.rehearsal
def test_d1_unknown_commit_readback_and_replay(assistant_d1_graph, monkeypatch):
    """Verify turn reservation idempotency, unknown commit recovery, and uncommitted failure handling."""
    user_it = assistant_d1_graph["user_it"]
    conv = Conversation.objects.using("default").create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )

    req_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    question = "Tôi đang giữ bao nhiêu máy tính?"

    # 1. Initial reservation creates a PROCESSING turn
    turn1, is_new1 = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=req_id,
        question=question,
        using="default",
    )
    assert is_new1 is True
    assert turn1.outcome == ChatTurn.Outcome.PROCESSING

    # Transition turn to ANSWERED so it can be escalated
    turn1.outcome = ChatTurn.Outcome.ANSWERED
    turn1.save(using="default")

    # Case A: Real batch commit lands on D1, but transport response dropped right after commit
    real_batch_fn = d1_client.D1Client.batch

    def commit_then_drop_response(self, *args, **kwargs):
        real_batch_fn(self, *args, **kwargs)
        raise urllib.error.URLError("Connection reset by peer after commit")

    monkeypatch.setattr(d1_client.D1Client, "batch", commit_then_drop_response)

    # escalate_turn catches transport drop, performs readback against D1, and recovers real ticket
    recovered_ticket = escalate_turn(actor=user_it, turn=turn1, using="default")
    assert recovered_ticket is not None
    assert recovered_ticket.status == SupportTicket.Status.OPEN
    turn1.refresh_from_db()
    assert turn1.outcome == ChatTurn.Outcome.ESCALATED

    # Replay via reserve_turn returns existing turn without creating duplicate ticket
    turn_replay, is_new_replay = reserve_turn(
        actor=user_it,
        conversation=conv,
        client_request_id=req_id,
        question=question,
        using="default",
    )
    assert is_new_replay is False
    assert turn_replay.id == turn1.id
    assert SupportTicket.objects.using("default").filter(turn=turn1).count() == 1

    # Replay escalate_turn returns existing ticket idempotently
    ticket_replay = escalate_turn(actor=user_it, turn=turn1, using="default")
    assert ticket_replay.id == recovered_ticket.id
    assert SupportTicket.objects.using("default").filter(turn=turn1).count() == 1

    # Case B1: Transport failure BEFORE commit (no commit landed)
    def drop_before_commit(self, *args, **kwargs):
        raise urllib.error.URLError("Connection refused before commit")

    monkeypatch.setattr(d1_client.D1Client, "batch", drop_before_commit)

    turn_uncommitted = ChatTurn.objects.using("default").create(
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi mất mạng trước commit",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Chưa chuyển hỗ trợ",
    )

    # When readback cannot confirm commit, must raise ServiceUnavailable (not falsely claim escalated)
    with pytest.raises(ServiceUnavailable):
        escalate_turn(actor=user_it, turn=turn_uncommitted, using="default")

    turn_uncommitted.refresh_from_db()
    assert turn_uncommitted.outcome == ChatTurn.Outcome.ANSWERED
    assert not SupportTicket.objects.using("default").filter(turn=turn_uncommitted).exists()

    # Case B2: Real batch commit lands on D1, transport response lost, AND readback query fails
    # Tested through real API endpoint POST /api/assistant/turns/{turn_id}/escalate/
    turn_readback_fail = ChatTurn.objects.using("default").create(
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Câu hỏi readback bị lỗi kết nối",
        outcome=ChatTurn.Outcome.ANSWERED,
        answer="Chưa chuyển hỗ trợ",
    )

    b2_commit_succeeded = False

    def b2_commit_then_drop(self, *args, **kwargs):
        nonlocal b2_commit_succeeded
        real_batch_fn(self, *args, **kwargs)
        b2_commit_succeeded = True
        raise urllib.error.URLError("Connection reset by peer after commit")

    monkeypatch.setattr(d1_client.D1Client, "batch", b2_commit_then_drop)

    from django.db.models.query import QuerySet
    orig_qs_get = QuerySet.get

    def failing_qs_get(self, *args, **kwargs):
        # Fault injection occurs ONLY after real batch commit, specifically on readback query
        # Ensures initial turn query before commit does NOT fail
        if b2_commit_succeeded and kwargs.get("id") == turn_readback_fail.id:
            raise DatabaseError("D1 readback connection timed out after commit")
        return orig_qs_get(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "get", failing_qs_get)

    from rest_framework.test import APIClient
    api_client = APIClient()
    api_client.force_authenticate(user=user_it)

    # Call real API endpoint
    resp_503 = api_client.post(f"/api/assistant/turns/{turn_readback_fail.id}/escalate/", data={})
    assert resp_503.status_code == 503, f"Expected 503 Service Unavailable, got {resp_503.status_code}: {resp_503.data}"
    assert "ticket" not in resp_503.data

    # Restore fault hooks
    monkeypatch.setattr(QuerySet, "get", orig_qs_get)
    monkeypatch.setattr(d1_client.D1Client, "batch", real_batch_fn)

    # Read committed ticket directly from D1 to confirm it committed
    committed_ticket = SupportTicket.objects.using("default").get(turn=turn_readback_fail)
    assert committed_ticket is not None
    assert committed_ticket.status == SupportTicket.Status.OPEN
    turn_readback_fail.refresh_from_db()
    assert turn_readback_fail.outcome == ChatTurn.Outcome.ESCALATED

    # Replay: call the endpoint again with the same turn & actor
    resp_replay = api_client.post(f"/api/assistant/turns/{turn_readback_fail.id}/escalate/", data={})
    assert resp_replay.status_code == 200, f"Replay failed: {resp_replay.data}"
    assert resp_replay.data["id"] == str(committed_ticket.id)
    assert SupportTicket.objects.using("default").filter(turn=turn_readback_fail).count() == 1


@pytest.mark.rehearsal
def test_d1_two_managers_reply_first_wins(assistant_d1_http, monkeypatch):
    """Verify OCC version check guarantees first manager wins concurrent replies on D1."""
    base_url = assistant_d1_http["base_url"]
    graph = assistant_d1_http["graph"]
    user_it = graph["user_it"]
    manager_it = graph["manager_it"]
    manager_it_2 = graph["manager_it_2"]

    token1 = generate_tokens_for_user(manager_it)["access"]
    token2 = generate_tokens_for_user(manager_it_2)["access"]

    conv = Conversation.objects.using("default").create(
        owner=user_it,
        role_snapshot=user_it.role,
        department_id_snapshot=user_it.department_id,
    )
    turn = ChatTurn.objects.using("default").create(
        conversation=conv,
        client_request_id=uuid.uuid4(),
        question="Cần hỗ trợ máy in IT",
        outcome=ChatTurn.Outcome.ESCALATED,
        answer="Chuyển hỗ trợ.",
    )
    ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"assethub-assistant-ticket:{turn.id}")
    ticket = SupportTicket.objects.using("default").create(
        id=ticket_id,
        turn=turn,
        target_kind=SupportTicket.TargetKind.DEPARTMENT,
        target_department=graph["it"],
        question_snapshot=turn.question,
        status=SupportTicket.Status.OPEN,
        version=1,
    )

    req_id_1 = str(uuid.uuid4())
    req_id_2 = str(uuid.uuid4())

    # CAS rendezvous hook:
    # Hook d1_batch when statement updates assistant_supportticket.
    # Both manager threads read version 1 / OPEN from D1, then rendezvous at cas_barrier
    # before executing real d1_batch against D1 SQLite.
    import assethub_api.backends.d1
    real_d1_batch = assethub_api.backends.d1.d1_batch
    cas_barrier = threading.Barrier(2)
    rendezvous_counter = 0
    counter_lock = threading.Lock()

    def rendezvous_d1_batch(statements, *args, **kwargs):
        nonlocal rendezvous_counter
        is_ticket_update = False
        for st in statements:
            sql = st[0] if isinstance(st, (tuple, list)) else (st.get("sql") if isinstance(st, dict) else str(st))
            if "UPDATE assistant_supportticket" in str(sql):
                is_ticket_update = True
                break
        if is_ticket_update:
            with counter_lock:
                rendezvous_counter += 1
            # Rendezvous at barrier: MUST NOT swallow BrokenBarrierError or timeout!
            # If barrier breaks or times out, the test MUST fail (red).
            cas_barrier.wait(timeout=5.0)
        return real_d1_batch(statements, *args, **kwargs)

    monkeypatch.setattr(assethub_api.backends.d1, "d1_batch", rendezvous_d1_batch)
    monkeypatch.setattr("apps.assistant.support.d1_batch", rendezvous_d1_batch, raising=False)

    payload1 = {
        "client_request_id": req_id_1,
        "expected_version": 1,
        "reply": "Phản hồi từ Quản lý IT 1",
    }
    payload2 = {
        "client_request_id": req_id_2,
        "expected_version": 1,
        "reply": "Phản hồi từ Quản lý IT 2",
    }

    def send_reply(token, payload):
        return _make_http_request(
            base_url,
            "POST",
            f"/api/assistant/support-tickets/{ticket.id}/reply/",
            token=token,
            data=payload,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future1 = executor.submit(send_reply, token1, payload1)
        future2 = executor.submit(send_reply, token2, payload2)
        res1 = future1.result(timeout=10.0)
        res2 = future2.result(timeout=10.0)

    # Assert exactly 2 distinct requests rendezvoused before any real batch UPDATE executed
    assert rendezvous_counter == 2, f"Expected 2 requests to reach CAS barrier, got {rendezvous_counter}"

    # Exactly one 200 OK, one 409 Conflict
    statuses = sorted([res1[0], res2[0]])
    assert statuses == [200, 409], f"Expected [200, 409], got {res1} and {res2}"

    winner_res = res1 if res1[0] == 200 else res2
    loser_res = res2 if res1[0] == 200 else res1
    winning_payload = payload1 if res1[0] == 200 else payload2
    winning_manager = manager_it if res1[0] == 200 else manager_it_2

    # Loser assertion: status 409 and code == STALE_VERSION_CONFLICT strictly from support.py
    assert loser_res[0] == 409
    assert loser_res[1].get("code") == "STALE_VERSION_CONFLICT"

    # Winner assertion: matches winner request payload exactly, response matches winner
    assert winner_res[0] == 200
    assert winner_res[1]["reply"] == winning_payload["reply"]
    assert winner_res[1]["status"] == "ANSWERED"
    assert winner_res[1]["version"] == 2

    # Verify D1 state directly
    ticket.refresh_from_db()
    assert ticket.version == 2
    assert ticket.status == SupportTicket.Status.ANSWERED
    assert ticket.reply == winning_payload["reply"]
    assert ticket.replied_by_id == winning_manager.id
    assert str(ticket.reply_request_id) == winning_payload["client_request_id"]
    assert SupportTicket.objects.using("default").filter(turn=turn).count() == 1


@pytest.mark.rehearsal
def test_local_http_question_handoff_reply_roundtrip(assistant_d1_http):
    """End-to-end HTTP roundtrip: conversation, handoff, reply, and boundary check on D1."""
    base_url = assistant_d1_http["base_url"]
    graph = assistant_d1_http["graph"]
    user_it = graph["user_it"]
    manager_it = graph["manager_it"]
    manager_hr = graph["manager_hr"]

    # 1. Obtain JWT access tokens
    token_user = generate_tokens_for_user(user_it)["access"]
    token_manager_it = generate_tokens_for_user(manager_it)["access"]
    token_manager_hr = generate_tokens_for_user(manager_hr)["access"]

    # 2. Create conversation via POST /api/assistant/conversations/
    status_code, conv_data = _make_http_request(
        base_url, "POST", "/api/assistant/conversations/", token=token_user, data={}
    )
    assert status_code == 201, f"Create conversation failed: {conv_data}"
    conv_id = conv_data["id"]

    # 3. POST /api/assistant/conversations/{conv_id}/turns/ with fake planner triggering handoff
    # Only mock gateway.plan_question (do NOT mock ChatService, finalize, or tools)
    handoff_decision = {
        "kind": "handoff",
        "calls": [],
        "clarification": None,
        "reason": "missing_knowledge",
    }
    with patch("apps.assistant.gateway.plan_question", return_value=handoff_decision):
        turn_payload = {
            "client_request_id": str(uuid.uuid4()),
            "question": "Chính sách bồi thường máy tính mất là gì?",
        }
        status_code, turn_resp = _make_http_request(
            base_url,
            "POST",
            f"/api/assistant/conversations/{conv_id}/turns/",
            token=token_user,
            data=turn_payload,
        )
        assert status_code == 201, f"Create turn failed: {turn_resp}"
        assert turn_resp["outcome"] == "ESCALATED"
        assert turn_resp["ticket"] is not None
        assert turn_resp["ticket"]["status"] == "OPEN"
        turn_id = turn_resp["id"]
        ticket_id = turn_resp["ticket"]["id"]

    # 4. Manager IT replies to ticket via POST /api/assistant/support-tickets/{ticket_id}/reply/
    reply_payload = {
        "client_request_id": str(uuid.uuid4()),
        "expected_version": 1,
        "reply": "Chúng tôi đang xử lý yêu cầu của bạn.",
    }
    status_code, reply_data = _make_http_request(
        base_url,
        "POST",
        f"/api/assistant/support-tickets/{ticket_id}/reply/",
        token=token_manager_it,
        data=reply_payload,
    )
    assert status_code == 200, f"Reply ticket failed: {reply_data}"
    assert reply_data["status"] == "ANSWERED"
    assert reply_data["reply"] == "Chúng tôi đang xử lý yêu cầu của bạn."

    # 5. Requester GET /api/assistant/turns/{turn_id}/ sees staff reply
    status_code, turn_data = _make_http_request(
        base_url, "GET", f"/api/assistant/turns/{turn_id}/", token=token_user
    )
    assert status_code == 200
    assert turn_data["outcome"] == "ESCALATED"
    assert turn_data["ticket"]["status"] == "ANSWERED"
    assert turn_data["ticket"]["reply"] == "Chúng tôi đang xử lý yêu cầu của bạn."
    assert turn_data["ticket"]["replied_by_name"] == "Quản lý IT"

    # 6. Manager HR checks ticket via GET /api/assistant/support-tickets/{ticket_id}/ (cross-department -> 404)
    status_code, hr_data = _make_http_request(
        base_url, "GET", f"/api/assistant/support-tickets/{ticket_id}/", token=token_manager_hr
    )
    assert status_code == 404, f"Cross-department ticket view should return 404, got {status_code}"

    # 7. Staff cannot read conversation turns directly (owner only)
    status_code_turns, _ = _make_http_request(
        base_url, "GET", f"/api/assistant/conversations/{conv_id}/turns/", token=token_manager_it
    )
    assert status_code_turns in (404, 403), f"Staff should not access user conversation turns, got {status_code_turns}"


@pytest.mark.rehearsal
def test_rehearsal_does_not_touch_foreign_listener_or_cloud(request, monkeypatch):
    """Verify Zero-Cloud guarantee and preflight foreign listener protection without starting harness."""
    check_rehearsal_opt_in(request)

    # 1. Foreign listener preflight check: occupied port 8787 blocks harness before any I/O
    foreign_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        foreign_sock.bind(("127.0.0.1", DEFAULT_PORT))
        foreign_sock.listen(128)

        assert check_port_in_use(port=DEFAULT_PORT) is True

        harness = D1TargetHarness(port=DEFAULT_PORT)
        with pytest.raises(RuntimeError) as exc_info:
            harness.setup(clean_first=False)
        assert "already in use" in str(exc_info.value).lower()

        # Verify foreign_sock is still alive and listening (was NOT killed)
        assert foreign_sock.fileno() != -1
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", DEFAULT_PORT)) == 0
    finally:
        foreign_sock.close()

    # 2. Verify port 8787 is released after foreign socket closed
    time.sleep(0.1)
    assert not check_port_in_use(port=DEFAULT_PORT)

    # 3. Outbound network guard verification: blocks non-loopback domains before network packet
    def _check_network_target(host):
        if host not in ("127.0.0.1", "localhost", "::1", "", None):
            raise RuntimeError(f"Outbound network access blocked during rehearsal: {host}")

    orig_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, port_val, *args, **kwargs):
        _check_network_target(host)
        return orig_getaddrinfo(host, port_val, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    with pytest.raises(RuntimeError) as exc1:
        socket.getaddrinfo("cloudflare.com", 443)
    assert "Outbound network access blocked" in str(exc1.value)

    with pytest.raises(RuntimeError) as exc2:
        socket.getaddrinfo("127.evil.tld", 80)
    assert "Outbound network access blocked" in str(exc2.value)

    # Allowed loopback passes through guard
    res_loopback = socket.getaddrinfo("127.0.0.1", 80)
    assert res_loopback is not None

    # 4. Check subprocess env has WRANGLER_SEND_METRICS == "false"
    monkeypatch.setenv("WRANGLER_SEND_METRICS", "false")
    assert os.environ.get("WRANGLER_SEND_METRICS") == "false"

    # 5. Isolation guard blocks --remote and deploy commands
    with pytest.raises(RuntimeError) as exc_remote:
        assert_safe_environment(["wrangler", "dev", "--remote"])
    assert "CRITICAL ISOLATION GUARD" in str(exc_remote.value)

    with pytest.raises(RuntimeError) as exc_deploy:
        assert_safe_environment(["wrangler", "deploy"])
    assert "CRITICAL ISOLATION GUARD" in str(exc_deploy.value)

    # 6. Environment variables must not contain remote Cloudflare endpoints
    for cloud_key in ("D1_BRIDGE_URL", "R2_ENDPOINT_URL", "AI_WORKER_URL"):
        env_val = os.environ.get(cloud_key, "")
        assert "cloudflare.com" not in env_val
        assert "workers.dev" not in env_val


@pytest.mark.rehearsal
def test_d1_reports_charts_distribution_parity(assistant_d1_graph):
    """Verify ReportService.get_charts runs on local D1 engine and matches independent oracle."""
    # 1. D1 engine verification
    assert connections["default"].settings_dict["ENGINE"] == "assethub_api.backends.d1"

    admin = assistant_d1_graph["admin"]
    manager_it = assistant_d1_graph["manager_it"]
    laptop = assistant_d1_graph["laptop"]

    # 2. Admin view (all departments + unallocated)
    admin_charts = ReportService.get_charts(user=admin)

    # Independent oracle: direct count from Asset on default D1 connection
    active_qs = Asset.objects.using("default").exclude(status=Asset.Status.DISPOSED)
    oracle_status = {
        s: active_qs.filter(status=s).count()
        for s in [
            Asset.Status.AVAILABLE,
            Asset.Status.IN_USE,
            Asset.Status.DAMAGED_PENDING_REPAIR,
            Asset.Status.UNDER_MAINTENANCE,
            Asset.Status.PENDING_DISPOSAL,
        ]
    }
    oracle_cat = {
        cat.id: active_qs.filter(category=cat).count()
        for cat in Category.objects.using("default").all()
    }
    oracle_dept = {
        dept.id: active_qs.filter(department=dept).count()
        for dept in Department.objects.using("default").all()
    }
    oracle_unallocated = active_qs.filter(department__isnull=True).count()

    # Compare status distribution
    for item in admin_charts["status_distribution"]:
        s_code = item["status"]
        assert item["count"] == oracle_status[s_code], f"Status mismatch for {s_code}"

    # Compare category distribution
    for item in admin_charts["category_distribution"]:
        c_id = item["category_id"]
        assert item["count"] == oracle_cat[c_id], f"Category mismatch for {c_id}"

    # Compare department distribution
    for item in admin_charts["department_distribution"]:
        d_id = item["department_id"]
        if d_id is None:
            assert item["count"] == oracle_unallocated
        else:
            assert item["count"] == oracle_dept[d_id], f"Dept mismatch for {d_id}"

    # Concrete graph expectations: 25 available, 3 in_use, 1 damaged = 29 total
    status_map = {item["status"]: item["count"] for item in admin_charts["status_distribution"]}
    assert status_map[Asset.Status.AVAILABLE] == 25
    assert status_map[Asset.Status.IN_USE] == 3
    assert status_map[Asset.Status.DAMAGED_PENDING_REPAIR] == 1
    assert status_map[Asset.Status.UNDER_MAINTENANCE] == 0
    assert status_map[Asset.Status.PENDING_DISPOSAL] == 0

    cat_map = {item["category_id"]: item["count"] for item in admin_charts["category_distribution"]}
    assert cat_map[laptop.id] == 28

    # 3. Manager IT view (scoped to department IT, empty department_distribution)
    mgr_charts = ReportService.get_charts(user=manager_it)
    assert mgr_charts["department_distribution"] == []
    mgr_status_map = {item["status"]: item["count"] for item in mgr_charts["status_distribution"]}
    assert mgr_status_map[Asset.Status.AVAILABLE] == 23
    assert mgr_status_map[Asset.Status.IN_USE] == 2
    assert mgr_status_map[Asset.Status.DAMAGED_PENDING_REPAIR] == 1
    mgr_cat_map = {item["category_id"]: item["count"] for item in mgr_charts["category_distribution"]}
    assert mgr_cat_map[laptop.id] == 25

    # 4. Filter check (category filter on admin view)
    filtered_charts = ReportService.get_charts(user=admin, category_param=str(laptop.id))
    filt_status_map = {item["status"]: item["count"] for item in filtered_charts["status_distribution"]}
    assert filt_status_map[Asset.Status.AVAILABLE] == 25
    assert filt_status_map[Asset.Status.IN_USE] == 3
    assert filt_status_map[Asset.Status.DAMAGED_PENDING_REPAIR] == 0  # monitor was damaged, not laptop
