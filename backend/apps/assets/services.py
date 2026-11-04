from typing import Any, List, Optional, Tuple
from django.db import DatabaseError, connections, transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.departments.models import Department
from assethub_api.backends.d1 import d1_batch


class AssetService:
    """
    Business service executing lifecycle transitions and audit logging on Asset models.
    Supports both PostgreSQL (select_for_update + transaction.atomic) and
    Cloudflare D1 (Atomic Guarded Batch OCC with _assert_guard).
    """

    @classmethod
    def _is_d1(cls, using: Optional[str] = None) -> bool:
        """
        Detects whether target connection is Cloudflare D1.
        """
        conn = connections[using or "default"]
        if getattr(conn, "vendor", None) == "postgresql":
            return False
        raw_conn = getattr(conn, "connection", None)
        return bool(
            getattr(conn, "vendor", None) in ("cloudflare_d1", "d1")
            or getattr(raw_conn, "client", None) is not None
            or (hasattr(conn, "client") and getattr(conn, "vendor", None) not in ("postgresql", "sqlite"))
        )

    @classmethod
    def _get_locked_asset(
        cls,
        asset_id: int,
        actor: User,
        admin_only: bool = False,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Acquires row-level lock on Asset in PG (or direct get in D1) and verifies scoping and actor permissions.
        Raises NotFound (404), PermissionDenied (403), or ConflictError (409).
        """
        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Yêu cầu xác thực.")

        if actor.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền thực hiện thao tác quản lý tài sản.")

        qs = Asset.objects
        if using:
            qs = qs.using(using)
        if not cls._is_d1(using):
            qs = qs.select_for_update()

        try:
            asset = qs.get(pk=asset_id)
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
        using: Optional[str] = None,
    ) -> AssetHistoryLog:
        qs = AssetHistoryLog.objects
        if using:
            qs = qs.using(using)
        return qs.create(
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
    def _build_audit_statement(
        cls,
        asset: Asset,
        actor: User,
        action_type: str,
        from_status: str,
        to_status: str,
        reason: str,
        from_user_id: Optional[int] = None,
        to_user_id: Optional[int] = None,
        from_department_id: Optional[int] = None,
        to_department_id: Optional[int] = None,
    ) -> Tuple[str, List[Any]]:
        actor_id = actor.id if hasattr(actor, "id") else int(actor)
        sql = """
INSERT INTO assets_assethistorylog (
    asset_id, actor_id, action_type, from_status, to_status,
    from_user_id, to_user_id, from_department_id, to_department_id,
    reason, asset_code_snapshot, asset_name_snapshot, created_at
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, datetime('now')
)
""".strip()
        params = [
            asset.id,
            actor_id,
            action_type,
            from_status,
            to_status,
            from_user_id,
            to_user_id,
            from_department_id,
            to_department_id,
            reason.strip(),
            asset.asset_code,
            asset.name,
        ]
        return (sql, params)

    @classmethod
    def _execute_d1_mutation(
        cls,
        statements: List[Tuple[str, List[Any]]],
        asset_id: int,
        using: Optional[str] = None,
    ) -> Asset:
        try:
            d1_batch(statements, using=using or "default")
        except DatabaseError as e:
            err_msg = str(e)
            if (
                "_assert_guard" in err_msg
                or "CHECK constraint failed" in err_msg
                or "rows_affected" in err_msg
                or "Constraint Conflict" in err_msg
            ):
                raise ConflictError(
                    "Trạng thái tài sản đã bị thay đổi bởi thao tác khác (OCC conflict)."
                ) from e
            raise

        qs = Asset.objects
        if using:
            qs = qs.using(using)
        return qs.get(pk=asset_id)

    # -------------------------------------------------------------------------
    # Statement Builder Helpers for Agent C's RequestService
    # -------------------------------------------------------------------------

    @classmethod
    def build_allocate_statements(
        cls,
        asset: Asset,
        actor: User,
        assigned_to_user: User,
        reason: str,
    ) -> List[Tuple[str, List[Any]]]:
        """
        Builds atomic D1 batch statements [UPDATE asset, ASSERT guard, INSERT audit_log] for allocate.
        """
        target_user_id = assigned_to_user.id if hasattr(assigned_to_user, "id") else int(assigned_to_user)
        update_sql = """
UPDATE assets_asset
SET status = %s, assigned_to_user_id = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL
""".strip()
        update_params = [Asset.Status.IN_USE, target_user_id, asset.id, Asset.Status.AVAILABLE, asset.department_id]
        assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
        audit_stmt = cls._build_audit_statement(
            asset=asset,
            actor=actor,
            action_type=AssetHistoryLog.ActionType.ALLOCATE,
            from_status=Asset.Status.AVAILABLE,
            to_status=Asset.Status.IN_USE,
            reason=reason,
            from_user_id=None,
            to_user_id=target_user_id,
            from_department_id=asset.department_id,
            to_department_id=asset.department_id,
        )
        return [(update_sql, update_params), assert_guard_stmt, audit_stmt]

    @classmethod
    def build_revoke_statements(
        cls,
        asset: Asset,
        actor: User,
        condition: str,
        reason: str,
    ) -> List[Tuple[str, List[Any]]]:
        """
        Builds atomic D1 batch statements [UPDATE asset, ASSERT guard, INSERT audit_log] for revoke.
        """
        to_status = Asset.Status.AVAILABLE if condition == "GOOD" else Asset.Status.DAMAGED_PENDING_REPAIR
        update_sql = """
UPDATE assets_asset
SET status = %s, assigned_to_user_id = NULL, updated_at = datetime('now')
WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s
""".strip()
        update_params = [to_status, asset.id, Asset.Status.IN_USE, asset.assigned_to_user_id, asset.department_id]
        assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
        audit_stmt = cls._build_audit_statement(
            asset=asset,
            actor=actor,
            action_type=AssetHistoryLog.ActionType.RETURN,
            from_status=Asset.Status.IN_USE,
            to_status=to_status,
            reason=reason,
            from_user_id=asset.assigned_to_user_id,
            to_user_id=None,
            from_department_id=asset.department_id,
            to_department_id=asset.department_id,
        )
        return [(update_sql, update_params), assert_guard_stmt, audit_stmt]

    @classmethod
    def build_confirm_damage_statements(
        cls,
        asset: Asset,
        actor: User,
        reason: str,
    ) -> List[Tuple[str, List[Any]]]:
        """
        Builds atomic D1 batch statements [UPDATE asset, ASSERT guard, INSERT audit_log] for confirm_damage_report.
        """
        update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND assigned_to_user_id = %s AND department_id = %s
""".strip()
        update_params = [
            Asset.Status.DAMAGED_PENDING_REPAIR,
            asset.id,
            Asset.Status.IN_USE,
            asset.assigned_to_user_id,
            asset.department_id,
        ]
        assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
        audit_stmt = cls._build_audit_statement(
            asset=asset,
            actor=actor,
            action_type=AssetHistoryLog.ActionType.DAMAGE,
            from_status=Asset.Status.IN_USE,
            to_status=Asset.Status.DAMAGED_PENDING_REPAIR,
            reason=reason,
            from_user_id=asset.assigned_to_user_id,
            to_user_id=asset.assigned_to_user_id,
            from_department_id=asset.department_id,
            to_department_id=asset.department_id,
        )
        return [(update_sql, update_params), assert_guard_stmt, audit_stmt]

    # -------------------------------------------------------------------------
    # 9 Lifecycle Methods
    # -------------------------------------------------------------------------

    @classmethod
    def allocate(
        cls,
        asset_id: int,
        actor: User,
        assigned_to_user_id: int,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Allocate AVAILABLE asset to an active user in the same department.
        Transitions: AVAILABLE -> IN_USE.
        """
        # Support both (asset_id, actor, assigned_to_user_id, reason)
        # and (asset_id, assigned_to_user_id, actor, reason)
        if isinstance(actor, (int, str)) and isinstance(assigned_to_user_id, User):
            actor, assigned_to_user_id = assigned_to_user_id, int(actor)

        if not assigned_to_user_id:
            raise ValidationError({"assigned_to_user_id": "Vui lòng chọn nhân viên nhận tài sản."})
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do cấp phát không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
                )
            if asset.department_id is None:
                raise ConflictError(
                    "Tài sản thuộc kho chung công ty chưa phân bổ về phòng ban, không thể cấp phát cho nhân viên."
                )
            if actor.role == User.Role.MANAGER and asset.department_id != actor.department_id:
                raise PermissionDenied("Không có quyền cấp phát tài sản ngoài phạm vi phòng ban.")
            try:
                user_qs = User.objects
                if using:
                    user_qs = user_qs.using(using)
                target_user = user_qs.get(pk=assigned_to_user_id)
            except User.DoesNotExist:
                raise ValidationError({"assigned_to_user_id": "Người dùng không tồn tại."})

            if not target_user.is_active:
                raise ConflictError("Người nhận tài sản đang bị vô hiệu hóa, không thể cấp phát.")
            if target_user.department_id != asset.department_id:
                raise ConflictError("Người nhận tài sản phải cùng phòng ban với tài sản.")

            target_user_id = target_user.id if hasattr(target_user, "id") else int(target_user)
            target_user_guard = (
                "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s",
                [target_user_id, asset.department_id],
            )
            user_guard_check = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            statements = [target_user_guard, user_guard_check] + cls.build_allocate_statements(
                asset, actor, target_user, reason
            )
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
                )

            if asset.department_id is None:
                raise ConflictError(
                    "Tài sản thuộc kho chung công ty chưa phân bổ về phòng ban, không thể cấp phát cho nhân viên."
                )

            try:
                user_qs = User.objects
                if using:
                    user_qs = user_qs.using(using)
                target_user = user_qs.get(pk=assigned_to_user_id)
            except User.DoesNotExist:
                raise ValidationError({"assigned_to_user_id": "Người dùng không tồn tại."})

            if not target_user.is_active:
                raise ConflictError("Người nhận tài sản đang bị vô hiệu hóa, không thể cấp phát.")

            if target_user.department_id != asset.department_id:
                raise ConflictError("Người nhận tài sản phải cùng phòng ban với tài sản.")

            from_status = asset.status
            asset.status = Asset.Status.IN_USE
            asset.assigned_to_user = target_user
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def revoke(
        cls,
        asset_id: int,
        actor: User,
        received_confirmed: bool = True,
        condition: str = "GOOD",
        reason: str = "",
        using: Optional[str] = None,
    ) -> Asset:
        """
        Revoke IN_USE asset back to stock or repair pending.
        Transitions:
        - condition == 'GOOD': IN_USE -> AVAILABLE
        - condition == 'DAMAGED': IN_USE -> DAMAGED_PENDING_REPAIR
        Holder is cleared in both cases.
        """
        # Support positional calling convention: revoke(asset_id, to_status_or_condition, actor, reason)
        if isinstance(actor, str) and isinstance(received_confirmed, User):
            status_or_cond = actor
            actual_actor = received_confirmed
            actual_reason = condition if isinstance(condition, str) else reason
            actual_cond = "GOOD" if status_or_cond in ("AVAILABLE", "GOOD") else "DAMAGED"
            actor = actual_actor
            condition = actual_cond
            received_confirmed = True
            reason = actual_reason

        if not received_confirmed:
            raise ValidationError(
                {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi thu hồi."}
            )
        if condition not in ("GOOD", "DAMAGED"):
            raise ValidationError(
                {"condition": "Tình trạng tài sản không hợp lệ (phải là GOOD hoặc DAMAGED)."}
            )
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do thu hồi không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể thu hồi."
                )
            statements = cls.build_revoke_statements(asset, actor, condition, reason)
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể thu hồi."
                )

            old_holder = asset.assigned_to_user
            from_status = asset.status

            if condition == "GOOD":
                to_status = Asset.Status.AVAILABLE
            else:
                to_status = Asset.Status.DAMAGED_PENDING_REPAIR

            asset.status = to_status
            asset.assigned_to_user = None
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def mark_damaged(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Mark an in-stock asset as damaged.
        Transitions: AVAILABLE -> DAMAGED_PENDING_REPAIR.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do báo hỏng không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE tại kho mới có thể báo hỏng trực tiếp."
                )
            if asset.assigned_to_user_id is not None:
                raise ConflictError("Tài sản đang có người giữ, không thể báo hỏng trực tiếp tại kho.")

            if asset.department_id is not None:
                update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id IS NULL
""".strip()
                update_params = [
                    Asset.Status.DAMAGED_PENDING_REPAIR,
                    asset.id,
                    Asset.Status.AVAILABLE,
                    asset.department_id,
                ]
            else:
                update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id IS NULL AND assigned_to_user_id IS NULL
""".strip()
                update_params = [
                    Asset.Status.DAMAGED_PENDING_REPAIR,
                    asset.id,
                    Asset.Status.AVAILABLE,
                ]
            assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            audit_stmt = cls._build_audit_statement(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DAMAGE,
                from_status=Asset.Status.AVAILABLE,
                to_status=Asset.Status.DAMAGED_PENDING_REPAIR,
                reason=reason,
                from_user_id=None,
                to_user_id=None,
                from_department_id=asset.department_id,
                to_department_id=asset.department_id,
            )
            statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE tại kho mới có thể báo hỏng trực tiếp."
                )

            from_status = asset.status
            asset.status = Asset.Status.DAMAGED_PENDING_REPAIR
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def confirm_damage_report(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Confirm damage reported on an IN_USE asset by its holder.
        Transitions: IN_USE -> DAMAGED_PENDING_REPAIR.
        Retains holder for accountability.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do báo hỏng không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể xác nhận báo hỏng."
                )
            if asset.assigned_to_user_id is None:
                raise ConflictError("Tài sản không có người giữ để xác nhận báo hỏng.")

            statements = cls.build_confirm_damage_statements(asset, actor, reason)
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.IN_USE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể xác nhận báo hỏng."
                )

            if asset.assigned_to_user is None:
                raise ConflictError("Tài sản không có người giữ để xác nhận báo hỏng.")

            old_holder = asset.assigned_to_user
            from_status = asset.status
            asset.status = Asset.Status.DAMAGED_PENDING_REPAIR
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def send_maintenance(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Send a damaged asset for repair/maintenance.
        Transitions: DAMAGED_PENDING_REPAIR -> UNDER_MAINTENANCE.
        Preserves holder if any.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do gửi bảo trì không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.DAMAGED_PENDING_REPAIR:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản DAMAGED_PENDING_REPAIR mới có thể gửi đi bảo trì."
                )
            if asset.department_id is not None:
                update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id = %s
""".strip()
                update_params = [
                    Asset.Status.UNDER_MAINTENANCE,
                    asset.id,
                    Asset.Status.DAMAGED_PENDING_REPAIR,
                    asset.department_id,
                ]
            else:
                update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id IS NULL
""".strip()
                update_params = [
                    Asset.Status.UNDER_MAINTENANCE,
                    asset.id,
                    Asset.Status.DAMAGED_PENDING_REPAIR,
                ]
            assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            audit_stmt = cls._build_audit_statement(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.MAINTENANCE_START,
                from_status=Asset.Status.DAMAGED_PENDING_REPAIR,
                to_status=Asset.Status.UNDER_MAINTENANCE,
                reason=reason,
                from_user_id=asset.assigned_to_user_id,
                to_user_id=asset.assigned_to_user_id,
                from_department_id=asset.department_id,
                to_department_id=asset.department_id,
            )
            statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.DAMAGED_PENDING_REPAIR:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản DAMAGED_PENDING_REPAIR mới có thể gửi đi bảo trì."
                )

            from_status = asset.status
            asset.status = Asset.Status.UNDER_MAINTENANCE
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def complete_maintenance(
        cls,
        asset_id: int,
        actor: User,
        result: str,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Complete maintenance on an asset under maintenance.
        Transitions:
        - result == 'RETURN_TO_USER': UNDER_MAINTENANCE -> IN_USE (holder must still be active and in same dept)
        - result == 'RETURN_TO_STOCK': UNDER_MAINTENANCE -> AVAILABLE (holder cleared)
        """
        if result not in ("RETURN_TO_USER", "RETURN_TO_STOCK"):
            raise ValidationError(
                {"result": "Kết quả bảo trì phải là RETURN_TO_USER hoặc RETURN_TO_STOCK."}
            )
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do hoàn tất bảo trì không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            if asset.status != Asset.Status.UNDER_MAINTENANCE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản UNDER_MAINTENANCE mới có thể hoàn tất bảo trì."
                )

            if result == "RETURN_TO_USER":
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

                target_user_guard = (
                    "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s",
                    [holder.id, asset.department_id],
                )
                user_guard_check = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])

                update_sql = """
UPDATE assets_asset
SET status = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id = %s AND assigned_to_user_id = %s
""".strip()
                update_params = [
                    Asset.Status.IN_USE,
                    asset.id,
                    Asset.Status.UNDER_MAINTENANCE,
                    asset.department_id,
                    holder.id,
                ]
                assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                audit_stmt = cls._build_audit_statement(
                    asset=asset,
                    actor=actor,
                    action_type=AssetHistoryLog.ActionType.MAINTENANCE_END,
                    from_status=Asset.Status.UNDER_MAINTENANCE,
                    to_status=Asset.Status.IN_USE,
                    reason=reason,
                    from_user_id=holder.id,
                    to_user_id=holder.id,
                    from_department_id=asset.department_id,
                    to_department_id=asset.department_id,
                )
                statements = [
                    target_user_guard,
                    user_guard_check,
                    (update_sql, update_params),
                    assert_guard_stmt,
                    audit_stmt,
                ]
                return cls._execute_d1_mutation(statements, asset_id, using=using)
            else:
                old_holder_id = asset.assigned_to_user_id
                if asset.department_id is not None:
                    update_sql = """
UPDATE assets_asset
SET status = %s, assigned_to_user_id = NULL, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id = %s
""".strip()
                    update_params = [
                        Asset.Status.AVAILABLE,
                        asset.id,
                        Asset.Status.UNDER_MAINTENANCE,
                        asset.department_id,
                    ]
                else:
                    update_sql = """
UPDATE assets_asset
SET status = %s, assigned_to_user_id = NULL, updated_at = datetime('now')
WHERE id = %s AND status = %s AND department_id IS NULL
""".strip()
                    update_params = [
                        Asset.Status.AVAILABLE,
                        asset.id,
                        Asset.Status.UNDER_MAINTENANCE,
                    ]

                assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                audit_stmt = cls._build_audit_statement(
                    asset=asset,
                    actor=actor,
                    action_type=AssetHistoryLog.ActionType.MAINTENANCE_END,
                    from_status=Asset.Status.UNDER_MAINTENANCE,
                    to_status=Asset.Status.AVAILABLE,
                    reason=reason,
                    from_user_id=old_holder_id,
                    to_user_id=None,
                    from_department_id=asset.department_id,
                    to_department_id=asset.department_id,
                )
                statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
                return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

            if asset.status != Asset.Status.UNDER_MAINTENANCE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản UNDER_MAINTENANCE mới có thể hoàn tất bảo trì."
                )

            from_status = asset.status

            if result == "RETURN_TO_USER":
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
                asset.save(using=using)
            else:
                old_holder = asset.assigned_to_user
                to_status = Asset.Status.AVAILABLE
                from_user = old_holder
                to_user = None
                asset.status = to_status
                asset.assigned_to_user = None
                asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def transfer(
        cls,
        asset_id: int,
        actor: User,
        to_department_id: Optional[int],
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Transfer an in-stock asset between departments or to company pool.
        Strictly Admin-only.
        Transitions: AVAILABLE -> AVAILABLE (updates department).
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do điều chuyển không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True, using=using)
            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể điều chuyển."
                )
            if asset.assigned_to_user_id is not None:
                raise ConflictError("Tài sản đang có người giữ, không thể điều chuyển.")

            if to_department_id is not None:
                try:
                    dept_qs = Department.objects
                    if using:
                        dept_qs = dept_qs.using(using)
                    target_dept = dept_qs.get(pk=to_department_id)
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

            update_sql = """
UPDATE assets_asset
SET department_id = %s, updated_at = datetime('now')
WHERE id = %s AND status = %s AND assigned_to_user_id IS NULL AND (department_id = %s OR (department_id IS NULL AND %s IS NULL))
""".strip()
            update_params = [
                to_department_id,
                asset.id,
                Asset.Status.AVAILABLE,
                asset.department_id,
                asset.department_id,
            ]
            assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            audit_stmt = cls._build_audit_statement(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.TRANSFER,
                from_status=Asset.Status.AVAILABLE,
                to_status=Asset.Status.AVAILABLE,
                reason=reason,
                from_user_id=None,
                to_user_id=None,
                from_department_id=asset.department_id,
                to_department_id=to_department_id,
            )
            statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True, using=using)

            if asset.status != Asset.Status.AVAILABLE:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể điều chuyển."
                )

            if asset.assigned_to_user is not None:
                raise ConflictError("Tài sản đang có người giữ, không thể điều chuyển.")

            if to_department_id is not None:
                try:
                    dept_qs = Department.objects
                    if using:
                        dept_qs = dept_qs.using(using)
                    target_dept = dept_qs.get(pk=to_department_id)
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
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def propose_disposal(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        received_confirmed: bool = False,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Propose asset for disposal.
        Source statuses: AVAILABLE, DAMAGED_PENDING_REPAIR, UNDER_MAINTENANCE.
        If holder is present, received_confirmed=True is required. Clears holder.
        Transitions: (AVAILABLE | DAMAGED_PENDING_REPAIR | UNDER_MAINTENANCE) -> PENDING_DISPOSAL.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do đề xuất thanh lý không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)
            allowed_statuses = {
                Asset.Status.AVAILABLE,
                Asset.Status.DAMAGED_PENDING_REPAIR,
                Asset.Status.UNDER_MAINTENANCE,
            }
            if asset.status not in allowed_statuses:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, không thể đề xuất thanh lý."
                )

            if asset.assigned_to_user_id is not None and not received_confirmed:
                raise ValidationError(
                    {
                        "received_confirmed": (
                            "Tài sản đang có người giữ, bắt buộc phải xác nhận "
                            "đã nhận lại trước khi đề xuất thanh lý."
                        )
                    }
                )

            dept_clause = "department_id = %s" if asset.department_id is not None else "department_id IS NULL"
            user_clause = "assigned_to_user_id = %s" if asset.assigned_to_user_id is not None else "assigned_to_user_id IS NULL"

            update_sql = f"""
UPDATE assets_asset
SET status = %s, assigned_to_user_id = NULL, updated_at = datetime('now')
WHERE id = %s AND status = %s AND {dept_clause} AND {user_clause}
""".strip()
            update_params = [
                Asset.Status.PENDING_DISPOSAL,
                asset.id,
                asset.status,
            ]
            if asset.department_id is not None:
                update_params.append(asset.department_id)
            if asset.assigned_to_user_id is not None:
                update_params.append(asset.assigned_to_user_id)
            assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            audit_stmt = cls._build_audit_statement(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
                from_status=asset.status,
                to_status=Asset.Status.PENDING_DISPOSAL,
                reason=reason,
                from_user_id=asset.assigned_to_user_id,
                to_user_id=None,
                from_department_id=asset.department_id,
                to_department_id=asset.department_id,
            )
            statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, using=using)

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
            asset.save(using=using)

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
                using=using,
            )

            return asset

    @classmethod
    def confirm_disposal(
        cls,
        asset_id: int,
        actor: User,
        reason: str,
        using: Optional[str] = None,
    ) -> Asset:
        """
        Confirm disposal of asset pending disposal.
        Strictly Admin-only.
        Transitions: PENDING_DISPOSAL -> DISPOSED.
        """
        if not reason or not reason.strip():
            raise ValidationError({"reason": "Lý do xác nhận thanh lý không được để trống."})

        if cls._is_d1(using):
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True, using=using)
            if asset.status != Asset.Status.PENDING_DISPOSAL:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản PENDING_DISPOSAL mới có thể xác nhận thanh lý."
                )

            dept_clause = "department_id = %s" if asset.department_id is not None else "department_id IS NULL"

            update_sql = f"""
UPDATE assets_asset
SET status = %s, assigned_to_user_id = NULL, updated_at = datetime('now')
WHERE id = %s AND status = %s AND {dept_clause} AND assigned_to_user_id IS NULL
""".strip()
            update_params = [
                Asset.Status.DISPOSED,
                asset.id,
                Asset.Status.PENDING_DISPOSAL,
            ]
            if asset.department_id is not None:
                update_params.append(asset.department_id)
            assert_guard_stmt = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            audit_stmt = cls._build_audit_statement(
                asset=asset,
                actor=actor,
                action_type=AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
                from_status=Asset.Status.PENDING_DISPOSAL,
                to_status=Asset.Status.DISPOSED,
                reason=reason,
                from_user_id=None,
                to_user_id=None,
                from_department_id=asset.department_id,
                to_department_id=asset.department_id,
            )
            statements = [(update_sql, update_params), assert_guard_stmt, audit_stmt]
            return cls._execute_d1_mutation(statements, asset_id, using=using)

        with transaction.atomic(using=using):
            asset = cls._get_locked_asset(asset_id, actor, admin_only=True, using=using)

            if asset.status != Asset.Status.PENDING_DISPOSAL:
                raise ConflictError(
                    f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản PENDING_DISPOSAL mới có thể xác nhận thanh lý."
                )

            from_status = asset.status
            asset.status = Asset.Status.DISPOSED
            asset.assigned_to_user = None
            asset.save(using=using)

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
                using=using,
            )

            return asset
