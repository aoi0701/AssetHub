import json
import socket
import urllib.error
import uuid
from types import SimpleNamespace

from rest_framework.exceptions import NotFound, Throttled
from rest_framework.throttling import UserRateThrottle

from apps.assistant import contracts, gateway, rendering, scoping, storage, tools
from apps.assistant.models import ChatTurn, Conversation
from apps.assistant.scoping import visible_assets
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


class AssistantRateThrottle(UserRateThrottle):
    scope = "assistant"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None) or request
        ident = getattr(user, "pk", str(user))
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }


def check_rate_limit(actor: User) -> None:
    throttle = AssistantRateThrottle()
    req = SimpleNamespace(user=actor)
    if not throttle.allow_request(req, None):
        wait = throttle.wait()
        raise Throttled(detail="Tần suất gửi câu hỏi quá nhanh. Vui lòng thử lại sau.", wait=wait)


def build_planner_context(*, actor: User, conversation: Conversation) -> list[dict]:
    turns = list(
        conversation.turns.exclude(outcome=ChatTurn.Outcome.PROCESSING)
        .order_by("-created_at", "-id")[:6]
    )
    turns.reverse()

    raw_turn_codes: list[list[str]] = []
    all_codes: set[str] = set()

    for turn in turns:
        codes: list[str] = []
        for src in turn.sources or []:
            if not isinstance(src, dict):
                continue
            code = None
            if src.get("kind") == "asset":
                code = src.get("key") or (src.get("facts") or {}).get("asset_code")
            elif "facts" in src and isinstance(src["facts"], dict) and "asset_code" in src["facts"]:
                code = src["facts"]["asset_code"]
            elif "asset_code" in src:
                code = src["asset_code"]

            if code and isinstance(code, str) and code.strip():
                clean_code = code.strip()
                if clean_code not in codes:
                    codes.append(clean_code)
                    all_codes.add(clean_code)
        raw_turn_codes.append(codes)

    if all_codes:
        allowed_codes = set(
            visible_assets(actor).filter(asset_code__in=all_codes).values_list("asset_code", flat=True)
        )
    else:
        allowed_codes = set()

    context: list[dict] = []
    for turn, codes in zip(turns, raw_turn_codes):
        valid_refs = [{"asset_code": c} for c in codes if c in allowed_codes]
        context.append({
            "question": turn.question,
            "refs": valid_refs,
        })

    while context and len(json.dumps(context, ensure_ascii=False)) > 6000:
        context.pop(0)

    return context


class ChatService:
    @classmethod
    def ask(
        cls,
        *,
        actor: User,
        conversation_id: uuid.UUID | str,
        client_request_id: uuid.UUID | str,
        question: str,
    ) -> ChatTurn:
        if isinstance(conversation_id, str):
            conversation_id = uuid.UUID(conversation_id)
        if isinstance(client_request_id, str):
            client_request_id = uuid.UUID(client_request_id)

        # Step 1: Check conversation existence and ownership
        conv = Conversation.objects.filter(id=conversation_id).first()
        if not conv or conv.owner_id != actor.id:
            raise NotFound("Hội thoại không tồn tại.")

        if (
            conv.role_snapshot != actor.role
            or conv.department_id_snapshot != actor.department_id
        ):
            raise ConflictError({
                "code": "CONVERSATION_SCOPE_CHANGED",
                "detail": "Trạng thái người dùng đã thay đổi so với khi tạo hội thoại.",
            })

        # Step 2: Check replay
        existing = ChatTurn.objects.filter(conversation=conv, client_request_id=client_request_id).first()
        if existing:
            if existing.question != question:
                raise ConflictError({
                    "code": "CLIENT_REQUEST_ID_CONFLICT",
                    "detail": "client_request_id đã được sử dụng với câu hỏi khác.",
                })
            if existing.outcome == ChatTurn.Outcome.PROCESSING:
                existing = storage.expire_stale_turn(turn=existing)
            existing.is_replay = True
            return existing

        # Step 3: Check rate limit
        check_rate_limit(actor)

        # Step 4: Reserve turn
        turn, is_new = storage.reserve_turn(
            actor=actor,
            conversation=conv,
            client_request_id=client_request_id,
            question=question,
        )
        if not is_new:
            if turn.outcome == ChatTurn.Outcome.PROCESSING:
                turn = storage.expire_stale_turn(turn=turn)
            turn.is_replay = True
            return turn

        # Step 5: Build context
        context = build_planner_context(actor=actor, conversation=conv)

        # Step 6: Plan question
        scope = {
            "role": actor.role,
            "department_id": actor.department_id,
            "department_name": actor.department.name if actor.department else None,
        }

        try:
            decision = gateway.plan_question(question=question, context=context, scope=scope)
        except contracts.PlannerProtocolError as exc:
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.ERROR,
                answer="Đã có lỗi xử lý yêu cầu.",
                sources=[],
            )
            turn.is_replay = False
            wrapped = contracts.AssistantProviderUnavailable("Lỗi giao thức từ hệ thống điều phối.")
            wrapped.turn = turn
            raise wrapped from exc
        except (contracts.AssistantProviderUnavailable, socket.timeout, urllib.error.URLError) as exc:
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.ERROR,
                answer="Dịch vụ trợ lý tạm thời không khả dụng. Vui lòng thử lại sau.",
                sources=[],
            )
            turn.is_replay = False
            if not isinstance(exc, contracts.AssistantProviderUnavailable):
                wrapped = contracts.AssistantProviderUnavailable(str(exc))
            else:
                wrapped = exc
            wrapped.turn = turn
            raise wrapped from exc

        # Normalize common LLM argument aliases before strict schema validation
        if isinstance(decision, dict) and isinstance(decision.get("calls"), list):
            for call in decision["calls"]:
                if isinstance(call, dict) and isinstance(call.get("arguments"), dict):
                    args = call["arguments"]
                    if "keyword" in args and "query" not in args:
                        args["query"] = args.pop("keyword")

        # Decision validation
        try:
            decision = contracts.validate_decision(decision)
        except contracts.PlannerProtocolError as exc:
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.ERROR,
                answer="Đã có lỗi xử lý yêu cầu.",
                sources=[],
            )
            turn.is_replay = False
            wrapped = contracts.AssistantProviderUnavailable("Lỗi giao thức từ hệ thống điều phối.")
            wrapped.turn = turn
            raise wrapped from exc

        # Step 7: Handle decision
        kind = decision.get("kind")
        if kind == "clarify":
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.CLARIFY,
                answer=decision.get("clarification") or "",
                sources=[],
            )
            turn.is_replay = False
            return turn

        if kind == "out_of_scope":
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.OUT_OF_SCOPE,
                answer=decision.get("reason") or "Câu hỏi nằm ngoài phạm vi hỗ trợ tài sản.",
                sources=[],
            )
            turn.is_replay = False
            return turn

        if kind == "handoff":
            answer = "Yêu cầu của bạn đã được chuyển tiếp tới bộ phận hỗ trợ chuyên môn."
            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=ChatTurn.Outcome.ESCALATED,
                answer=answer,
                sources=[],
            )
            turn.refresh_from_db()
            turn.is_replay = False
            return turn

        if kind == "tools":
            tool_results = []
            try:
                for call in decision.get("calls", []):
                    res = tools.execute_tool(actor=actor, call=call)
                    tool_results.append(res)
            except contracts.PlannerProtocolError as exc:
                turn = storage.finalize_turn(
                    actor=actor,
                    turn=turn,
                    outcome=ChatTurn.Outcome.ERROR,
                    answer="Đã có lỗi xử lý yêu cầu.",
                    sources=[],
                )
                turn.is_replay = False
                wrapped = contracts.AssistantProviderUnavailable("Lỗi giao thức từ hệ thống điều phối.")
                wrapped.turn = turn
                raise wrapped from exc

            answer, sources = rendering.render_tool_results(tool_results)

            if any(res.get("status") == "restricted" for res in tool_results):
                outcome = ChatTurn.Outcome.RESTRICTED
            elif any(res.get("status") == "clarify" for res in tool_results):
                outcome = ChatTurn.Outcome.CLARIFY
            elif any(res.get("status") == "unsupported" for res in tool_results):
                outcome = ChatTurn.Outcome.ESCALATED
                answer = answer or "Yêu cầu của bạn đã được chuyển tiếp tới bộ phận hỗ trợ chuyên môn."
                sources = []
            else:
                outcome = ChatTurn.Outcome.ANSWERED

            turn = storage.finalize_turn(
                actor=actor,
                turn=turn,
                outcome=outcome,
                answer=answer,
                sources=sources,
            )
            if outcome == ChatTurn.Outcome.ESCALATED:
                turn.refresh_from_db()
            turn.is_replay = False
            return turn

        turn = storage.finalize_turn(
            actor=actor,
            turn=turn,
            outcome=ChatTurn.Outcome.ERROR,
            answer="Đã có lỗi xử lý yêu cầu.",
            sources=[],
        )
        turn.is_replay = False
        wrapped = contracts.AssistantProviderUnavailable("Lỗi giao thức từ hệ thống điều phối.")
        wrapped.turn = turn
        raise wrapped
