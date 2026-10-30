from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from apps.authentication.models import User
from apps.authentication import services
from apps.common.pagination import StandardPagination
from apps.common.permissions import IsAdminOrManagerReadOnly
from apps.departments.models import Department


class ConflictError(APIException):
    """HTTP 409 Conflict exception when operation violates asset/request state."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý, không thể đổi phòng ban hoặc vô hiệu hóa."
    default_code = "conflict"


class UserAdminSerializer(serializers.ModelSerializer):
    """
    Representation serializer for user administration list/detail and responses.
    Strictly excludes password, password_hash, and auth_version.
    """
    department_id = serializers.IntegerField(source='department.id', read_only=True, allow_null=True)
    department_name = serializers.CharField(source='department.name', read_only=True, allow_null=True)
    department_code = serializers.CharField(source='department.code', read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'full_name',
            'phone',
            'role',
            'department_id',
            'department_name',
            'department_code',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields


class UserAdminCreateSerializer(serializers.Serializer):
    """
    Serializer for Admin creating a new user.
    Hashed password properly; enforces department assignment for active USER/MANAGER.
    """
    username = serializers.CharField(max_length=150, required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    role = serializers.ChoiceField(choices=User.Role.choices, default=User.Role.USER)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        required=False,
        allow_null=True,
        default=None,
    )
    is_active = serializers.BooleanField(default=True, required=False)

    def validate_username(self, value):
        val = value.strip()
        if User.objects.filter(username__iexact=val).exists():
            raise serializers.ValidationError("Tên đăng nhập đã tồn tại.")
        return val

    def validate_email(self, value):
        val = value.strip().lower()
        if User.objects.filter(email__iexact=val).exists():
            raise serializers.ValidationError("Email đã được sử dụng.")
        return val

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate(self, attrs):
        role = attrs.get('role', User.Role.USER)
        is_active = attrs.get('is_active', True)
        department = attrs.get('department')

        if is_active and role in [User.Role.USER, User.Role.MANAGER] and not department:
            raise serializers.ValidationError(
                {"department_id": "Nhân viên và Quản lý đang hoạt động bắt buộc phải thuộc một phòng ban."}
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        department = validated_data.pop('department', None)

        user = User(
            username=validated_data['username'],
            email=validated_data['email'],
            full_name=validated_data.get('full_name', ''),
            phone=validated_data.get('phone', ''),
            role=validated_data.get('role', User.Role.USER),
            department=department,
            is_active=validated_data.get('is_active', True),
        )
        user.set_password(password)
        user.save()
        return user


class UserAdminUpdateSerializer(serializers.Serializer):
    """
    Serializer for Admin updating an existing user (PATCH).
    Enforces active department rule, last active Admin protection,
    service guard for assets/requests, and immediate session revocation.
    """
    email = serializers.EmailField(required=False)
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        required=False,
        allow_null=True,
    )
    is_active = serializers.BooleanField(required=False)

    def validate_email(self, value):
        val = value.strip().lower()
        if User.objects.filter(email__iexact=val).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("Email đã được sử dụng.")
        return val

    def validate(self, attrs):
        user = self.instance
        target_role = attrs.get('role', user.role)
        target_active = attrs.get('is_active', user.is_active)
        target_department = attrs.get('department', user.department) if 'department' in attrs else user.department

        # 1. Protection for last active Admin
        if user.role == User.Role.ADMIN and user.is_active is True:
            is_demoting = 'role' in attrs and attrs['role'] != User.Role.ADMIN
            is_deactivating = 'is_active' in attrs and attrs['is_active'] is False
            if is_demoting or is_deactivating:
                has_other_admin = User.objects.filter(
                    role=User.Role.ADMIN,
                    is_active=True,
                ).exclude(pk=user.pk).exists()
                if not has_other_admin:
                    raise serializers.ValidationError(
                        "Không thể vô hiệu hóa hoặc hạ quyền quản trị viên duy nhất đang hoạt động."
                    )

        # 2. Active USER and MANAGER must have a department assigned
        if target_active and target_role in [User.Role.USER, User.Role.MANAGER] and target_department is None:
            raise serializers.ValidationError(
                {"department_id": "Nhân viên và Quản lý đang hoạt động bắt buộc phải thuộc một phòng ban."}
            )

        # 3. Service guard: prevent department change or deactivation if user holds assets or has pending requests
        department_changed = 'department' in attrs and attrs['department'] != user.department
        deactivated = 'is_active' in attrs and attrs['is_active'] is False and user.is_active is True

        if department_changed or deactivated:
            if not services.check_can_modify_user_department_or_status(user):
                raise ConflictError()

        return attrs

    def update(self, instance, validated_data):
        department_changed = 'department' in validated_data and validated_data['department'] != instance.department
        role_changed = 'role' in validated_data and validated_data['role'] != instance.role
        active_changed = 'is_active' in validated_data and validated_data['is_active'] != instance.is_active

        security_changed = department_changed or role_changed or active_changed

        with transaction.atomic():
            if 'department' in validated_data:
                instance.department = validated_data['department']
            if 'role' in validated_data:
                instance.role = validated_data['role']
            if 'is_active' in validated_data:
                instance.is_active = validated_data['is_active']
            if 'email' in validated_data:
                instance.email = validated_data['email']
            if 'full_name' in validated_data:
                instance.full_name = validated_data['full_name'].strip()
            if 'phone' in validated_data:
                instance.phone = validated_data['phone'].strip()

            if security_changed:
                instance.auth_version += 1

            instance.save()

        return instance


@extend_schema_view(
    list=extend_schema(
        summary="Danh sách người dùng (Quản trị)",
        tags=["users"],
        parameters=[
            OpenApiParameter(name="role", type=str, description="Lọc theo vai trò: ADMIN, MANAGER, USER"),
            OpenApiParameter(name="is_active", type=bool, description="Lọc theo trạng thái hoạt động"),
            OpenApiParameter(name="department_id", type=int, description="Lọc theo phòng ban"),
            OpenApiParameter(name="search", type=str, description="Tìm kiếm theo username, email, full_name"),
        ],
        responses={200: UserAdminSerializer(many=True)},
    ),
    retrieve=extend_schema(
        summary="Chi tiết người dùng (Quản trị)",
        tags=["users"],
        responses={200: UserAdminSerializer},
    ),
    create=extend_schema(
        summary="Tạo tài khoản người dùng mới (Chỉ Admin)",
        tags=["users"],
        request=UserAdminCreateSerializer,
        responses={201: UserAdminSerializer},
    ),
    partial_update=extend_schema(
        summary="Cập nhật tài khoản người dùng (Chỉ Admin)",
        tags=["users"],
        request=UserAdminUpdateSerializer,
        responses={200: UserAdminSerializer},
    ),
)
class UserAdminViewSet(viewsets.ModelViewSet):
    """
    User Administration API:
    - Admin: Full CRUD (GET list, POST create, GET detail, PATCH update). Strictly no DELETE or PUT.
    - Manager: Read-only access (GET list, GET detail) scoped to their assigned department.
    - User: Completely blocked (403 Forbidden).
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManagerReadOnly]
    pagination_class = StandardPagination
    optional_pagination = True
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return User.objects.none()

        if user.role == User.Role.ADMIN:
            queryset = User.objects.all().select_related('department')
        elif user.role == User.Role.MANAGER:
            if user.department_id:
                queryset = User.objects.filter(department_id=user.department_id).select_related('department')
            else:
                queryset = User.objects.none()
        else:
            return User.objects.none()

        # Filtering (Admin-focused, but applies cleanly if manager filters within own department)
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role.upper())

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            if is_active.lower() in ('true', '1'):
                queryset = queryset.filter(is_active=True)
            elif is_active.lower() in ('false', '0'):
                queryset = queryset.filter(is_active=False)

        dept_id = self.request.query_params.get('department_id')
        if dept_id:
            queryset = queryset.filter(department_id=dept_id)

        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            search = search.strip()
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(full_name__icontains=search)
            )

        return queryset.order_by('-date_joined', '-id')

    def get_serializer_class(self):
        if self.action == 'create':
            return UserAdminCreateSerializer
        if self.action in ['update', 'partial_update']:
            return UserAdminUpdateSerializer
        return UserAdminSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        response_serializer = UserAdminSerializer(user)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        response_serializer = UserAdminSerializer(user)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
