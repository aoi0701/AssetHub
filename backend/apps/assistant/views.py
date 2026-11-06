"""API views for Assistant conversations, chat turns, procedures, and support tickets."""
from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from drf_spectacular.settings import spectacular_settings
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status

spectacular_settings.ENUM_NAME_OVERRIDES.setdefault(
    "SupportTicketStatusEnum", "apps.assistant.models.SupportTicket.Status"
)
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant import contracts, knowledge, storage, support
from apps.assistant.models import ChatTurn, Conversation, SupportTicket
from apps.assistant.pagination import AssistantPagination
from apps.assistant.serializers import (
    AskQuestionSerializer,
    ConversationSerializer,
    CreateConversationSerializer,
    EscalateTurnSerializer,
    ProcedureDetailSerializer,
    ReplyTicketSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
    TurnSerializer,
)
from apps.assistant.services import ChatService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


class AssistantFeatureDisabled(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "ASSISTANT_DISABLED"

    def __init__(self) -> None:
        super().__init__(
            detail={
                "code": "ASSISTANT_DISABLED",
                "detail": "Assistant feature is currently disabled.",
            }
        )


class AssistantBaseView(APIView):
    """Base API view enforcing authentication, feature flag gate, and error contracts."""

    permission_classes = [IsAuthenticated]

    def initial(self, request: Any, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if not getattr(settings, "ASSISTANT_ENABLED", False):
            raise AssistantFeatureDisabled()

    def handle_exception(self, exc: Exception) -> Response:
        response = super().handle_exception(exc)
        if isinstance(exc, ConflictError) and response is not None and isinstance(response.data, dict):
            if "code" not in response.data:
                detail_val = str(response.data.get("detail", ""))
                for code in (
                    "CONVERSATION_SCOPE_CHANGED",
                    "CLIENT_REQUEST_ID_CONFLICT",
                    "CONVERSATION_BUSY",
                    "STALE_VERSION_CONFLICT",
                    "TURN_STATE_CONFLICT",
                    "TICKET_ALREADY_ANSWERED",
                    "REPLY_REQUEST_ID_CONFLICT",
                ):
                    if code in detail_val or code in str(exc):
                        response.data["code"] = code
                        break
                if "code" not in response.data:
                    response.data["code"] = getattr(exc, "default_code", "conflict")
        return response


class ConversationListCreateView(AssistantBaseView):
    """List and create assistant conversations."""

    serializer_class = ConversationSerializer
    pagination_class = AssistantPagination

    @extend_schema(
        summary="Liệt kê danh sách hội thoại",
        responses={200: ConversationSerializer(many=True)},
    )
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        qs = Conversation.objects.filter(
            owner=request.user,
            role_snapshot=request.user.role,
            department_id_snapshot=request.user.department_id,
        ).order_by("-created_at", "-id")

        paginator = AssistantPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = ConversationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = ConversationSerializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Tạo mới hội thoại",
        request=CreateConversationSerializer,
        responses={201: ConversationSerializer},
    )
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        serializer = CreateConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conv = Conversation.objects.create(
            owner=request.user,
            role_snapshot=request.user.role,
            department_id_snapshot=request.user.department_id,
        )
        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED)


class TurnListCreateView(AssistantBaseView):
    """List and ask questions within a conversation."""

    serializer_class = TurnSerializer
    pagination_class = AssistantPagination

    def _get_conversation(self, conversation_id: uuid.UUID, user: User) -> Conversation:
        conv = Conversation.objects.filter(id=conversation_id).first()
        if not conv or conv.owner_id != user.id:
            raise NotFound("Hội thoại không tồn tại.")
        if (
            conv.role_snapshot != user.role
            or conv.department_id_snapshot != user.department_id
        ):
            raise ConflictError({
                "code": "CONVERSATION_SCOPE_CHANGED",
                "detail": "Trạng thái người dùng đã thay đổi so với khi tạo hội thoại.",
            })
        return conv

    @extend_schema(
        summary="Lịch sử các lượt hỏi trong hội thoại",
        parameters=[
            OpenApiParameter(
                name="conversation_id",
                description="UUID của cuộc hội thoại",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={200: TurnSerializer(many=True)},
    )
    def get(self, request: Any, conversation_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        conv = self._get_conversation(conversation_id, request.user)
        qs = (
            conv.turns.all()
            .select_related("ticket__target_department", "ticket__replied_by")
            .order_by("created_at", "id")
        )
        paginator = AssistantPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        items = page if page is not None else list(qs)
        for t in items:
            if t.outcome == ChatTurn.Outcome.PROCESSING:
                storage.expire_stale_turn(turn=t)

        if page is not None:
            serializer = TurnSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = TurnSerializer(items, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Gửi câu hỏi mới tới trợ lý trong hội thoại",
        parameters=[
            OpenApiParameter(
                name="conversation_id",
                description="UUID của cuộc hội thoại",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        request=AskQuestionSerializer,
        responses={
            201: TurnSerializer,
            200: TurnSerializer,
            202: TurnSerializer,
            503: OpenApiResponse(description="Dịch vụ trợ lý AI tạm thời không khả dụng"),
        },
    )
    def post(self, request: Any, conversation_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        self._get_conversation(conversation_id, request.user)
        serializer = AskQuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            turn = ChatService.ask(
                actor=request.user,
                conversation_id=conversation_id,
                client_request_id=serializer.validated_data["client_request_id"],
                question=serializer.validated_data["question"],
            )
        except contracts.AssistantProviderUnavailable as exc:
            turn_obj = getattr(exc, "turn", None)
            turn_data = TurnSerializer(turn_obj).data if turn_obj else None
            return Response(
                {"code": "ASSISTANT_UNAVAILABLE", "turn": turn_data},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if getattr(turn, "is_replay", False):
            res_status = (
                status.HTTP_202_ACCEPTED
                if turn.outcome == ChatTurn.Outcome.PROCESSING
                else status.HTTP_200_OK
            )
        else:
            res_status = status.HTTP_201_CREATED

        return Response(TurnSerializer(turn).data, status=res_status)


class TurnDetailView(AssistantBaseView):
    """Retrieve details of a single chat turn."""

    serializer_class = TurnSerializer

    @extend_schema(
        summary="Xem chi tiết một lượt hỏi",
        parameters=[
            OpenApiParameter(
                name="turn_id",
                description="UUID của lượt hỏi",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={200: TurnSerializer},
    )
    def get(self, request: Any, turn_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        turn = (
            ChatTurn.objects.select_related(
                "conversation", "ticket__target_department", "ticket__replied_by"
            )
            .filter(id=turn_id)
            .first()
        )
        if not turn or turn.conversation.owner_id != request.user.id:
            raise NotFound("Lượt hỏi không tồn tại.")

        conv = turn.conversation
        if (
            conv.role_snapshot != request.user.role
            or conv.department_id_snapshot != request.user.department_id
        ):
            raise ConflictError({
                "code": "CONVERSATION_SCOPE_CHANGED",
                "detail": "Trạng thái người dùng đã thay đổi so với khi tạo hội thoại.",
            })

        if turn.outcome == ChatTurn.Outcome.PROCESSING:
            turn = storage.expire_stale_turn(turn=turn)

        return Response(TurnSerializer(turn).data)


class EscalateTurnView(AssistantBaseView):
    """Manually escalate an in-domain turn to support."""

    serializer_class = SupportTicketDetailSerializer

    @extend_schema(
        summary="Chuyển lượt hỏi tới nhân viên hỗ trợ",
        parameters=[
            OpenApiParameter(
                name="turn_id",
                description="UUID của lượt hỏi",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        request=EscalateTurnSerializer,
        responses={
            201: SupportTicketDetailSerializer,
            200: SupportTicketDetailSerializer,
        },
    )
    def post(self, request: Any, turn_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        serializer = EscalateTurnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        turn = (
            ChatTurn.objects.select_related("conversation")
            .filter(id=turn_id)
            .first()
        )
        if not turn or turn.conversation.owner_id != request.user.id:
            raise NotFound("Lượt hỏi không tồn tại.")

        conv = turn.conversation
        if (
            conv.role_snapshot != request.user.role
            or conv.department_id_snapshot != request.user.department_id
        ):
            raise ConflictError({
                "code": "CONVERSATION_SCOPE_CHANGED",
                "detail": "Trạng thái người dùng đã thay đổi so với khi tạo hội thoại.",
            })

        if turn.outcome in (ChatTurn.Outcome.PROCESSING, ChatTurn.Outcome.OUT_OF_SCOPE):
            raise ConflictError({
                "code": "TURN_STATE_CONFLICT",
                "detail": "Không thể chuyển hỗ trợ lượt hỏi đang xử lý hoặc ngoài phạm vi.",
            })

        ticket_existed = SupportTicket.objects.filter(turn=turn).exists()
        ticket = support.escalate_turn(actor=request.user, turn=turn)

        res_status = status.HTTP_200_OK if ticket_existed else status.HTTP_201_CREATED
        return Response(SupportTicketDetailSerializer(ticket).data, status=res_status)


class ProcedureDetailView(AssistantBaseView):
    """Retrieve details and actionable links for a procedure topic."""

    serializer_class = ProcedureDetailSerializer

    @extend_schema(
        summary="Tra cứu quy trình tài sản",
        parameters=[
            OpenApiParameter(
                name="topic",
                description="Tên chủ đề quy trình",
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={200: ProcedureDetailSerializer},
    )
    def get(self, request: Any, topic: str, *args: Any, **kwargs: Any) -> Response:
        try:
            res = knowledge.lookup_procedure(actor=request.user, topic=topic)
        except (contracts.PlannerProtocolError, KeyError):
            raise NotFound("Quy trình không tồn tại.")

        if res.get("status") != "ok":
            raise NotFound("Quy trình không tồn tại.")

        return Response(res["data"])


class SupportTicketListView(AssistantBaseView):
    """List support tickets for user or staff inbox."""

    serializer_class = SupportTicketListSerializer
    pagination_class = AssistantPagination

    @extend_schema(
        summary="Danh sách phiếu hỗ trợ",
        parameters=[
            OpenApiParameter(
                name="view",
                description="Chế độ xem ('mine' hoặc 'inbox')",
                required=False,
                type=str,
                default="mine",
            ),
            OpenApiParameter(
                name="status",
                description="Lọc theo trạng thái ('OPEN' hoặc 'ANSWERED')",
                required=False,
                type=str,
            ),
        ],
        responses={200: SupportTicketListSerializer(many=True)},
    )
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        view_param = request.query_params.get("view", "mine")
        if view_param not in ("mine", "inbox"):
            raise ValidationError({"view": "Tham số view không hợp lệ."})

        if view_param == "inbox" and request.user.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền truy cập hộp thư hỗ trợ.")

        qs = support.visible_tickets(actor=request.user, view=view_param)

        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in SupportTicket.Status.values:
                raise ValidationError({"status": "Trạng thái không hợp lệ."})
            qs = qs.filter(status=status_param)

        qs = qs.order_by("-created_at", "-id")

        paginator = AssistantPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = SupportTicketListSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = SupportTicketListSerializer(qs, many=True)
        return Response(serializer.data)


class SupportTicketDetailView(AssistantBaseView):
    """Retrieve details of a single support ticket."""

    serializer_class = SupportTicketDetailSerializer

    @extend_schema(
        summary="Xem chi tiết phiếu hỗ trợ",
        parameters=[
            OpenApiParameter(
                name="ticket_id",
                description="UUID của phiếu hỗ trợ",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={200: SupportTicketDetailSerializer},
    )
    def get(self, request: Any, ticket_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        ticket = support.get_visible_ticket(actor=request.user, ticket_id=ticket_id)
        return Response(SupportTicketDetailSerializer(ticket).data)


class SupportTicketReplyView(AssistantBaseView):
    """Reply to an open support ticket."""

    serializer_class = SupportTicketDetailSerializer

    @extend_schema(
        summary="Phản hồi phiếu hỗ trợ",
        parameters=[
            OpenApiParameter(
                name="ticket_id",
                description="UUID của phiếu hỗ trợ",
                required=True,
                type=uuid.UUID,
                location=OpenApiParameter.PATH,
            ),
        ],
        request=ReplyTicketSerializer,
        responses={200: SupportTicketDetailSerializer},
    )
    def post(self, request: Any, ticket_id: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        serializer = ReplyTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ticket = support.reply_ticket(
            actor=request.user,
            ticket_id=ticket_id,
            reply=serializer.validated_data["reply"],
            expected_version=serializer.validated_data["expected_version"],
            client_request_id=serializer.validated_data["client_request_id"],
        )
        return Response(SupportTicketDetailSerializer(ticket).data, status=status.HTTP_200_OK)
