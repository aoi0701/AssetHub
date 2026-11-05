import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual

from apps.authentication.models import User


def _length_between(field, minimum, maximum):
    return models.Q(GreaterThanOrEqual(Length(field), minimum), LessThanOrEqual(Length(field), maximum))


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='assistant_conversations',
        db_index=False,
    )
    role_snapshot = models.CharField(max_length=20, choices=User.Role.choices)
    department_id_snapshot = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', '-created_at'], name='assistant_conv_owner_date'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role_snapshot__in=User.Role.values),
                name='chk_assistant_conv_role',
            ),
        ]


class ChatTurn(models.Model):
    class Outcome(models.TextChoices):
        PROCESSING = 'PROCESSING', 'Processing'
        ANSWERED = 'ANSWERED', 'Answered'
        CLARIFY = 'CLARIFY', 'Clarify'
        ESCALATED = 'ESCALATED', 'Escalated'
        RESTRICTED = 'RESTRICTED', 'Restricted'
        OUT_OF_SCOPE = 'OUT_OF_SCOPE', 'Out of scope'
        ERROR = 'ERROR', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.PROTECT,
        related_name='turns',
        db_index=False,
    )
    client_request_id = models.UUIDField()
    question = models.TextField()
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.PROCESSING)
    answer = models.TextField(blank=True, default='')
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at'], name='assistant_turn_conv_date'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'client_request_id'],
                name='uniq_assistant_turn_request',
            ),
            models.UniqueConstraint(
                fields=['conversation'],
                condition=models.Q(outcome='PROCESSING'),
                name='uniq_assistant_turn_processing',
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=['PROCESSING', 'ANSWERED', 'CLARIFY', 'ESCALATED',
                                                'RESTRICTED', 'OUT_OF_SCOPE', 'ERROR']),
                name='chk_assistant_turn_outcome',
            ),
            models.CheckConstraint(
                condition=_length_between('question', 1, 1000),
                name='chk_assistant_turn_question_len',
            ),
            models.CheckConstraint(
                condition=models.Q(LessThanOrEqual(Length('answer'), 6000)),
                name='chk_assistant_turn_answer_len',
            ),
        ]


class SupportTicket(models.Model):
    class TargetKind(models.TextChoices):
        DEPARTMENT = 'DEPARTMENT', 'Department'
        ADMIN = 'ADMIN', 'Admin'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        ANSWERED = 'ANSWERED', 'Answered'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    turn = models.OneToOneField(ChatTurn, on_delete=models.PROTECT, related_name='ticket')
    target_kind = models.CharField(max_length=20, choices=TargetKind.choices)
    target_department = models.ForeignKey(
        'departments.Department',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assistant_tickets',
        db_index=False,
    )
    question_snapshot = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    version = models.IntegerField(default=1)
    reply = models.TextField(blank=True, default='')
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assistant_ticket_replies',
        db_index=False,
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    reply_request_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['target_kind', 'target_department', 'status', '-created_at'],
                name='assistant_ticket_target_date',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(target_kind='DEPARTMENT', target_department__isnull=False)
                    | models.Q(target_kind='ADMIN', target_department__isnull=True)
                ),
                name='chk_assistant_ticket_target',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='OPEN',
                        reply='',
                        replied_by__isnull=True,
                        replied_at__isnull=True,
                        reply_request_id__isnull=True,
                    )
                    | models.Q(
                        GreaterThanOrEqual(Length('reply'), 1),
                        status='ANSWERED',
                        replied_by__isnull=False,
                        replied_at__isnull=False,
                    )
                ),
                name='chk_assistant_ticket_reply_state',
            ),
            models.CheckConstraint(
                condition=_length_between('question_snapshot', 1, 3000),
                name='chk_assistant_ticket_snapshot',
            ),
            models.CheckConstraint(
                condition=models.Q(LessThanOrEqual(Length('reply'), 4000)),
                name='chk_assistant_ticket_reply_len',
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name='chk_assistant_ticket_version',
            ),
        ]
