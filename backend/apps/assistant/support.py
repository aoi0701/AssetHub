from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.ai.exceptions import ServiceUnavailable

from apps.assistant.models import ChatTurn, SupportTicket
from apps.assistant.storage import _is_d1
from apps.authentication.models import User
from apps.common.exceptions import ConflictError

if TYPE_CHECKING:
    from apps.assistant.contracts import RoutingTarget

MAX_REPLY_LENGTH = 4000
MAX_SNAPSHOT_LENGTH = 3000

ESCALATABLE_OUTCOMES = (
    ChatTurn.Outcome.ANSWERED,
    ChatTurn.Outcome.CLARIFY,
    ChatTurn.Outcome.RESTRICTED,
    ChatTurn.Outcome.ERROR,
)


def resolve_support_target(actor: User, using: str | None = None) -> RoutingTarget:
    """Resolve routing target for handoff.

    Routes to actor's department if an active manager other than actor exists;
    otherwise falls back to ADMIN.
    """
    if actor is None or actor.department_id is None:
        return {"kind": "ADMIN", "department_id": None}

    has_active_other_manager = (
        User.objects.using(using)
        .filter(
            department_id=actor.department_id,
            role=User.Role.MANAGER,
            is_active=True,
        )
        .exclude(id=actor.id)
        .exists()
    )
    if has_active_other_manager:
        return {"kind": "DEPARTMENT", "department_id": actor.department_id}
    return {"kind": "ADMIN", "department_id": None}


def build_question_snapshot(turn: ChatTurn, using: str | None = None) -> str:
    """Build a conversation context snapshot comprising up to 2 prior questions and current question."""
    qs = ChatTurn.objects.using(using).filter(conversation_id=turn.conversation_id).exclude(id=turn.id)
    if turn.created_at:
        qs = qs.filter(created_at__lte=turn.created_at)
    prior_turns = list(qs.order_by("-created_at")[:2])
    prior_turns.reverse()

    questions = [t.question.strip() for t in prior_turns if t.question and t.question.strip()]
    if turn.question and turn.question.strip():
        questions.append(turn.question.strip())

    snapshot = "\n\n".join(questions)
    if len(snapshot) > MAX_SNAPSHOT_LENGTH:
        snapshot = snapshot[:MAX_SNAPSHOT_LENGTH]
    return snapshot or (turn.question[:MAX_SNAPSHOT_LENGTH] if turn.question else "Câu hỏi")


def escalate_turn(
    *,
    actor: User,
    turn: ChatTurn,
    using: str | None = None,
) -> SupportTicket:
    """Escalate a turn to a SupportTicket, ensuring idempotency and atomic handoff.

    Requires turn to be in an in-domain completed state (ANSWERED, CLARIFICATION_NEEDED) or ERROR.
    PROCESSING and OUT_OF_SCOPE turns cannot be escalated.
    Enforces conversation scope snapshot validation.
    """
    conv = turn.conversation

    # 1. Ownership check
    if conv.owner_id != actor.id:
        raise PermissionDenied("Không có quyền chuyển hỗ trợ cho lượt hỏi này.")

    # 2. Scope snapshot check (Point 4)
    if (
        actor.role != conv.role_snapshot
        or actor.department_id != conv.department_id_snapshot
    ):
        raise ConflictError("CONVERSATION_SCOPE_CHANGED")

    # 3. Idempotency check: return existing ticket if already created
    existing_ticket = SupportTicket.objects.using(using).filter(turn=turn).first()
    if existing_ticket is not None:
        return existing_ticket

    # 4. Turn outcome validation (Point 3: only in-domain completed or ERROR)
    if turn.outcome == ChatTurn.Outcome.PROCESSING:
        raise ConflictError("TURN_STATE_CONFLICT")
    if turn.outcome == ChatTurn.Outcome.OUT_OF_SCOPE:
        raise ConflictError("TURN_STATE_CONFLICT")
    if turn.outcome not in ESCALATABLE_OUTCOMES and turn.outcome != ChatTurn.Outcome.ESCALATED:
        raise ConflictError("TURN_STATE_CONFLICT")

    target = resolve_support_target(actor, using=using)
    snapshot = build_question_snapshot(turn, using=using)
    ticket_id = uuid.uuid5(uuid.NAMESPACE_URL, f"assethub-assistant-ticket:{turn.id}")

    if _is_d1(using):
        from assethub_api.backends.d1 import d1_batch

        now = timezone.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        turn_id_hex = turn.id.hex if hasattr(turn.id, "hex") else str(turn.id).replace("-", "")
        ticket_id_hex = ticket_id.hex if hasattr(ticket_id, "hex") else str(ticket_id).replace("-", "")
        target_dept = target.get("department_id")

        stmts = [
            (
                "UPDATE assistant_chatturn SET outcome = %s, updated_at = %s WHERE id = %s AND outcome IN ('ANSWERED', 'CLARIFY', 'RESTRICTED', 'ERROR')",
                [ChatTurn.Outcome.ESCALATED, now_str, turn_id_hex],
            ),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", []),
            (
                "INSERT INTO assistant_supportticket (id, turn_id, target_kind, target_department_id, question_snapshot, status, version, reply, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'OPEN', 1, '', %s, %s)",
                [ticket_id_hex, turn_id_hex, target["kind"], target_dept, snapshot, now_str, now_str],
            ),
        ]
        idempotency_key = f"assistant-escalate:{turn.id}"
        try:
            d1_batch(stmts, using=using or "default", idempotency_key=idempotency_key)
        except Exception as exc:
            err_msg = str(exc)
            if (
                "_assert_guard" in err_msg
                or "CHECK constraint failed" in err_msg
                or "rows_affected" in err_msg
            ):
                raise ConflictError("TURN_STATE_CONFLICT") from exc
            # Readback recovery
            try:
                re_turn = ChatTurn.objects.using(using).get(id=turn.id)
                re_ticket = SupportTicket.objects.using(using).filter(turn=turn).first()
                if re_ticket is not None and re_turn.outcome == ChatTurn.Outcome.ESCALATED:
                    return re_ticket
            except Exception:
                pass
            if isinstance(exc, (ConflictError, PermissionDenied, NotFound, ValidationError)):
                raise
            raise ServiceUnavailable("Lỗi kết nối cơ sở dữ liệu khi chuyển hỗ trợ.") from exc

        return SupportTicket.objects.using(using).get(id=ticket_id)

    try:
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
                raise PermissionDenied("Không có quyền chuyển hỗ trợ cho lượt hỏi này.")
            if (
                actor.role != locked_turn.conversation.role_snapshot
                or actor.department_id != locked_turn.conversation.department_id_snapshot
            ):
                raise ConflictError("CONVERSATION_SCOPE_CHANGED")

            existing = SupportTicket.objects.using(using).filter(turn=locked_turn).first()
            if existing is not None:
                return existing

            if locked_turn.outcome in (ChatTurn.Outcome.PROCESSING, ChatTurn.Outcome.OUT_OF_SCOPE):
                raise ConflictError("TURN_STATE_CONFLICT")
            if locked_turn.outcome not in ESCALATABLE_OUTCOMES:
                raise ConflictError("TURN_STATE_CONFLICT")

            locked_turn.outcome = ChatTurn.Outcome.ESCALATED
            locked_turn.save(update_fields=["outcome", "updated_at"])

            ticket = SupportTicket.objects.using(using).create(
                id=ticket_id,
                turn=locked_turn,
                target_kind=target["kind"],
                target_department_id=target.get("department_id"),
                question_snapshot=snapshot,
                status=SupportTicket.Status.OPEN,
                version=1,
            )
            return ticket
    except IntegrityError:
        ticket = SupportTicket.objects.using(using).filter(turn=turn).first()
        if ticket:
            return ticket
        raise


def reply_ticket(
    *,
    actor: User,
    ticket_id: uuid.UUID | str,
    reply: str,
    expected_version: int,
    client_request_id: uuid.UUID | str,
    using: str | None = None,
) -> SupportTicket:
    """Reply to an OPEN SupportTicket with optimistic concurrency control (OCC)."""
    if isinstance(ticket_id, str):
        ticket_id = uuid.UUID(ticket_id)
    if isinstance(client_request_id, str):
        client_request_id = uuid.UUID(client_request_id)

    ticket = (
        SupportTicket.objects.using(using)
        .select_related("turn__conversation", "target_department")
        .filter(id=ticket_id)
        .first()
    )
    if ticket is None:
        raise NotFound("Không tìm thấy phiếu hỗ trợ.")

    # Permissions check
    if actor.role == User.Role.ADMIN:
        pass
    elif ticket.target_kind == SupportTicket.TargetKind.DEPARTMENT:
        if (
            actor.role == User.Role.MANAGER
            and actor.department_id is not None
            and actor.department_id == ticket.target_department_id
        ):
            pass
        else:
            raise PermissionDenied("Bạn không có quyền phản hồi phiếu hỗ trợ này.")
    else:
        raise PermissionDenied("Chỉ quản trị viên mới có quyền phản hồi phiếu hỗ trợ này.")

    clean_reply = (reply or "").strip()
    if not clean_reply:
        raise ValidationError("Nội dung phản hồi không được để trống.")
    if len(reply) > MAX_REPLY_LENGTH:
        raise ValidationError(f"Nội dung phản hồi không được vượt quá {MAX_REPLY_LENGTH} ký tự.")

    # Replay check for already answered ticket
    if ticket.status == SupportTicket.Status.ANSWERED:
        if ticket.reply_request_id == client_request_id:
            if ticket.replied_by_id == actor.id and ticket.reply == reply:
                return ticket
            raise ConflictError("REPLY_REQUEST_ID_CONFLICT")
        raise ConflictError("TICKET_ALREADY_ANSWERED")

    # OCC Update
    if _is_d1(using):
        from assethub_api.backends.d1 import d1_batch

        now = timezone.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        ticket_id_hex = ticket.id.hex if hasattr(ticket.id, "hex") else str(ticket.id).replace("-", "")
        req_id_hex = client_request_id.hex if hasattr(client_request_id, "hex") else str(client_request_id).replace("-", "")
        update_stmt = (
            "UPDATE assistant_supportticket SET status = 'ANSWERED', version = %s, reply = %s, replied_by_id = %s, replied_at = %s, reply_request_id = %s, updated_at = %s WHERE id = %s AND version = %s AND status = 'OPEN'",
            [expected_version + 1, reply, actor.id, now_str, req_id_hex, now_str, ticket_id_hex, expected_version],
        )
        guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
        try:
            d1_batch([update_stmt, guard_stmt], using=using or "default")
        except Exception as e:
            err_msg = str(e)
            is_guard_failure = (
                "_assert_guard" in err_msg
                or "CHECK constraint failed" in err_msg
                or "rows_affected" in err_msg
                or "Constraint Conflict" in err_msg
            )
            try:
                ticket.refresh_from_db()
                readback_ok = True
            except Exception:
                readback_ok = False

            if readback_ok:
                # Commit landed + response lost, replay matched
                if ticket.status == SupportTicket.Status.ANSWERED and ticket.reply_request_id == client_request_id:
                    if ticket.replied_by_id == actor.id and ticket.reply == reply:
                        return ticket
                    raise ConflictError("REPLY_REQUEST_ID_CONFLICT")

                # Proven CAS failure (guard failed or version changed or already answered)
                if (
                    is_guard_failure
                    or ticket.status != SupportTicket.Status.OPEN
                    or ticket.version != expected_version
                ):
                    raise ConflictError("STALE_VERSION_CONFLICT") from e

            # Outage before commit or failed readback
            raise ServiceUnavailable("Lỗi kết nối cơ sở dữ liệu khi phản hồi phiếu hỗ trợ.") from e

        ticket.refresh_from_db()
        return ticket

    now = timezone.now()
    updated = (
        SupportTicket.objects.using(using)
        .filter(id=ticket.id, version=expected_version, status=SupportTicket.Status.OPEN)
        .update(
            status=SupportTicket.Status.ANSWERED,
            version=expected_version + 1,
            reply=reply,
            replied_by=actor,
            replied_at=now,
            reply_request_id=client_request_id,
            updated_at=now,
        )
    )
    if updated == 0:
        ticket.refresh_from_db()
        if ticket.status == SupportTicket.Status.ANSWERED and ticket.reply_request_id == client_request_id:
            if ticket.replied_by_id == actor.id and ticket.reply == reply:
                return ticket
            raise ConflictError("REPLY_REQUEST_ID_CONFLICT")
        raise ConflictError("STALE_VERSION_CONFLICT")

    ticket.refresh_from_db()
    return ticket


def visible_tickets(*, actor: User, view: str = "mine", using: str | None = None) -> QuerySet[SupportTicket]:
    """Return tickets queryset visible to actor according to view and scope rules."""
    qs = SupportTicket.objects.using(using)
    if view == "mine":
        return qs.filter(
            turn__conversation__owner=actor,
            turn__conversation__role_snapshot=actor.role,
            turn__conversation__department_id_snapshot=actor.department_id,
        )
    elif view == "inbox":
        if actor.role == User.Role.ADMIN:
            return qs.all()
        elif actor.role == User.Role.MANAGER:
            if actor.department_id is None:
                return qs.none()
            return qs.filter(
                target_kind=SupportTicket.TargetKind.DEPARTMENT,
                target_department_id=actor.department_id,
            )
        elif actor.role == User.Role.USER:
            return qs.none()
        return qs.none()
    else:
        raise ValidationError(f"Chế độ xem không hợp lệ: '{view}'")


def get_visible_ticket(*, actor: User, ticket_id: uuid.UUID | str, using: str | None = None) -> SupportTicket:
    """Retrieve ticket if actor is authorized (either owner with matched scope or authorized staff)."""
    if isinstance(ticket_id, str):
        ticket_id = uuid.UUID(ticket_id)

    ticket = (
        SupportTicket.objects.using(using)
        .select_related("turn__conversation", "target_department")
        .filter(id=ticket_id)
        .first()
    )
    if ticket is None:
        raise NotFound("Không tìm thấy phiếu hỗ trợ.")

    # Owner check with scope snapshot validation
    conv = ticket.turn.conversation
    if (
        conv.owner_id == actor.id
        and conv.role_snapshot == actor.role
        and conv.department_id_snapshot == actor.department_id
    ):
        return ticket

    # Staff check
    if actor.role == User.Role.ADMIN:
        return ticket
    if (
        actor.role == User.Role.MANAGER
        and ticket.target_kind == SupportTicket.TargetKind.DEPARTMENT
        and actor.department_id is not None
        and actor.department_id == ticket.target_department_id
    ):
        return ticket

    raise NotFound("Không tìm thấy phiếu hỗ trợ.")
