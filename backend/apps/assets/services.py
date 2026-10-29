from typing import Optional
from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.departments.models import Department


class AssetService:
    """
    Business service executing lifecycle transitions and audit logging on Asset models.
    All transitions execute within an atomic transaction with select_for_update() row-level locking.
    Permissions and state conditions are re-verified after acquiring the lock.
    """

    @classmethod
    def _get_locked_asset(cls, asset_id: int, actor: User, admin_only: bool = False) -> Asset:
        """
        Acquires row-level lock on Asset and verifies scoping and actor permissions.
        Raises NotFound (404), PermissionDenied (403), or ConflictError (409).
        """
        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Yêu cầu xác thực.")

        if actor.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền thực hiện thao tác quản lý tài sản.")

        try:
            asset = Asset.objects.select_for_update().get(pk=asset_id)
        except Asset.DoesNotExist:
            raise NotFound("Không tìm thấy tài sản.")

        # Scoping check for Manager
        if actor.role == User.Role.MANAGER:
            # Manager has no access to company pool or assets in other departments
            if asset.department_id is None or asset.department_id != actor.department_id:
                raise NotFound("Không tìm thấy tài sản trong phạm vi phòng ban được phân công.")

        # Role permission check
        if admin_only and actor.role != User.Role.ADMIN:
            raise PermissionDenied("Chỉ Quản trị viên (Admin) mới có quyền thực hiện thao tác này.")

        # Terminal state check
        if asset.status == Asset.Status.DISPOSED:
            raise ConflictError("Tài sản đã thanh lý (DISPOSED), không thể thực hiện thêm thao tác chuyển trạng thái.")

        return asset

    @classmethod
    def _create_log(
        cls,
        asset: Asset,
        actor: User,
        action_type: str,
        from_status: str,
        to_status: str,
        reason: str,
        from_user: Optional[User] = None,
        to_user: Optional[User] = None,
        from_department: Optional[Department] = None,
        to_department: Optional[Department] = None,
    ) -> AssetHistoryLog:
        return AssetHistoryLog.objects.create(
            asset=asset,
            actor=actor,
            action_type=action_type,
            from_status=from_status,
            to_status=to_status,
            from_user=from_user,
            to_user=to_user,
            from_department=from_department,
            to_department=to_department,
            reason=reason.strip(),
            asset_code_snapshot=asset.asset_code,
            asset_name_snapshot=asset.name,
        )

    @classmethod
    def allocate(cls, asset_id: int, actor: User, assigned_to_user_id: int, reason: str) -> Asset:
        """
        Allocate AVAILABLE asset to an active user in the same department.
        Transitions: AVAILABLE -> IN_USE.
        """
        if not assigned_to_user_id:
            raise ValidationError({"assigned_to_user_id": "Vui lòng chọn nhân viên nhận tài sản."})
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do cấp phát không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
                )

            if asset.department_id is None:
                raise ConflictError(
                    "Tài sản thuộc kho chung công ty chưa phân bổ về phòng ban, không thể cấp phát cho nhân viên."
                )

            try:
                target_user = User.objects.get(pk=assigned_to_user_id)
            except User.DoesNotExist:
                raise ValidationError({"assigned_to_user_id": "Người dùng không tồn tại."})

            if not target_user.is_active:
                raise ConflictError("Người nhận tài sản đang bị vô hiệu hóa, không thể cấp phát.")

            if target_user.department_id != asset.department_id:
                raise ConflictError("Người nhận tài sản phải cùng phòng ban với tài sản.")

            from_status = asset.status
            asset.status = Asset.Status.IN_USE
            asset.assigned_to_user = target_user
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.ALLOCATE,
                from_status=from_status,
                to_status=Asset.Status.IN_USE,
                from_user=None,
                to_user=target_user,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def revoke(
        cls,
        asset_id: int,
        actor: User,
        received_confirmed: bool,
        condition: str,
        reason: str,
    ) -> Asset:
        """
        Revoke IN_USE asset back to stock or repair pending.
        Transitions:
        - condition == 'GOOD': IN_USE -> AVAILABLE
        - condition == 'DAMAGED': IN_USE -> DAMAGED_PENDING_REPAIR
        Holder is cleared in both cases.
        """
        if not received_confirmed:
            raise ValidationError(
                {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi thu hồi."}
            )
        if condition not in ('GOOD', 'DAMAGED'):
            raise ValidationError(
                {"condition": "Tình trạng tài sản không hợp lệ (phải là GOOD hoặc DAMAGED)."}
            )
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do thu hồi không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể thu hồi."
                )

            old_holder = asset.assigned_to_user
            from_status = asset.status

            if condition == 'GOOD':
                to_status = Asset.Status.AVAILABLE
            else:
                to_status = Asset.Status.DAMAGED_PENDING_REPAIR

            asset.status = to_status
            asset.assigned_to_user = None
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.RETURN,
                from_status=from_status,
                to_status=to_status,
                from_user=old_holder,
                to_user=None,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def mark_damaged(cls, asset_id: int, actor: User, reason: str) -> Asset:
        """
        Mark an in-stock asset as damaged.
        Transitions: AVAILABLE -> DAMAGED_PENDING_REPAIR.
        (If asset is IN_USE, damage reporting must go through the request workflow).
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do báo hỏng không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE tại kho mới có thể báo hỏng trực tiếp."
                )

            from_status = asset.status
            asset.status = Asset.Status.DAMAGED_PENDING_REPAIR
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DAMAGE,
                from_status=from_status,
                to_status=Asset.Status.DAMAGED_PENDING_REPAIR,
                from_user=None,
                to_user=None,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def confirm_damage_report(cls, asset_id: int, actor: User, reason: str) -> Asset:
        """
        Confirm damage reported on an IN_USE asset by its holder.
        Transitions: IN_USE -> DAMAGED_PENDING_REPAIR.
        Retains holder for accountability.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do báo hỏng không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể xác nhận báo hỏng."
                )

            if asset.assigned_to_user is None:
                raise ConflictError("Tài sản không có người giữ để xác nhận báo hỏng.")

            old_holder = asset.assigned_to_user
            from_status = asset.status
            asset.status = Asset.Status.DAMAGED_PENDING_REPAIR
            # Keeps assigned_to_user intact
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DAMAGE,
                from_status=from_status,
                to_status=Asset.Status.DAMAGED_PENDING_REPAIR,
                from_user=old_holder,
                to_user=old_holder,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def send_maintenance(cls, asset_id: int, actor: User, reason: str) -> Asset:
        """
        Send a damaged asset for repair/maintenance.
        Transitions: DAMAGED_PENDING_REPAIR -> UNDER_MAINTENANCE.
        Preserves holder if any.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do gửi bảo trì không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.DAMAGED_PENDING_REPAIR:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản DAMAGED_PENDING_REPAIR mới có thể gửi đi bảo trì."
                )

            from_status = asset.status
            asset.status = Asset.Status.UNDER_MAINTENANCE
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.MAINTENANCE_START,
                from_status=from_status,
                to_status=Asset.Status.UNDER_MAINTENANCE,
                from_user=asset.assigned_to_user,
                to_user=asset.assigned_to_user,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def complete_maintenance(cls, asset_id: int, actor: User, result: str, reason: str) -> Asset:
        """
        Complete maintenance on an asset under maintenance.
        Transitions:
        - result == 'RETURN_TO_USER': UNDER_MAINTENANCE -> IN_USE (holder must still be active and in same dept)
        - result == 'RETURN_TO_STOCK': UNDER_MAINTENANCE -> AVAILABLE (holder cleared)
        """
        if result not in ('RETURN_TO_USER', 'RETURN_TO_STOCK'):
            raise ValidationError(
                {"result": "Kết quả bảo trì phải là RETURN_TO_USER hoặc RETURN_TO_STOCK."}
            )
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do hoàn tất bảo trì không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            if asset.status != Asset.Status.UNDER_MAINTENANCE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản UNDER_MAINTENANCE mới có thể hoàn tất bảo trì."
                )

            from_status = asset.status

            if result == 'RETURN_TO_USER':
                holder = asset.assigned_to_user
                if not holder:
                    raise ConflictError(
                        "Tài sản không có thông tin người chịu trách nhiệm trước đó để bàn giao lại."
                    )
                if not holder.is_active:
                    raise ConflictError(
                        "Người chịu trách nhiệm trước đó hiện không còn hoạt động, không thể bàn giao lại."
                    )
                if holder.department_id != asset.department_id:
                    raise ConflictError(
                        "Người chịu trách nhiệm trước đó đã chuyển sang phòng ban khác, không thể bàn giao lại."
                    )

                to_status = Asset.Status.IN_USE
                from_user = holder
                to_user = holder
                asset.status = to_status
                asset.save()
            else:
                old_holder = asset.assigned_to_user
                to_status = Asset.Status.AVAILABLE
                from_user = old_holder
                to_user = None
                asset.status = to_status
                asset.assigned_to_user = None
                asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.MAINTENANCE_END,
                from_status=from_status,
                to_status=to_status,
                from_user=from_user,
                to_user=to_user,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def transfer(
        cls,
        asset_id: int,
        actor: User,
        to_department_id: Optional[int],
        reason: str,
    ) -> Asset:
        """
        Transfer an in-stock asset between departments or to company pool.
        Strictly Admin-only.
        Transitions: AVAILABLE -> AVAILABLE (updates department).
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do điều chuyển không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể điều chuyển."
                )

            if asset.assigned_to_user is not None:
                raise ConflictError("Tài sản đang có người giữ, không thể điều chuyển.")

            if to_department_id is not None:
                try:
                    target_dept = Department.objects.get(pk=to_department_id)
                except Department.DoesNotExist:
                    raise ValidationError({"to_department_id": "Phòng ban đích không tồn tại."})

                if asset.department_id == target_dept.id:
                    raise ValidationError(
                        {"to_department_id": "Phòng ban đích phải khác với phòng ban hiện tại của tài sản."}
                    )
            else:
                if asset.department_id is None:
                    raise ValidationError(
                        {"to_department_id": "Tài sản hiện đã thuộc kho chung công ty."}
                    )
                target_dept = None

            old_dept = asset.department
            asset.department = target_dept
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.TRANSFER,
                from_status=Asset.Status.AVAILABLE,
                to_status=Asset.Status.AVAILABLE,
                from_user=None,
                to_user=None,
                from_department=old_dept,
                to_department=target_dept,
                reason=reason,
            )

            return asset

    @classmethod
    def propose_disposal(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        received_confirmed: bool = False,
    ) -> Asset:
        """
        Propose asset for disposal.
        Source statuses: AVAILABLE, DAMAGED_PENDING_REPAIR, UNDER_MAINTENANCE.
        If holder is present, received_confirmed=True is required. Clears holder.
        Transitions: (AVAILABLE | DAMAGED_PENDING_REPAIR | UNDER_MAINTENANCE) -> PENDING_DISPOSAL.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do đề xuất thanh lý không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor)

            allowed_statuses = {
                Asset.Status.AVAILABLE,
                Asset.Status.DAMAGED_PENDING_REPAIR,
                Asset.Status.UNDER_MAINTENANCE,
            }
            if asset.status not in allowed_statuses:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, không thể đề xuất thanh lý."
                )

            if asset.assigned_to_user is not None and not received_confirmed:
                raise ValidationError(
                    {
                        "received_confirmed": (
                            "Tài sản đang có người giữ, bắt buộc phải xác nhận "
                            "đã nhận lại trước khi đề xuất thanh lý."
                        )
                    }
                )

            old_status = asset.status
            old_user = asset.assigned_to_user

            asset.status = Asset.Status.PENDING_DISPOSAL
            asset.assigned_to_user = None
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
                from_status=old_status,
                to_status=Asset.Status.PENDING_DISPOSAL,
                from_user=old_user,
                to_user=None,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset

    @classmethod
    def confirm_disposal(cls, asset_id: int, actor: User, reason: str) -> Asset:
        """
        Confirm disposal of asset pending disposal.
        Strictly Admin-only.
        Transitions: PENDING_DISPOSAL -> DISPOSED.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do xác nhận thanh lý không được để trống."})

        with transaction.atomic():
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True)

            if asset.status != Asset.Status.PENDING_DISPOSAL:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản PENDING_DISPOSAL mới có thể xác nhận thanh lý."
                )

            from_status = asset.status
            asset.status = Asset.Status.DISPOSED
            asset.assigned_to_user = None
            asset.save()

            cls._create_log(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
                from_status=from_status,
                to_status=Asset.Status.DISPOSED,
                from_user=None,
                to_user=None,
                from_department=asset.department,
                to_department=asset.department,
                reason=reason,
            )

            return asset
