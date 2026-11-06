from __future__ import annotations

import datetime
import json
import uuid
from typing import TYPE_CHECKING

from django.db import DatabaseError, IntegrityError, connections, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.ai.exceptions import ServiceUnavailable

from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.common.exceptions import ConflictError

if TYPE_CHECKING:
    from apps.assistant.contracts import Evidence, RoutingTarget
    from apps.authentication.models import User

MAX_ANSWER_LENGTH = 6000
TRUNCATION_NOTE = "\n\n[Nội dung đã được rút gọn do vượt quá giới hạn độ dài]"

TERMINAL_OUTCOMES = (
    ChatTurn.Outcome.ANSWERED,
    ChatTurn.Outcome.CLARIFY,
    ChatTurn.Outcome.ESCALATED,
    ChatTurn.Outcome.RESTRICTED,
    ChatTurn.Outcome.OUT_OF_SCOPE,
    ChatTurn.Outcome.ERROR,
)


def _is_d1(using: str | None = None) -> bool:
    if using in ("cloudflare_d1", "d1"):
        return True
    try:
        conn = connections[using or "default"]
    except Exception:
        return False
    if getattr(conn, "vendor", None) == "postgresql":
        return False
    raw_conn = getattr(conn, "connection", None)
    return bool(
        getattr(conn, "vendor", None) in ("cloudflare_d1", "d1")
        or hasattr(conn, "client")
        or getattr(raw_conn, "client", None) is not None
    )


def expire_stale_turn(
    *,
    turn: ChatTurn,
    now: datetime.datetime | None = None,
    using: str | None = None,
) -> ChatTurn:
    """If turn is PROCESSING and older than 180s, expire it to ERROR."""
    if turn.outcome == ChatTurn.Outcome.PROCESSING:
        current_time = now or timezone.now()
        turn_created = turn.created_at
        if timezone.is_aware(current_time) and timezone.is_naive(turn_created):
            turn_created = timezone.make_aware(turn_created)
        elif timezone.is_naive(current_time) and timezone.is_aware(turn_created):
            current_time = timezone.make_aware(current_time)

        if (current_time - turn_created).total_seconds() > 180:
            updated = (
                ChatTurn.objects.using(using)
                .filter(id=turn.id, outcome=ChatTurn.Outcome.PROCESSING)
                .update(
                    outcome=ChatTurn.Outcome.ERROR,
                    answer="Yêu cầu đã quá thời gian xử lý.",
                    updated_at=current_time,
                )
            )
            if updated > 0:
                turn.outcome = ChatTurn.Outcome.ERROR
                turn.answer = "Yêu cầu đã quá thời gian xử lý."
                turn.updated_at = current_time
            else:
                turn.refresh_from_db()
    return turn


def reserve_turn(
    *,
    actor: User,
    conversation: Conversation,
    client_request_id: uuid.UUID | str,
    question: str,
    using: str | None = None,
) -> tuple[ChatTurn, bool]:
    """Reserve a new PROCESSING turn or return idempotent replay for identical request.

    Scoped strictly to the specified conversation to match unique(conversation, client_request_id).
    Returns (turn, is_new).
    """
    if isinstance(client_request_id, str):
        client_request_id = uuid.UUID(client_request_id)

    # 1. Ownership check
    if conversation.owner_id != actor.id:
        raise PermissionDenied("Không có quyền truy cập cuộc trò chuyện.")

    # 2. Scope snapshot check
    if (
        actor.role != conversation.role_snapshot
        or actor.department_id != conversation.department_id_snapshot
    ):
        raise ConflictError("CONVERSATION_SCOPE_CHANGED")

    # 3. Check existing client_request_id within THIS conversation
    existing_turn = (
        ChatTurn.objects.using(using)
        .filter(conversation=conversation, client_request_id=client_request_id)
        .first()
    )
    if existing_turn is not None:
        if existing_turn.question == question:
            existing_turn = expire_stale_turn(turn=existing_turn, using=using)
            return existing_turn, False
        raise ConflictError("CLIENT_REQUEST_ID_CONFLICT")

    # 4. Clean up / check active processing turn in conversation
    processing_turn = (
        ChatTurn.objects.using(using)
        .filter(conversation=conversation, outcome=ChatTurn.Outcome.PROCESSING)
        .first()
    )
    if processing_turn is not None:
        processing_turn = expire_stale_turn(turn=processing_turn, using=using)
        if processing_turn.outcome == ChatTurn.Outcome.PROCESSING:
            raise ConflictError("CONVERSATION_BUSY")

    # 5. Question validation
    if not question or not question.strip():
        raise ValidationError("Câu hỏi không được để trống.")
    if len(question) > 1000:
        raise ValidationError("Câu hỏi không được vượt quá 1000 ký tự.")

    try:
        with transaction.atomic(using=using):
            turn = ChatTurn.objects.using(using).create(
                conversation=conversation,
                client_request_id=client_request_id,
                question=question,
                outcome=ChatTurn.Outcome.PROCESSING,
            )
            return turn, True
    except IntegrityError:
        existing = (
            ChatTurn.objects.using(using)
            .filter(conversation=conversation, client_request_id=client_request_id)
            .first()
        )
        if existing:
            if existing.question == question:
                return existing, False
            raise ConflictError("CLIENT_REQUEST_ID_CONFLICT")
        raise ConflictError("CONVERSATION_BUSY")


def finalize_turn(
    *,
    actor: User,
    turn: ChatTurn,
    outcome: str,
    answer: str,
    sources: list[Evidence],
    routing: RoutingTarget | None = None,
    using: str | None = None,
) -> ChatTurn:
    """Atomically finalize a PROCESSING turn to its terminal outcome and answer."""
    # Point 5: Only terminal outcomes are accepted; PROCESSING must be rejected
    if outcome not in TERMINAL_OUTCOMES or outcome == ChatTurn.Outcome.PROCESSING:
        raise ValidationError(f"Outcome không hợp lệ: {outcome}. Phải là trạng thái kết thúc (terminal outcome).")

    # Point 4: Ownership and scope snapshot check before mutating
    conv = turn.conversation
    if conv.owner_id != actor.id:
        raise PermissionDenied("Không có quyền truy cập lượt hỏi.")
    if (
        actor.role != conv.role_snapshot
        or actor.department_id != conv.department_id_snapshot
    ):
        raise ConflictError("CONVERSATION_SCOPE_CHANGED")

    if _is_d1(using):
        from assethub_api.backends.d1 import d1_batch

        turn_qs = ChatTurn.objects.using(using).select_related("conversation")
        curr_turn = turn_qs.filter(id=turn.id).first()
        if curr_turn is None:
            raise NotFound("Không tìm thấy lượt hỏi.")
        if curr_turn.conversation.owner_id != actor.id:
            raise PermissionDenied("Không có quyền truy cập lượt hỏi.")
        if (
            actor.role != curr_turn.conversation.role_snapshot
            or actor.department_id != curr_turn.conversation.department_id_snapshot
        ):
            raise ConflictError("CONVERSATION_SCOPE_CHANGED")
        if curr_turn.outcome != ChatTurn.Outcome.PROCESSING:
            raise ConflictError("TURN_STATE_CONFLICT")

        if len(answer) > MAX_ANSWER_LENGTH:
            answer = answer[: MAX_ANSWER_LENGTH - len(TRUNCATION_NOTE)] + TRUNCATION_NOTE

        now = timezone.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        turn_id_hex = turn.id.hex if hasattr(turn.id, "hex") else str(turn.id).replace("-", "")

        stmts: list[tuple[str, list[object]]] = [
            (
                "UPDATE assistant_chatturn SET outcome = %s, answer = %s, sources = %s, updated_at = %s WHERE id = %s AND outcome = 'PROCESSING'",
                [outcome, answer, json.dumps(sources), now_str, turn_id_hex],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", []),
        ]

        ticket_id = None
        if outcome == ChatTurn.Outcome.ESCALATED:
            from apps.assistant.support import build_question_snapshot, resolve_support_target

            if not SupportTicket.objects.using(using).filter(turn=curr_turn).exists():
                target = routing or resolve_support_target(actor, using=using)
                snapshot = build_question_snapshot(curr_turn, using=using)
                ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"assethub-assistant-ticket:{curr_turn.id}")
                ticket_id_hex = ticket_id.hex if hasattr(ticket_id, "hex") else str(ticket_id).replace("-", "")
                target_dept = target.get("department_id")
                stmts.append((
                    "INSERT INTO assistant_supportticket (id, turn_id, target_kind, target_department_id, question_snapshot, status, version, reply, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'OPEN', 1, '', %s, %s)",
                    [ticket_id_hex, turn_id_hex, target["kind"], target_dept, snapshot, now_str, now_str],
                ))

        idempotency_key = f"assistant-finalize:{turn.id}"
        try:
            d1_batch(stmts, using=using or "default", idempotency_key=idempotency_key)
        except DatabaseError as e:
            err_msg = str(e)
            if (
                "_assert_guard" in err_msg
                or "CHECK constraint failed" in err_msg
                or "rows_affected" in err_msg
                or "Constraint Conflict" in err_msg
            ):
                raise ConflictError("TURN_STATE_CONFLICT") from e
            # Readback recovery
            try:
                curr_turn.refresh_from_db()
                if curr_turn.outcome == outcome:
                    if outcome == ChatTurn.Outcome.ESCALATED and ticket_id:
                        if SupportTicket.objects.using(using).filter(id=ticket_id).exists():
                            return curr_turn
                    else:
                        return curr_turn
            except Exception:
                pass
            raise ServiceUnavailable("Lỗi kết nối cơ sở dữ liệu khi hoàn tất lượt hỏi.") from e

        curr_turn.refresh_from_db()
        return curr_turn

    with transaction.atomic(using=using):
        locked_turn = (
            ChatTurn.objects.using(using)
            .select_for_update()
            .select_related("conversation")
            .filter(id=turn.id)
            .first()
        )
        if locked_turn is None:
            raise NotFound("Không tìm thấy lượt hỏi.")
        if locked_turn.conversation.owner_id != actor.id:
            raise PermissionDenied("Không có quyền truy cập lượt hỏi.")
        if (
            actor.role != locked_turn.conversation.role_snapshot
            or actor.department_id != locked_turn.conversation.department_id_snapshot
        ):
            raise ConflictError("CONVERSATION_SCOPE_CHANGED")
        if locked_turn.outcome != ChatTurn.Outcome.PROCESSING:
            raise ConflictError("TURN_STATE_CONFLICT")

        if len(answer) > MAX_ANSWER_LENGTH:
            answer = answer[: MAX_ANSWER_LENGTH - len(TRUNCATION_NOTE)] + TRUNCATION_NOTE

        locked_turn.outcome = outcome
        locked_turn.answer = answer
        locked_turn.sources = sources
        locked_turn.save(update_fields=["outcome", "answer", "sources", "updated_at"])

        if outcome == ChatTurn.Outcome.ESCALATED:
            from apps.assistant.support import build_question_snapshot, resolve_support_target

            if not SupportTicket.objects.using(using).filter(turn=locked_turn).exists():
                target = routing or resolve_support_target(actor, using=using)
                snapshot = build_question_snapshot(locked_turn, using=using)
                ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"assethub-assistant-ticket:{locked_turn.id}")
                SupportTicket.objects.using(using).create(
                    id=ticket_id,
                    turn=locked_turn,
                    target_kind=target["kind"],
                    target_department_id=target.get("department_id"),
                    question_snapshot=snapshot,
                    status=SupportTicket.Status.OPEN,
                    version=1,
                )
        return locked_turn
