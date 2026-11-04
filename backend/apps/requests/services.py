from typing import Optional
from django.db import DatabaseError, connections, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.requests.models import AssetRequest
from assethub_api.backends.d1 import d1_batch


class RequestService:
    """
    Business service executing request workflows (creation, approval, rejection).
    Maintains strict lock order: AssetRequest -> Asset.
    All state transitions share AssetService to ensure transactional integrity and audit logging.
    Supports both PostgreSQL (row-level locks with transaction.atomic) and
    Cloudflare D1 (guarded atomic batch OCC execution with _assert_guard).
    """

    @classmethod
    def _is_d1(cls, using: Optional[str] = None) -> bool:
        """
        Detects whether target connection is Cloudflare D1.
        """
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

    @classmethod
    def _get_locked_request(
        cls,
        request_id: int,
        actor: User,
        using: Optional[str] = None,
    ) -> AssetRequest:
        """
        Acquire row-level lock on AssetRequest in PG (or direct get in D1) and enforce scoping permissions.
        User: 403 Forbidden.
        Manager of different department: 404 Not Found.
        """
        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Yêu cầu xác thực.")

        if actor.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền phê duyệt hoặc từ chối phiếu yêu cầu.")

        qs = AssetRequest.objects
        if using:
            qs = qs.using(using)
        if not cls._is_d1(using):
            qs = qs.select_for_update()

        try:
            req = qs.get(pk=request_id)
        except AssetRequest.DoesNotExist:
            raise NotFound("Không tìm thấy phiếu yêu cầu.")

        if actor.role == User.Role.MANAGER:
            if req.department_id != actor.department_id:
                raise NotFound("Không tìm thấy phiếu yêu cầu trong phạm vi phòng ban được phân công.")

        return req

    @classmethod
    def approve_request(
        cls,
        request_id: int,
        actor: User,
        data: dict,
        using: Optional[str] = None,
    ) -> AssetRequest:
        """
        Approve an AssetRequest within an atomic transaction / D1 batch.
        Enforces locking order:
        1. Lock AssetRequest with select_for_update() (or direct get in D1).
        2. Verify request status is PENDING and requester is active in same department.
        3. Lock Asset with select_for_update() (or OCC guard in D1) and transition state via AssetService.
        4. Update request status to APPROVED and record reviewer details.
        """
        if cls._is_d1(using):
            req = cls._get_locked_request(request_id, actor, using=using)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            # Validate requester status & department at time of review
            requester = getattr(req, "requester", None)
            if requester is None or isinstance(requester, int):
                requester_qs = User.objects
                if using:
                    requester_qs = requester_qs.using(using)
                try:
                    requester = requester_qs.get(pk=req.requester_id)
                except User.DoesNotExist:
                    raise ConflictError("Người yêu cầu không tồn tại.")

            if not requester.is_active:
                raise ConflictError("Người yêu cầu hiện không còn hoạt động, không thể duyệt phiếu.")

            if requester.department_id != req.department_id:
                raise ConflictError("Người yêu cầu đã chuyển sang phòng ban khác, không thể duyệt phiếu.")

            if req.request_type == AssetRequest.RequestType.ALLOCATE:
                selected_asset_id = data.get("selected_asset_id")
                if not selected_asset_id:
                    raise ValidationError({"selected_asset_id": "Vui lòng chọn tài sản để cấp phát."})

                asset_qs = Asset.objects
                if using:
                    asset_qs = asset_qs.using(using)
                try:
                    asset = asset_qs.get(pk=selected_asset_id)
                except Asset.DoesNotExist:
                    raise ValidationError({"selected_asset_id": "Tài sản được chọn không tồn tại."})

                if asset.category_id != req.requested_category_id:
                    raise ValidationError(
                        {"selected_asset_id": "Danh mục của tài sản không khớp với danh mục được yêu cầu."}
                    )

                if asset.department_id != req.department_id:
                    if actor.role == User.Role.MANAGER:
                        raise NotFound("Không tìm thấy tài sản trong phạm vi phòng ban được phân công.")
                    raise ValidationError(
                        {"selected_asset_id": "Tài sản không thuộc phòng ban của phiếu yêu cầu."}
                    )

                if asset.status != Asset.Status.AVAILABLE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
                    )

                asset_stmts = AssetService.build_allocate_statements(
                    asset,
                    actor,
                    requester,
                    f"Duyệt yêu cầu cấp phát {req.request_code}: {req.reason}",
                )
                req_stmt = (
                    "UPDATE requests_assetrequest SET status = %s, reviewed_by_id = %s, reviewed_at = datetime('now'), asset_id = %s WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s",
                    [
                        AssetRequest.Status.APPROVED,
                        actor.id,
                        asset.id,
                        req.id,
                        AssetRequest.Status.PENDING,
                        req.requester_id,
                        req.department_id,
                    ],
                )
                req_guard = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                requester_guard = (
                    "UPDATE authentication_user SET id = id WHERE id = %s AND is_active = 1 AND department_id = %s",
                    [requester.id, req.department_id],
                )
                requester_guard_check = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                batch = [req_stmt, req_guard, requester_guard, requester_guard_check] + asset_stmts

            elif req.request_type == AssetRequest.RequestType.RETURN:
                received_confirmed = data.get("received_confirmed", False)
                if not received_confirmed:
                    raise ValidationError(
                        {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi duyệt trả."}
                    )

                condition = data.get("condition")
                if condition not in ("GOOD", "DAMAGED"):
                    raise ValidationError(
                        {"condition": "Tình trạng tài sản không hợp lệ (phải là GOOD hoặc DAMAGED)."}
                    )

                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần thu hồi."})

                asset = getattr(req, "asset", None)
                if asset is None or isinstance(asset, int):
                    asset_qs = Asset.objects
                    if using:
                        asset_qs = asset_qs.using(using)
                    try:
                        asset = asset_qs.get(pk=req.asset_id)
                    except Asset.DoesNotExist:
                        asset = None

                if not asset:
                    raise ValidationError({"asset": "Tài sản không tồn tại."})

                if asset.status != Asset.Status.IN_USE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể thu hồi."
                    )

                if asset.assigned_to_user_id != requester.id:
                    raise ConflictError("Tài sản không còn được giữ bởi người yêu cầu.")

                if asset.department_id != req.department_id:
                    raise ConflictError("Tài sản đã chuyển sang phòng ban khác.")

                asset_stmts = AssetService.build_revoke_statements(
                    asset,
                    actor,
                    condition,
                    f"Duyệt yêu cầu trả tài sản {req.request_code}: {req.reason}",
                )
                req_stmt = (
                    "UPDATE requests_assetrequest SET status = %s, reviewed_by_id = %s, reviewed_at = datetime('now') WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s",
                    [AssetRequest.Status.APPROVED, actor.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id],
                )
                req_guard = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                batch = [req_stmt, req_guard] + asset_stmts

            elif req.request_type == AssetRequest.RequestType.DAMAGE_REPORT:
                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần báo hỏng."})

                asset = getattr(req, "asset", None)
                if asset is None or isinstance(asset, int):
                    asset_qs = Asset.objects
                    if using:
                        asset_qs = asset_qs.using(using)
                    try:
                        asset = asset_qs.get(pk=req.asset_id)
                    except Asset.DoesNotExist:
                        asset = None

                if not asset:
                    raise ValidationError({"asset": "Tài sản không tồn tại."})

                if asset.status != Asset.Status.IN_USE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể xác nhận báo hỏng."
                    )

                if asset.assigned_to_user_id != requester.id:
                    raise ConflictError("Tài sản không còn được giữ bởi người yêu cầu.")

                if asset.department_id != req.department_id:
                    raise ConflictError("Tài sản đã chuyển sang phòng ban khác.")

                asset_stmts = AssetService.build_confirm_damage_statements(
                    asset,
                    actor,
                    f"Xác nhận báo hỏng theo phiếu {req.request_code}: {req.reason}",
                )
                req_stmt = (
                    "UPDATE requests_assetrequest SET status = %s, reviewed_by_id = %s, reviewed_at = datetime('now') WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s",
                    [AssetRequest.Status.APPROVED, actor.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id],
                )
                req_guard = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
                batch = [req_stmt, req_guard] + asset_stmts
            else:
                raise ValidationError({"request_type": f"Loại yêu cầu không hợp lệ: {req.request_type}"})

            try:
                d1_batch(batch, using=using or "default")
            except DatabaseError as e:
                err_msg = str(e)
                if (
                    "_assert_guard" in err_msg
                    or "CHECK constraint failed" in err_msg
                    or "rows_affected" in err_msg
                    or "Constraint Conflict" in err_msg
                ):
                    raise ConflictError(
                        "Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."
                    ) from e
                raise

            req_qs = AssetRequest.objects
            if using:
                req_qs = req_qs.using(using)
            return req_qs.get(pk=request_id)

        with transaction.atomic(using=using):
            req = cls._get_locked_request(request_id, actor, using=using)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            # Validate requester status & department at time of review
            requester = getattr(req, "requester", None)
            if requester is None or isinstance(requester, int):
                requester_qs = User.objects
                if using:
                    requester_qs = requester_qs.using(using)
                try:
                    requester = requester_qs.get(pk=req.requester_id)
                except User.DoesNotExist:
                    raise ConflictError("Người yêu cầu không tồn tại.")

            if not requester.is_active:
                raise ConflictError("Người yêu cầu hiện không còn hoạt động, không thể duyệt phiếu.")

            if requester.department_id != req.department_id:
                raise ConflictError("Người yêu cầu đã chuyển sang phòng ban khác, không thể duyệt phiếu.")

            if req.request_type == AssetRequest.RequestType.ALLOCATE:
                selected_asset_id = data.get("selected_asset_id")
                if not selected_asset_id:
                    raise ValidationError({"selected_asset_id": "Vui lòng chọn tài sản để cấp phát."})

                asset_qs = Asset.objects
                if using:
                    asset_qs = asset_qs.using(using)
                try:
                    asset = asset_qs.select_for_update().get(pk=selected_asset_id)
                except Asset.DoesNotExist:
                    raise ValidationError({"selected_asset_id": "Tài sản được chọn không tồn tại."})

                if asset.category_id != req.requested_category_id:
                    raise ValidationError(
                        {"selected_asset_id": "Danh mục của tài sản không khớp với danh mục được yêu cầu."}
                    )

                if asset.department_id != req.department_id:
                    if actor.role == User.Role.MANAGER:
                        raise NotFound("Không tìm thấy tài sản trong phạm vi phòng ban được phân công.")
                    raise ValidationError(
                        {"selected_asset_id": "Tài sản không thuộc phòng ban của phiếu yêu cầu."}
                    )

                if asset.status != Asset.Status.AVAILABLE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản AVAILABLE mới có thể cấp phát."
                    )

                # Transition asset and record ALLOCATE audit log via AssetService
                allocated_asset = AssetService.allocate(
                    asset_id=asset.id,
                    actor=actor,
                    assigned_to_user_id=requester.id,
                    reason=f"Duyệt yêu cầu cấp phát {req.request_code}: {req.reason}",
                    using=using,
                )

                req.asset = allocated_asset
                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save(using=using)

            elif req.request_type == AssetRequest.RequestType.RETURN:
                received_confirmed = data.get("received_confirmed", False)
                if not received_confirmed:
                    raise ValidationError(
                        {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi duyệt trả."}
                    )

                condition = data.get("condition")
                if condition not in ("GOOD", "DAMAGED"):
                    raise ValidationError(
                        {"condition": "Tình trạng tài sản không hợp lệ (phải là GOOD hoặc DAMAGED)."}
                    )

                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần thu hồi."})

                asset = getattr(req, "asset", None)
                if asset is None or isinstance(asset, int):
                    asset_qs = Asset.objects
                    if using:
                        asset_qs = asset_qs.using(using)
                    try:
                        asset = asset_qs.select_for_update().get(pk=req.asset_id)
                    except Asset.DoesNotExist:
                        asset = None

                if not asset:
                    raise ValidationError({"asset": "Tài sản không tồn tại."})

                if asset.status != Asset.Status.IN_USE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể thu hồi."
                    )

                if asset.assigned_to_user_id != requester.id:
                    raise ConflictError("Tài sản không còn được giữ bởi người yêu cầu.")

                if asset.department_id != req.department_id:
                    raise ConflictError("Tài sản đã chuyển sang phòng ban khác.")

                # Transition asset and record RETURN audit log via AssetService
                AssetService.revoke(
                    asset_id=asset.id,
                    actor=actor,
                    received_confirmed=True,
                    condition=condition,
                    reason=f"Duyệt yêu cầu trả tài sản {req.request_code}: {req.reason}",
                    using=using,
                )

                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save(using=using)

            elif req.request_type == AssetRequest.RequestType.DAMAGE_REPORT:
                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần báo hỏng."})

                asset = getattr(req, "asset", None)
                if asset is None or isinstance(asset, int):
                    asset_qs = Asset.objects
                    if using:
                        asset_qs = asset_qs.using(using)
                    try:
                        asset = asset_qs.select_for_update().get(pk=req.asset_id)
                    except Asset.DoesNotExist:
                        asset = None

                if not asset:
                    raise ValidationError({"asset": "Tài sản không tồn tại."})

                if asset.status != Asset.Status.IN_USE:
                    raise ConflictError(
                        f"Tài sản đang ở trạng thái {asset.status}, chỉ tài sản IN_USE mới có thể xác nhận báo hỏng."
                    )

                if asset.assigned_to_user_id != requester.id:
                    raise ConflictError("Tài sản không còn được giữ bởi người yêu cầu.")

                if asset.department_id != req.department_id:
                    raise ConflictError("Tài sản đã chuyển sang phòng ban khác.")

                # Transition asset and record DAMAGE audit log via AssetService
                AssetService.confirm_damage_report(
                    asset_id=asset.id,
                    actor=actor,
                    reason=f"Xác nhận báo hỏng theo phiếu {req.request_code}: {req.reason}",
                    using=using,
                )

                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save(using=using)

            return req

    @classmethod
    def reject_request(
        cls,
        request_id: int,
        actor: User,
        rejection_reason: str,
        using: Optional[str] = None,
    ) -> AssetRequest:
        """
        Reject an AssetRequest within an atomic transaction / D1 batch.
        Asset remains untouched.
        """
        if not rejection_reason or not rejection_reason.strip():
            raise ValidationError({"rejection_reason": "Lý do từ chối không được để trống."})

        if cls._is_d1(using):
            req = cls._get_locked_request(request_id, actor, using=using)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            stmt1 = (
                "UPDATE requests_assetrequest SET status = %s, rejection_reason = %s, reviewed_by_id = %s, reviewed_at = datetime('now') WHERE id = %s AND status = %s AND requester_id = %s AND department_id = %s",
                [AssetRequest.Status.REJECTED, rejection_reason.strip(), actor.id, req.id, AssetRequest.Status.PENDING, req.requester_id, req.department_id],
            )
            stmt2 = ("INSERT INTO _assert_guard (rows_affected) VALUES (changes());", [])
            try:
                d1_batch([stmt1, stmt2], using=using or "default")
            except DatabaseError as e:
                err_msg = str(e)
                if (
                    "_assert_guard" in err_msg
                    or "CHECK constraint failed" in err_msg
                    or "rows_affected" in err_msg
                    or "Constraint Conflict" in err_msg
                ):
                    raise ConflictError(
                        "Xung đột xử lý phiếu yêu cầu hoặc tài sản đã thay đổi."
                    ) from e
                raise

            req_qs = AssetRequest.objects
            if using:
                req_qs = req_qs.using(using)
            return req_qs.get(pk=request_id)

        with transaction.atomic(using=using):
            req = cls._get_locked_request(request_id, actor, using=using)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            req.status = AssetRequest.Status.REJECTED
            req.rejection_reason = rejection_reason.strip()
            req.reviewed_by = actor
            req.reviewed_at = timezone.now()
            req.save(using=using)

            return req
