from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.assets.models import Asset
from apps.common.exceptions import ConflictError
from apps.departments.models import Category
from apps.requests.models import AssetRequest


class AssetRequestSerializer(serializers.ModelSerializer):
    """
    Detailed read serializer for AssetRequest responses.
    Exposes foreign key IDs alongside readable descriptive names.
    """
    requester_id = serializers.IntegerField(source='requester.id', read_only=True)
    requester_name = serializers.CharField(source='requester.full_name', read_only=True)
    requester_username = serializers.CharField(source='requester.username', read_only=True)

    asset_id = serializers.IntegerField(source='asset.id', read_only=True, allow_null=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True, allow_null=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True, allow_null=True)

    requested_category_id = serializers.IntegerField(
        source='requested_category.id', read_only=True, allow_null=True
    )
    requested_category_name = serializers.CharField(
        source='requested_category.name', read_only=True, allow_null=True
    )

    department_id = serializers.IntegerField(source='department.id', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    department_code = serializers.CharField(source='department.code', read_only=True)

    reviewed_by_id = serializers.IntegerField(source='reviewed_by.id', read_only=True, allow_null=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.full_name', read_only=True, allow_null=True)
    reviewed_by_username = serializers.CharField(source='reviewed_by.username', read_only=True, allow_null=True)

    class Meta:
        model = AssetRequest
        fields = [
            'id',
            'request_code',
            'request_type',
            'requester',
            'requester_id',
            'requester_name',
            'requester_username',
            'asset',
            'asset_id',
            'asset_code',
            'asset_name',
            'requested_category',
            'requested_category_id',
            'requested_category_name',
            'department',
            'department_id',
            'department_name',
            'department_code',
            'reason',
            'status',
            'reviewed_by',
            'reviewed_by_id',
            'reviewed_by_name',
            'reviewed_by_username',
            'reviewed_at',
            'rejection_reason',
            'created_at',
        ]
        read_only_fields = fields


class AssetRequestCreateSerializer(serializers.Serializer):
    """
    Serializer for creating an AssetRequest.
    - Sets requester = request.user, department = request.user.department.
    - ALLOCATE requires requested_category_id and non-empty reason; client must NOT send asset_id.
    - RETURN requires asset_id; asset must be IN_USE and assigned to requester.
    - DAMAGE_REPORT requires asset_id; asset must be IN_USE and assigned to requester.
    """
    request_type = serializers.ChoiceField(
        choices=AssetRequest.RequestType.choices,
        required=True,
    )
    requested_category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source='requested_category',
        required=False,
        allow_null=True,
        default=None,
    )
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(),
        source='asset',
        required=False,
        allow_null=True,
        default=None,
    )
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise ValidationError("Lý do không được để trống.")
        return val

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if not user or not user.is_authenticated:
            raise ValidationError("Yêu cầu xác thực trước khi gửi phiếu.")

        if not user.department:
            raise ValidationError(
                {"department": "Người dùng chưa được phân công vào phòng ban nào, không thể gửi yêu cầu."}
            )

        request_type = attrs.get('request_type')
        initial_data = self.initial_data

        if request_type == AssetRequest.RequestType.ALLOCATE:
            # Client MUST NOT send asset_id
            if 'asset_id' in initial_data or 'asset' in initial_data:
                raise ValidationError(
                    {"asset_id": "Client không được gửi asset_id khi yêu cầu cấp phát (ALLOCATE)."}
                )
            if not attrs.get('requested_category'):
                raise ValidationError(
                    {"requested_category_id": "Vui lòng chọn danh mục tài sản cần cấp phát."}
                )

        elif request_type == AssetRequest.RequestType.RETURN:
            asset = attrs.get('asset')
            if not asset:
                raise ValidationError({"asset_id": "Vui lòng chọn tài sản cần thu hồi / trả lại."})

            if asset.status != Asset.Status.IN_USE:
                raise ValidationError(
                    {"asset_id": "Chỉ có thể tạo yêu cầu trả cho tài sản đang sử dụng (IN_USE)."}
                )

            if asset.assigned_to_user_id != user.id:
                raise ValidationError(
                    {"asset_id": "Tài sản không thuộc quyền sở hữu/sử dụng của bạn."}
                )

            # Prevent duplicate pending RETURN request
            if AssetRequest.objects.filter(
                requester=user,
                request_type=AssetRequest.RequestType.RETURN,
                asset=asset,
                status=AssetRequest.Status.PENDING,
            ).exists():
                raise ConflictError("Bạn đã có một yêu cầu trả tài sản này đang chờ duyệt.")

        elif request_type == AssetRequest.RequestType.DAMAGE_REPORT:
            asset = attrs.get('asset')
            if not asset:
                raise ValidationError({"asset_id": "Vui lòng chọn tài sản cần báo hỏng."})

            if asset.status != Asset.Status.IN_USE:
                raise ValidationError(
                    {"asset_id": "Chỉ có thể báo hỏng tài sản đang sử dụng (IN_USE)."}
                )

            if asset.assigned_to_user_id != user.id:
                raise ValidationError(
                    {"asset_id": "Tài sản không thuộc quyền sở hữu/sử dụng của bạn."}
                )

            # Prevent duplicate pending DAMAGE_REPORT request
            if AssetRequest.objects.filter(
                requester=user,
                request_type=AssetRequest.RequestType.DAMAGE_REPORT,
                asset=asset,
                status=AssetRequest.Status.PENDING,
            ).exists():
                raise ConflictError("Bạn đã có một yêu cầu báo hỏng tài sản này đang chờ duyệt.")

        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        request_type = validated_data['request_type']
        requested_category = (
            validated_data.get('requested_category')
            if request_type == AssetRequest.RequestType.ALLOCATE
            else None
        )
        req = AssetRequest.objects.create(
            request_type=request_type,
            requester=user,
            department=user.department,
            requested_category=requested_category,
            asset=validated_data.get('asset'),
            reason=validated_data['reason'].strip(),
            status=AssetRequest.Status.PENDING,
        )
        return req


class RequestApproveSerializer(serializers.Serializer):
    """
    Serializer for approving a request.
    Validates input parameters depending on request_type.
    """
    selected_asset_id = serializers.IntegerField(required=False, allow_null=True)
    received_confirmed = serializers.BooleanField(required=False, default=False)
    condition = serializers.ChoiceField(
        choices=['GOOD', 'DAMAGED'],
        required=False,
        allow_null=True,
    )


class RequestRejectSerializer(serializers.Serializer):
    """
    Serializer for rejecting a request with mandatory reason.
    """
    rejection_reason = serializers.CharField(required=True, allow_blank=False)

    def validate_rejection_reason(self, value):
        val = value.strip()
        if not val:
            raise ValidationError("Lý do từ chối không được để trống.")
        return val
