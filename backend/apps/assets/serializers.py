from django.db import transaction
from rest_framework import serializers

from apps.assets.identifiers import generate_identifier
from apps.assets.models import Asset, AssetHistoryLog
from apps.departments.models import Category, Department


class AssetSerializer(serializers.ModelSerializer):
    """
    Representation serializer for Asset list, detail, and mutation responses.
    Exposes both foreign key IDs and descriptive snapshots/names.
    """
    category = serializers.PrimaryKeyRelatedField(read_only=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)

    department = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    department_id = serializers.IntegerField(source='department.id', read_only=True, allow_null=True)
    department_name = serializers.CharField(source='department.name', read_only=True, allow_null=True)
    department_code = serializers.CharField(source='department.code', read_only=True, allow_null=True)

    assigned_to_user = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    assigned_to_user_id = serializers.IntegerField(source='assigned_to_user.id', read_only=True, allow_null=True)
    assigned_to_user_name = serializers.CharField(source='assigned_to_user.full_name', read_only=True, allow_null=True)
    assigned_to_user_username = serializers.CharField(source='assigned_to_user.username', read_only=True, allow_null=True)

    class Meta:
        model = Asset
        fields = [
            'id',
            'asset_code',
            'name',
            'category',
            'category_id',
            'category_name',
            'serial_number',
            'specification',
            'status',
            'department',
            'department_id',
            'department_name',
            'department_code',
            'assigned_to_user',
            'assigned_to_user_id',
            'assigned_to_user_name',
            'assigned_to_user_username',
            'image',
            'document',
            'purchase_date',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class AssetCreateSerializer(serializers.Serializer):
    """
    Serializer for Admin creating a new asset.
    - Forces status=AVAILABLE and assigned_to_user=None.
    - Rejects client-supplied status, assigned_to_user, holder, or asset_code with 400.
    - Auto-generates asset_code via generate_identifier('AST').
    - Atomically creates exactly one CREATE AssetHistoryLog record with snapshots.
    """
    name = serializers.CharField(max_length=255, required=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source='category',
        required=True,
    )
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        required=False,
        allow_null=True,
        default=None,
    )
    serial_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    specification = serializers.JSONField(required=False, default=dict)
    purchase_date = serializers.DateField(required=False, allow_null=True, default=None)
    image = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    document = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        # Reject client attempts to dictate workflow fields or manual code
        forbidden_rules = {
            'status': "Trường 'status' không được thiết lập khi tạo mới tài sản. Tài sản mới luôn có trạng thái AVAILABLE.",
            'assigned_to_user': "Trường 'assigned_to_user' không được gán khi tạo mới tài sản. Tài sản mới chưa có người giữ.",
            'assigned_to_user_id': "Trường 'assigned_to_user_id' không được gán khi tạo mới tài sản.",
            'holder': "Trường 'holder' không được gán khi tạo mới tài sản.",
            'asset_code': "Mã tài sản 'asset_code' được hệ thống tự động sinh và không được chỉ định thủ công.",
            'code': "Mã tài sản 'code' không được chỉ định thủ công.",
            'history': "Không thể gửi dữ liệu lịch sử lồng nhau.",
            'history_logs': "Không thể gửi dữ liệu lịch sử lồng nhau.",
        }
        for field, err_msg in forbidden_rules.items():
            if field in self.initial_data:
                raise serializers.ValidationError({field: err_msg})

        return attrs

    def create(self, validated_data):
        reason = validated_data.pop('reason', '')
        user = self.context['request'].user

        with transaction.atomic():
            asset_code = generate_identifier('AST')
            asset = Asset(
                asset_code=asset_code,
                name=validated_data['name'].strip(),
                category=validated_data['category'],
                department=validated_data.get('department'),
                serial_number=validated_data.get('serial_number', '').strip(),
                specification=validated_data.get('specification', {}),
                purchase_date=validated_data.get('purchase_date'),
                image=validated_data.get('image', '').strip(),
                document=validated_data.get('document', '').strip(),
                status=Asset.Status.AVAILABLE,
                assigned_to_user=None,
            )
            asset.save()

            # Record single CREATE audit history log in same transaction
            AssetHistoryLog.objects.create(
                asset=asset,
                actor=user,
                action_type=AssetHistoryLog.ActionType.CREATE,
                from_status='',
                to_status=Asset.Status.AVAILABLE,
                from_user=None,
                to_user=None,
                from_department=None,
                to_department=asset.department,
                reason=reason.strip() if reason else 'Khởi tạo tài sản mới',
                asset_code_snapshot=asset.asset_code,
                asset_name_snapshot=asset.name,
            )

        return asset


class AssetUpdateSerializer(serializers.Serializer):
    """
    Serializer for Admin updating asset metadata (PATCH).
    Strict allowlist: name, category_id, serial_number, specification, purchase_date.
    Any forbidden workflow/protected fields (status, department, assigned_to_user, asset_code)
    immediately result in a 400 Bad Request error.
    """
    name = serializers.CharField(max_length=255, required=False)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source='category',
        required=False,
    )
    serial_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    specification = serializers.JSONField(required=False)
    purchase_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        allowed_fields = {
            'name',
            'category_id',
            'category',
            'serial_number',
            'specification',
            'purchase_date',
        }
        raw_keys = set(self.initial_data.keys())
        disallowed_keys = raw_keys - allowed_fields

        if disallowed_keys:
            errors = {}
            for key in disallowed_keys:
                if key in ('status', 'status_id'):
                    errors[key] = "Không thể cập nhật trạng thái qua PATCH. Vui lòng sử dụng các endpoint workflow chuyển đổi trạng thái."
                elif key in ('department', 'department_id'):
                    errors[key] = "Không thể cập nhật phòng ban qua PATCH. Vui lòng sử dụng endpoint điều chuyển (transfer)."
                elif key in ('assigned_to_user', 'assigned_to_user_id', 'holder'):
                    errors[key] = "Không thể gán/thu hồi người giữ qua PATCH. Vui lòng sử dụng endpoint allocate hoặc revoke."
                elif key in ('asset_code', 'code'):
                    errors[key] = "Mã tài sản là bất biến và không thể cập nhật."
                else:
                    errors[key] = f"Trường '{key}' không được phép cập nhật qua PATCH. Chỉ cho phép: name, category_id, serial_number, specification, purchase_date."
            raise serializers.ValidationError(errors)

        return attrs

    def update(self, instance, validated_data):
        if 'name' in validated_data:
            instance.name = validated_data['name'].strip()
        if 'category' in validated_data:
            instance.category = validated_data['category']
        if 'serial_number' in validated_data:
            instance.serial_number = validated_data['serial_number'].strip()
        if 'specification' in validated_data:
            instance.specification = validated_data['specification']
        if 'purchase_date' in validated_data:
            instance.purchase_date = validated_data['purchase_date']

        instance.save()
        return instance


class AssetHistoryLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for append-only asset history audit trail."""
    actor_name = serializers.CharField(source='actor.full_name', read_only=True)
    actor_username = serializers.CharField(source='actor.username', read_only=True)
    from_user_name = serializers.CharField(source='from_user.full_name', read_only=True, allow_null=True)
    to_user_name = serializers.CharField(source='to_user.full_name', read_only=True, allow_null=True)
    from_department_name = serializers.CharField(source='from_department.name', read_only=True, allow_null=True)
    to_department_name = serializers.CharField(source='to_department.name', read_only=True, allow_null=True)

    class Meta:
        model = AssetHistoryLog
        fields = [
            'id',
            'asset_id',
            'actor_id',
            'actor_name',
            'actor_username',
            'action_type',
            'from_status',
            'to_status',
            'from_user_id',
            'from_user_name',
            'to_user_id',
            'to_user_name',
            'from_department_id',
            'from_department_name',
            'to_department_id',
            'to_department_name',
            'reason',
            'asset_code_snapshot',
            'asset_name_snapshot',
            'created_at',
        ]
        read_only_fields = fields


class AssetAllocateActionSerializer(serializers.Serializer):
    assigned_to_user_id = serializers.IntegerField(required=True)
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val


class AssetRevokeActionSerializer(serializers.Serializer):
    received_confirmed = serializers.BooleanField(required=False, default=None)
    condition = serializers.ChoiceField(choices=['GOOD', 'DAMAGED'], required=False, default=None)
    to_status = serializers.ChoiceField(choices=['AVAILABLE', 'DAMAGED_PENDING_REPAIR'], required=False, default=None)
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val

    def validate(self, attrs):
        to_status = attrs.get('to_status')
        condition = attrs.get('condition')
        received_confirmed = attrs.get('received_confirmed')

        if to_status is not None and condition is None:
            if to_status == 'AVAILABLE':
                condition = 'GOOD'
            else:
                condition = 'DAMAGED'
            attrs['condition'] = condition
            if received_confirmed is None:
                attrs['received_confirmed'] = True

        if attrs.get('received_confirmed') is None:
            raise serializers.ValidationError({"received_confirmed": "This field is required."})
        if not attrs.get('received_confirmed'):
            raise serializers.ValidationError(
                {"received_confirmed": "Bắt buộc phải xác nhận đã nhận lại tài sản trước khi thu hồi."}
            )

        if not attrs.get('condition'):
            raise serializers.ValidationError({"condition": "This field is required."})

        return attrs


class AssetMarkDamagedActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val


class AssetSendMaintenanceActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val


class AssetCompleteMaintenanceActionSerializer(serializers.Serializer):
    result = serializers.ChoiceField(choices=['RETURN_TO_USER', 'RETURN_TO_STOCK'], required=True)
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val


class AssetTransferActionSerializer(serializers.Serializer):
    to_department_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    target_department_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val

    def validate(self, attrs):
        if 'target_department_id' in self.initial_data and 'to_department_id' not in self.initial_data:
            attrs['to_department_id'] = attrs.get('target_department_id')
        if 'to_department_id' not in attrs and 'target_department_id' not in attrs:
            raise serializers.ValidationError({"to_department_id": "This field is required."})
        return attrs


class AssetProposeDisposalActionSerializer(serializers.Serializer):
    received_confirmed = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val


class AssetConfirmDisposalActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)

    def validate_reason(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Lý do không được để trống.")
        return val

