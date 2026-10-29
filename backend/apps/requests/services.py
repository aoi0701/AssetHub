from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError
from apps.requests.models import AssetRequest


class RequestService:
    """
    Business service executing request workflows (creation, approval, rejection).
    Maintains strict lock order: AssetRequest -> Asset.
    All state transitions share AssetService to ensure transactional integrity and audit logging.
    """

    @classmethod
    def _get_locked_request(cls, request_id: int, actor: User) -> AssetRequest:
        """
        Acquire row-level lock on AssetRequest and enforce scoping permissions.
        User: 403 Forbidden.
        Manager of different department: 404 Not Found.
        """
        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Yêu cầu xác thực.")

        if actor.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền phê duyệt hoặc từ chối phiếu yêu cầu.")

        try:
            req = AssetRequest.objects.select_for_update().get(pk=request_id)
        except AssetRequest.DoesNotExist:
            raise NotFound("Không tìm thấy phiếu yêu cầu.")

        if actor.role == User.Role.MANAGER:
            if req.department_id != actor.department_id:
                raise NotFound("Không tìm thấy phiếu yêu cầu trong phạm vi phòng ban được phân công.")

        return req

    @classmethod
    def approve_request(cls, request_id: int, actor: User, data: dict) -> AssetRequest:
        """
        Approve an AssetRequest within an atomic transaction.
        Enforces locking order:
        1. Lock AssetRequest with select_for_update().
        2. Verify request status is PENDING and requester is active in same department.
        3. Lock Asset with select_for_update() and transition state via AssetService.
        4. Update request status to APPROVED and record reviewer details.
        """
        with transaction.atomic():
            req = cls._get_locked_request(request_id, actor)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            # Validate requester status & department at time of review
            requester = req.requester
            if not requester.is_active:
                raise ConflictError("Người yêu cầu hiện không còn hoạt động, không thể duyệt phiếu.")

            if requester.department_id != req.department_id:
                raise ConflictError("Người yêu cầu đã chuyển sang phòng ban khác, không thể duyệt phiếu.")

            if req.request_type == AssetRequest.RequestType.ALLOCATE:
                selected_asset_id = data.get('selected_asset_id')
                if not selected_asset_id:
                    raise ValidationError({"selected_asset_id": "Vui lòng chọn tài sản để cấp phát."})

                try:
                    asset = Asset.objects.select_for_update().get(pk=selected_asset_id)
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
                )

                req.asset = allocated_asset
                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save()

            elif req.request_type == AssetRequest.RequestType.RETURN:
                received_confirmed = data.get('received_confirmed', False)
                if not received_confirmed:
                    raise ValidationError(
                        {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi duyệt trả."}
                    )

                condition = data.get('condition')
                if condition not in ('GOOD', 'DAMAGED'):
                    raise ValidationError(
                        {"condition": "Tình trạng tài sản không hợp lệ (phải là GOOD hoặc DAMAGED)."}
                    )

                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần thu hồi."})

                asset = Asset.objects.select_for_update().get(pk=req.asset_id)

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
                )

                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save()

            elif req.request_type == AssetRequest.RequestType.DAMAGE_REPORT:
                if not req.asset_id:
                    raise ValidationError({"asset": "Phiếu yêu cầu không có thông tin tài sản cần báo hỏng."})

                asset = Asset.objects.select_for_update().get(pk=req.asset_id)

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
                )

                req.status = AssetRequest.Status.APPROVED
                req.reviewed_by = actor
                req.reviewed_at = timezone.now()
                req.save()

            return req

    @classmethod
    def reject_request(cls, request_id: int, actor: User, rejection_reason: str) -> AssetRequest:
        """
        Reject an AssetRequest within an atomic transaction.
        Asset remains untouched.
        """
        if not rejection_reason or not rejection_reason.strip():
            raise ValidationError({"rejection_reason": "Lý do từ chối không được để trống."})

        with transaction.atomic():
            req = cls._get_locked_request(request_id, actor)

            if req.status != AssetRequest.Status.PENDING:
                raise ConflictError(
                    f"Phiếu yêu cầu đã được xử lý trước đó (trạng thái hiện tại: {req.status})."
                )

            req.status = AssetRequest.Status.REJECTED
            req.rejection_reason = rejection_reason.strip()
            req.reviewed_by = actor
            req.reviewed_at = timezone.now()
            req.save()

            return req
