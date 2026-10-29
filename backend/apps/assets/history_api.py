from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination

from apps.assets.models import AssetHistoryLog


class PersonalAssetHistoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class PersonalAssetHistorySerializer(serializers.ModelSerializer):
    """
    Safe serializer for personal asset history.
    Exposes only allowed non-sensitive fields and friendly localized message.
    """
    asset_id = serializers.IntegerField(read_only=True)
    message = serializers.SerializerMethodField()

    class Meta:
        model = AssetHistoryLog
        fields = [
            'id',
            'asset_id',
            'asset_code_snapshot',
            'asset_name_snapshot',
            'action_type',
            'from_status',
            'to_status',
            'created_at',
            'message',
        ]
        read_only_fields = fields

    def get_message(self, obj: AssetHistoryLog) -> str:
        request = self.context.get('request')
        user_id = request.user.id if (request and request.user) else None

        if obj.action_type == AssetHistoryLog.ActionType.ALLOCATE and obj.to_user_id == user_id:
            return "Tài sản được cấp phát cho bạn"
        if obj.action_type == AssetHistoryLog.ActionType.RETURN and obj.from_user_id == user_id:
            return "Bạn đã hoàn trả tài sản"
        return "Sự kiện tài sản liên quan đến bạn"


@extend_schema(
    summary="Lịch sử tài sản cá nhân của người dùng đăng nhập",
    tags=["assets"],
    responses={200: PersonalAssetHistorySerializer(many=True)},
)
class PersonalAssetHistoryView(generics.ListAPIView):
    """
    Read-only personal asset history for the authenticated user.
    Strictly scoped to events where the user was recipient (to_user) or returner (from_user).
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = PersonalAssetHistoryPagination
    serializer_class = PersonalAssetHistorySerializer

    def get_queryset(self):
        # Prevent querying other users' history
        if 'user' in self.request.query_params or 'user_id' in self.request.query_params:
            raise PermissionDenied("Không có quyền truy vấn lịch sử tài sản của người dùng khác.")

        user = self.request.user
        return AssetHistoryLog.objects.filter(
            Q(from_user=user) | Q(to_user=user)
        ).order_by('-created_at', '-id')
