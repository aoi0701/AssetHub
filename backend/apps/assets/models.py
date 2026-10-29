from django.core.exceptions import ValidationError
from django.db import models


class IdentifierSequence(models.Model):
    """
    Sequence counter for generating sequential unique identifiers (AST-YYYY-NNN, REQ-YYYY-NNN).
    Locked with select_for_update inside transactions to prevent duplicates.
    """
    class Kind(models.TextChoices):
        AST = 'AST', 'Asset'
        REQ = 'REQ', 'Request'

    kind = models.CharField(max_length=10, choices=Kind.choices)
    year = models.IntegerField()
    value = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'year'],
                name='unique_identifier_sequence_kind_year',
            )
        ]
        ordering = ['kind', 'year']
        verbose_name = 'Identifier Sequence'
        verbose_name_plural = 'Identifier Sequences'

    def __str__(self):
        return f"{self.kind}-{self.year}: {self.value}"


class Asset(models.Model):
    """
    Physical asset model representing company assets.
    Status transitions are controlled via dedicated service workflow actions.
    Direct modification via PATCH only allows non-workflow fields.
    """
    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        IN_USE = 'IN_USE', 'In Use'
        DAMAGED_PENDING_REPAIR = 'DAMAGED_PENDING_REPAIR', 'Damaged Pending Repair'
        UNDER_MAINTENANCE = 'UNDER_MAINTENANCE', 'Under Maintenance'
        PENDING_DISPOSAL = 'PENDING_DISPOSAL', 'Pending Disposal'
        DISPOSED = 'DISPOSED', 'Disposed'

    asset_code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        'departments.Category',
        on_delete=models.PROTECT,
        related_name='assets',
    )
    serial_number = models.CharField(max_length=100, blank=True, default='')
    specification = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    department = models.ForeignKey(
        'departments.Department',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assets',
    )
    assigned_to_user = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assigned_assets',
    )
    image = models.CharField(max_length=500, blank=True, default='')
    document = models.CharField(max_length=500, blank=True, default='')
    purchase_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Asset'
        verbose_name_plural = 'Assets'
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=[
                        'AVAILABLE',
                        'PENDING_DISPOSAL',
                        'DISPOSED',
                    ],
                    assigned_to_user__isnull=False,
                ),
                name='chk_asset_no_holder_when_available_or_disposal',
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    department__isnull=True,
                    assigned_to_user__isnull=False,
                ),
                name='chk_asset_no_holder_when_unallocated',
            ),
        ]

    def clean(self):
        super().clean()
        no_holder_statuses = {
            self.Status.AVAILABLE,
            self.Status.PENDING_DISPOSAL,
            self.Status.DISPOSED,
        }
        if self.status in no_holder_statuses and self.assigned_to_user_id is not None:
            raise ValidationError(
                {'assigned_to_user': f"Tài sản ở trạng thái {self.status} không được có người giữ."}
            )

        if self.department_id is None and self.assigned_to_user_id is not None:
            raise ValidationError(
                {'assigned_to_user': "Tài sản thuộc kho chung (chưa phân bổ) không được có người giữ."}
            )

        if self.status == self.Status.IN_USE:
            if self.assigned_to_user_id is None:
                raise ValidationError(
                    {'assigned_to_user': "Tài sản đang sử dụng (IN_USE) bắt buộc phải có người giữ."}
                )
            assigned_user = getattr(self, 'assigned_to_user', None)
            if assigned_user is None and self.assigned_to_user_id is not None:
                from apps.authentication.models import User
                assigned_user = User.objects.filter(pk=self.assigned_to_user_id).first()
            if assigned_user and self.department_id != assigned_user.department_id:
                raise ValidationError(
                    {'assigned_to_user': "Người giữ tài sản phải cùng phòng ban với tài sản."}
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.asset_code})"


class AssetHistoryLog(models.Model):
    """
    Append-only audit trail for asset lifecycle events and transitions.
    Cannot be modified or deleted once created.
    """
    class ActionType(models.TextChoices):
        CREATE = 'CREATE', 'Create'
        ALLOCATE = 'ALLOCATE', 'Allocate'
        RETURN = 'RETURN', 'Return'
        DAMAGE = 'DAMAGE', 'Damage'
        MAINTENANCE_START = 'MAINTENANCE_START', 'Maintenance Start'
        MAINTENANCE_END = 'MAINTENANCE_END', 'Maintenance End'
        TRANSFER = 'TRANSFER', 'Transfer'
        DISPOSAL_PROPOSE = 'DISPOSAL_PROPOSE', 'Disposal Propose'
        DISPOSAL_CONFIRM = 'DISPOSAL_CONFIRM', 'Disposal Confirm'

    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name='history_logs',
    )
    actor = models.ForeignKey(
        'authentication.User',
        on_delete=models.PROTECT,
        related_name='asset_actions',
    )
    action_type = models.CharField(
        max_length=50,
        choices=ActionType.choices,
    )
    from_status = models.CharField(max_length=50, blank=True, default='')
    to_status = models.CharField(max_length=50)
    from_user = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assets_handed_over',
    )
    to_user = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assets_received',
    )
    from_department = models.ForeignKey(
        'departments.Department',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assets_transferred_from',
    )
    to_department = models.ForeignKey(
        'departments.Department',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assets_transferred_to',
    )
    reason = models.TextField(blank=True, default='')
    asset_code_snapshot = models.CharField(max_length=50)
    asset_name_snapshot = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Asset History Log'
        verbose_name_plural = 'Asset History Logs'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("AssetHistoryLog is append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AssetHistoryLog is append-only and cannot be deleted.")

    def __str__(self):
        return f"[{self.action_type}] {self.asset_code_snapshot} ({self.created_at})"
