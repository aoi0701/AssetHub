"""Serializers for Assistant conversations, chat turns, tickets, and procedures."""
from __future__ import annotations

from rest_framework import serializers

from .models import ChatTurn, Conversation, SupportTicket


class StrictSerializer(serializers.Serializer):
    """Serializer that strictly rejects any unknown extra fields in input data."""

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"detail": "Expected a dictionary of items."})
        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError({
                field: ["Unexpected field."] for field in sorted(unknown_fields)
            })
        return super().to_internal_value(data)


# ---------------------------------------------------------------------------
# Input Serializers
# ---------------------------------------------------------------------------

class CreateConversationSerializer(StrictSerializer):
    """Strict empty-body serializer for conversation creation."""
    pass


class AskQuestionSerializer(StrictSerializer):
    """Serializer for asking a question in a conversation turn."""
    client_request_id = serializers.UUIDField(required=True)
    question = serializers.CharField(required=True, min_length=1, max_length=1000)

    def validate_question(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError("Question cannot be blank or whitespace only.")
        return stripped


class EscalateTurnSerializer(StrictSerializer):
    """Strict empty-body serializer for manual escalation."""
    pass


class ReplyTicketSerializer(StrictSerializer):
    """Serializer for replying to an open support ticket."""
    client_request_id = serializers.UUIDField(required=True)
    expected_version = serializers.IntegerField(required=True, min_value=1)
    reply = serializers.CharField(required=True, min_length=1, max_length=4000)

    def validate_reply(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError("Reply cannot be blank or whitespace only.")
        return stripped


# ---------------------------------------------------------------------------
# Output Serializers
# ---------------------------------------------------------------------------

class SupportTicketNestedSerializer(serializers.ModelSerializer):
    """Nested serializer for support tickets attached to chat turns."""
    target_department_name = serializers.CharField(
        source='target_department.name', read_only=True, default=None
    )
    replied_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            'id',
            'target_kind',
            'target_department_name',
            'status',
            'version',
            'reply',
            'replied_by_name',
            'replied_at',
        ]

    def get_replied_by_name(self, obj: SupportTicket) -> str | None:
        if not obj.replied_by:
            return None
        return getattr(obj.replied_by, 'full_name', None) or obj.replied_by.username


class TurnSerializer(serializers.ModelSerializer):
    """Serializer for conversation chat turns."""
    conversation_id = serializers.UUIDField(source='conversation.id', read_only=True)
    ticket = SupportTicketNestedSerializer(read_only=True)

    class Meta:
        model = ChatTurn
        fields = [
            'id',
            'conversation_id',
            'client_request_id',
            'question',
            'outcome',
            'answer',
            'sources',
            'created_at',
            'updated_at',
            'ticket',
        ]


class ConversationSerializer(serializers.ModelSerializer):
    """Serializer for assistant conversations."""

    class Meta:
        model = Conversation
        fields = ['id', 'created_at', 'updated_at']


class SupportTicketListSerializer(serializers.ModelSerializer):
    """Serializer for listing support tickets."""
    target_department_name = serializers.CharField(
        source='target_department.name', read_only=True, default=None
    )

    class Meta:
        model = SupportTicket
        fields = [
            'id',
            'target_kind',
            'target_department_name',
            'status',
            'version',
            'created_at',
            'updated_at',
        ]


class SupportTicketDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for support tickets with turn and snapshot context."""
    turn_id = serializers.UUIDField(source='turn.id', read_only=True)
    target_department_name = serializers.CharField(
        source='target_department.name', read_only=True, default=None
    )
    replied_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            'id',
            'turn_id',
            'target_kind',
            'target_department_name',
            'question_snapshot',
            'status',
            'version',
            'reply',
            'replied_by_name',
            'replied_at',
            'created_at',
            'updated_at',
        ]

    def get_replied_by_name(self, obj: SupportTicket) -> str | None:
        if not obj.replied_by:
            return None
        return getattr(obj.replied_by, 'full_name', None) or obj.replied_by.username


class ProcedureDetailSerializer(serializers.Serializer):
    """Serializer for procedure knowledge lookup results."""
    topic = serializers.CharField()
    version = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    actions = serializers.ListField()
