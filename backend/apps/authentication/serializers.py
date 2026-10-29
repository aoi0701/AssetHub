from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import exceptions, serializers
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    """
    Public registration serializer with strict field allowlist.
    Only allows: username, email, password, full_name, phone.
    Any attempt to set privileged fields (role, is_active, department) or unexpected fields is rejected with 400.
    """
    ALLOWED_FIELDS = {'username', 'email', 'password', 'full_name', 'phone'}

    username = serializers.CharField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message='Tên đăng nhập đã tồn tại.')],
    )
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text='User password, validated by Django password validators',
    )
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message='Email này đã được sử dụng.')],
        help_text='Required unique email address',
    )
    full_name = serializers.CharField(
        required=False,
        default='',
        allow_blank=True,
        max_length=150,
    )
    phone = serializers.CharField(
        required=False,
        default='',
        allow_blank=True,
        max_length=20,
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'full_name', 'phone')

    def to_internal_value(self, data):
        # Strict allowlist check: reject any unexpected or privileged fields
        extra_fields = set(data.keys()) - self.ALLOWED_FIELDS
        if extra_fields:
            errors = {field: ['Trường này không được phép trong biểu mẫu đăng ký.'] for field in extra_fields}
            raise serializers.ValidationError(errors)
        return super().to_internal_value(data)

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            full_name=validated_data.get('full_name', ''),
            phone=validated_data.get('phone', ''),
            role=User.Role.USER,
            is_active=False,
            department=None,
        )


class LoginSerializer(serializers.Serializer):
    """Serializer for user credentials on login."""
    username = serializers.CharField(required=True)
    password = serializers.CharField(
        required=True,
        write_only=True,
        style={'input_type': 'password'},
    )


class UserProfileSerializer(serializers.ModelSerializer):
    """Public read-only serializer for user profile information."""
    department_id = serializers.IntegerField(source='department.id', read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'full_name',
            'phone',
            'role',
            'department_id',
            'created_at',
        )
        read_only_fields = fields


class LoginResponseSerializer(serializers.Serializer):
    """Schema for successful login response."""
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserProfileSerializer()


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for user updating own profile.
    Only allows: full_name, email, phone.
    Rejects attempts to modify role, department, is_active, username, etc.
    """
    ALLOWED_FIELDS = {'full_name', 'email', 'phone'}

    class Meta:
        model = User
        fields = ('full_name', 'email', 'phone')

    def to_internal_value(self, data):
        extra_fields = set(data.keys()) - self.ALLOWED_FIELDS
        if extra_fields:
            errors = {field: ['Không được phép chỉnh sửa trường này trong hồ sơ cá nhân.'] for field in extra_fields}
            raise serializers.ValidationError(errors)
        return super().to_internal_value(data)

    def validate_email(self, value):
        if User.objects.exclude(pk=self.instance.pk).filter(email=value).exists():
            raise serializers.ValidationError('Email này đã được sử dụng bởi tài khoản khác.')
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for changing current user password."""
    old_password = serializers.CharField(
        required=True,
        write_only=True,
        style={'input_type': 'password'},
    )
    new_password = serializers.CharField(
        required=True,
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate(self, attrs):
        user = self.context['request'].user
        if not user.check_password(attrs['old_password']):
            raise serializers.ValidationError({'old_password': ['Mật khẩu cũ không chính xác.']})

        validate_password(attrs['new_password'], user=user)
        return attrs


class CustomTokenRefreshSerializer(serializers.Serializer):
    """
    Serializer to refresh JWT access token.
    Validates token signature and checks database user is_active and auth_version.
    """
    refresh = serializers.CharField(required=True)

    def validate(self, attrs):
        refresh_str = attrs['refresh']
        try:
            token = RefreshToken(refresh_str)
        except (TokenError, InvalidToken):
            raise exceptions.AuthenticationFailed(
                'Refresh token không hợp lệ hoặc đã hết hạn.',
                code='token_invalid',
            )

        user_id = token.payload.get('user_id')
        user = User.objects.filter(pk=user_id).first()

        if user is None or not user.is_active:
            raise exceptions.AuthenticationFailed(
                'Tài khoản không tồn tại hoặc đã bị khóa.',
                code='user_inactive',
            )

        token_version = token.payload.get('auth_version')
        if token_version is None or token_version != user.auth_version:
            raise exceptions.AuthenticationFailed(
                'Phiên làm việc đã bị thu hồi hoặc đã hết hạn.',
                code='token_revoked',
            )

        # Generate new access token
        access_token = token.access_token
        return {'access': str(access_token)}


class TokenRefreshResponseSerializer(serializers.Serializer):
    """Response serializer containing new access token."""
    access = serializers.CharField()


class MessageResponseSerializer(serializers.Serializer):
    """Standard message response."""
    message = serializers.CharField()
