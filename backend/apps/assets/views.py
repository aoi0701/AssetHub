from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.assets.models import Asset
from apps.assets.serializers import (
    AssetAllocateActionSerializer,
    AssetCompleteMaintenanceActionSerializer,
    AssetConfirmDisposalActionSerializer,
    AssetCreateSerializer,
    AssetHistoryLogSerializer,
    AssetMarkDamagedActionSerializer,
    AssetProposeDisposalActionSerializer,
    AssetRevokeActionSerializer,
    AssetSendMaintenanceActionSerializer,
    AssetSerializer,
    AssetTransferActionSerializer,
    AssetUpdateSerializer,
)
from apps.assets.services import AssetService
from apps.authentication.models import User
from apps.common.exceptions import ConflictError


class AssetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AssetPermission(permissions.BasePermission):
    """
    Asset access permission:
    - Admin: Full access (list, detail, create, patch, all actions, history).
    - Manager:
      - Read access to assets in own department (list, detail).
      - History access to assets in own department.
      - Action access for: allocate, revoke, mark_damaged, send_maintenance,
        complete_maintenance, propose_disposal in own department.
      - Action transfer & confirm_disposal: forbidden (403).
      - Other departments or company pool: 404 (enforced via queryset scoping).
      - Create and patch: forbidden (403).
    - User:
      - Read access to assigned assets (list, detail).
      - Cannot access history (403).
      - Cannot execute any action, create, or patch (403).
    """

    ACTION_NAMES = {
        'allocate',
        'revoke',
        'mark_damaged',
        'send_maintenance',
        'complete_maintenance',
        'transfer',
        'propose_disposal',
        'confirm_disposal',
    }

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        user = request.user
        if user.role == User.Role.ADMIN:
            return True

        if user.role == User.Role.USER:
            # User only has read access to list/retrieve; forbidden for history and any actions
            if view.action in ['list', 'retrieve']:
                return True
            return False

        if user.role == User.Role.MANAGER:
            # Manager cannot create or patch assets
            if view.action in ['create', 'update', 'partial_update']:
                return False
            # Manager can invoke detail actions and safe views (queryset scoping handles department)
            if view.action in self.ACTION_NAMES or view.action in ['list', 'retrieve', 'history']:
                return True
            return False

        return False

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False

        user = request.user
        if user.role == User.Role.ADMIN:
            return True

        if user.role == User.Role.USER:
            if view.action == 'history':
                return False
            return obj.assigned_to_user_id == user.id

        if user.role == User.Role.MANAGER:
            # Must match department
            if not user.department_id or obj.department_id != user.department_id:
                return False
            # transfer and confirm_disposal are Admin-only
            if view.action in ['transfer', 'confirm_disposal']:
                return False
            return True

        return False


@extend_schema_view(
    list=extend_schema(
        summary="Danh sách tài sản (Phân quyền theo vai trò)",
        tags=["assets"],
        parameters=[
            OpenApiParameter(name="category_id", type=int, description="Lọc theo danh mục tài sản"),
            OpenApiParameter(name="status", type=str, description="Lọc theo trạng thái"),
            OpenApiParameter(name="department_id", type=str, description="Lọc theo phòng ban hoặc 'unallocated' (Chỉ Admin)"),
            OpenApiParameter(name="search", type=str, description="Tìm kiếm theo mã, tên, hoặc số serial"),
        ],
        responses={200: AssetSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Chi tiết tài sản",
        tags=["assets"],
        responses={200: AssetSerializer},
    ),
    create=extend_schema(
        summary="Tạo hồ sơ tài sản mới (Chỉ Admin)",
        tags=["assets"],
        request=AssetCreateSerializer,
        responses={201: AssetSerializer},
    ),
    partial_update=extend_schema(
        summary="Cập nhật thông tin tài sản (Chỉ Admin, strict allowlist)",
        tags=["assets"],
        request=AssetUpdateSerializer,
        responses={200: AssetSerializer},
    ),
)
class AssetViewSet(viewsets.ModelViewSet):
    """
    Asset management API:
    - Admin: Full CRUD (GET list, POST create, GET detail, PATCH update) and full action permissions. Strictly no DELETE or PUT.
    - Manager: Department-scoped read access and operational lifecycle actions.
    - User: Read-only access scoped to currently held assets.
    """
    permission_classes = [permissions.IsAuthenticated, AssetPermission]
    pagination_class = AssetPagination
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Asset.objects.none()

        # Role-based scoping
        if user.role == User.Role.ADMIN:
            queryset = Asset.objects.all()
        elif user.role == User.Role.MANAGER:
            if user.department_id:
                queryset = Asset.objects.filter(department_id=user.department_id)
            else:
                queryset = Asset.objects.none()
        elif user.role == User.Role.USER:
            queryset = Asset.objects.filter(assigned_to_user_id=user.id)
        else:
            return Asset.objects.none()

        queryset = queryset.select_related('category', 'department', 'assigned_to_user')

        # Filters
        category_id = self.request.query_params.get('category_id')
        if category_id and category_id.isdigit():
            queryset = queryset.filter(category_id=int(category_id))

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param.strip().upper())

        # Admin-only department filter
        if user.role == User.Role.ADMIN:
            dept_param = self.request.query_params.get('department_id')
            if dept_param is not None:
                dept_str = str(dept_param).strip().lower()
                if dept_str in ('unallocated', 'null', 'none'):
                    queryset = queryset.filter(department__isnull=True)
                elif dept_str.isdigit():
                    queryset = queryset.filter(department_id=int(dept_str))

        # Search across asset_code, name, serial_number
        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            search_str = search.strip()
            queryset = queryset.filter(
                Q(asset_code__icontains=search_str)
                | Q(name__icontains=search_str)
                | Q(serial_number__icontains=search_str)
            )

        return queryset.order_by('-created_at', '-id')

    def get_serializer_class(self):
        if self.action == 'create':
            return AssetCreateSerializer
        if self.action in ['update', 'partial_update']:
            return AssetUpdateSerializer
        return AssetSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        asset = serializer.save()
        output_serializer = AssetSerializer(asset)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == Asset.Status.DISPOSED:
            raise ConflictError("Tài sản đã thanh lý (DISPOSED), không thể sửa đổi thông tin.")

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        asset = serializer.save()
        output_serializer = AssetSerializer(asset)
        return Response(output_serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "Xóa tài sản bị cấm tuyệt đối theo chính sách hệ thống."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # -----------------------------------------------------------------------
    # Lifecycle Action Endpoints
    # -----------------------------------------------------------------------

    @extend_schema(
        summary="Cấp phát tài sản cho nhân viên",
        tags=["assets"],
        request=AssetAllocateActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='allocate')
    def allocate(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetAllocateActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.allocate(
            asset_id=asset.id,
            actor=request.user,
            assigned_to_user_id=serializer.validated_data['assigned_to_user_id'],
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Thu hồi tài sản",
        tags=["assets"],
        request=AssetRevokeActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetRevokeActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.revoke(
            asset_id=asset.id,
            actor=request.user,
            received_confirmed=serializer.validated_data['received_confirmed'],
            condition=serializer.validated_data['condition'],
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Báo hỏng tài sản tại kho",
        tags=["assets"],
        request=AssetMarkDamagedActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='mark-damaged')
    def mark_damaged(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetMarkDamagedActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.mark_damaged(
            asset_id=asset.id,
            actor=request.user,
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Gửi tài sản đi bảo dưỡng/sửa chữa",
        tags=["assets"],
        request=AssetSendMaintenanceActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='send-maintenance')
    def send_maintenance(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetSendMaintenanceActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.send_maintenance(
            asset_id=asset.id,
            actor=request.user,
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Hoàn tất bảo dưỡng/sửa chữa",
        tags=["assets"],
        request=AssetCompleteMaintenanceActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='complete-maintenance')
    def complete_maintenance(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetCompleteMaintenanceActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.complete_maintenance(
            asset_id=asset.id,
            actor=request.user,
            result=serializer.validated_data['result'],
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Điều chuyển tài sản sang phòng ban khác hoặc kho công ty (Chỉ Admin)",
        tags=["assets"],
        request=AssetTransferActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='transfer')
    def transfer(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetTransferActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.transfer(
            asset_id=asset.id,
            actor=request.user,
            to_department_id=serializer.validated_data['to_department_id'],
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Đề xuất thanh lý tài sản",
        tags=["assets"],
        request=AssetProposeDisposalActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='propose-disposal')
    def propose_disposal(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetProposeDisposalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.propose_disposal(
            asset_id=asset.id,
            actor=request.user,
            reason=serializer.validated_data['reason'],
            received_confirmed=serializer.validated_data.get('received_confirmed', False),
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Xác nhận thanh lý tài sản (Chỉ Admin)",
        tags=["assets"],
        request=AssetConfirmDisposalActionSerializer,
        responses={200: AssetSerializer},
    )
    @action(detail=True, methods=['post'], url_path='confirm-disposal')
    def confirm_disposal(self, request, pk=None):
        asset = self.get_object()
        serializer = AssetConfirmDisposalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_asset = AssetService.confirm_disposal(
            asset_id=asset.id,
            actor=request.user,
            reason=serializer.validated_data['reason'],
        )
        return Response(AssetSerializer(updated_asset).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Lịch sử vòng đời tài sản (Admin toàn quyền, Manager trong phòng ban)",
        tags=["assets"],
        responses={200: AssetHistoryLogSerializer(many=True)},
    )
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        asset = self.get_object()
        ordering = request.query_params.get('ordering', 'created_at')
        if ordering == '-created_at':
            logs = asset.history_logs.all().order_by('-created_at', '-id')
        else:
            logs = asset.history_logs.all().order_by('created_at', 'id')
        serializer = AssetHistoryLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
