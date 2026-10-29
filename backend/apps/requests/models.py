from django.conf import settings
from django.db import models
from apps.assets.identifiers import generate_identifier


class AssetRequest(models.Model):
    """
    Asset Request lifecycle: ALLOCATE, RETURN, DAMAGE_REPORT.
    Created by User, reviewed/approved/rejected by Manager (matching department) or Admin.
    """
    class RequestType(models.TextChoices):
        ALLOCATE = 'ALLOCATE', 'Cấp phát'
        RETURN = 'RETURN', 'Thu hồi / Trả lại'
        DAMAGE_REPORT = 'DAMAGE_REPORT', 'Báo hỏng'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ duyệt'
        APPROVED = 'APPROVED', 'Đã duyệt'
        REJECTED = 'REJECTED', 'Từ chối'

    request_code = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
    )
    request_type = models.CharField(
        max_length=20,
        choices=RequestType.choices,
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='asset_requests',
    )
    asset = models.ForeignKey(
        'assets.Asset',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='requests',
    )
    requested_category = models.ForeignKey(
        'departments.Category',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='asset_requests',
    )
    department = models.ForeignKey(
        'departments.Department',
        on_delete=models.PROTECT,
        related_name='asset_requests',
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reviewed_requests',
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    rejection_reason = models.TextField(
        blank=True,
        default='',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['requester', 'request_type', 'asset'],
                condition=models.Q(
                    status='PENDING',
                    request_type__in=['RETURN', 'DAMAGE_REPORT'],
                    asset__isnull=False,
                ),
                name='unique_pending_return_or_damage_per_user_asset',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.request_code:
            self.request_code = generate_identifier('REQ')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.request_code} ({self.request_type}) - {self.status}"
