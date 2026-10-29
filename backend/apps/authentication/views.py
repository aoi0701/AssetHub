from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.authentication.serializers import (
    ChangePasswordSerializer,
    CustomTokenRefreshSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    MessageResponseSerializer,
    ProfileUpdateSerializer,
    RegisterSerializer,
    TokenRefreshResponseSerializer,
    UserProfileSerializer,
)
from apps.authentication.services import (
    generate_tokens_for_user,
    revoke_all_user_sessions,
)

User = get_user_model()


class RegisterView(APIView):
    """
    Public user registration endpoint.
    New accounts are assigned role=USER, is_active=False, department=None,
    and must be activated by an Administrator before login is permitted.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth_register'

    @extend_schema(
        summary='Đăng ký tài khoản người dùng',
        tags=['auth'],
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(
                response=MessageResponseSerializer,
                description='Tài khoản được tạo thành công, ở trạng thái chờ kích hoạt.',
            ),
            400: OpenApiResponse(description='Dữ liệu không hợp lệ hoặc chứa trường bị cấm.'),
            429: OpenApiResponse(description='Vượt quá giới hạn số lượt gửi yêu cầu.'),
        },
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'Đăng ký thành công. Tài khoản đang chờ Admin phê duyệt.'},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    Authentication endpoint returning access and refresh JWT tokens and user profile.
    Inactive users or invalid credentials receive 401 Unauthorized.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth_login'

    @extend_schema(
        summary='Đăng nhập tài khoản',
        tags=['auth'],
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(
                response=LoginResponseSerializer,
                description='Đăng nhập thành công, trả về cặp token JWT và thông tin tài khoản.',
            ),
            400: OpenApiResponse(description='Thiếu tên đăng nhập hoặc mật khẩu.'),
            401: OpenApiResponse(description='Sai thông tin đăng nhập hoặc tài khoản chưa được kích hoạt.'),
            429: OpenApiResponse(description='Vượt quá giới hạn số lượt gửi yêu cầu.'),
        },
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data['username']
        password = serializer.validated_data['password']

        user = User.objects.filter(username=username).first()
        if user is None or not user.check_password(password) or not user.is_active:
            return Response(
                {'detail': 'Tên đăng nhập hoặc mật khẩu không chính xác hoặc tài khoản chưa kích hoạt.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        tokens = generate_tokens_for_user(user)
        user_data = UserProfileSerializer(user).data

        return Response(
            {
                'access': tokens['access'],
                'refresh': tokens['refresh'],
                'user': user_data,
            },
            status=status.HTTP_200_OK,
        )


class CustomTokenRefreshView(APIView):
    """
    Endpoint to obtain a new access token using a valid refresh token.
    Verifies token claims and DB state (user is_active and auth_version).
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary='Làm mới access token',
        tags=['auth'],
        request=CustomTokenRefreshSerializer,
        responses={
            200: OpenApiResponse(
                response=TokenRefreshResponseSerializer,
                description='Access token mới.',
            ),
            400: OpenApiResponse(description='Thiếu refresh token.'),
            401: OpenApiResponse(description='Refresh token không hợp lệ, hết hạn hoặc phiên đã bị thu hồi.'),
        },
    )
    def post(self, request):
        serializer = CustomTokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    Logout endpoint that increments user's auth_version in database,
    revoking all existing access and refresh tokens across all sessions.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Đăng xuất khỏi tất cả phiên làm việc',
        tags=['auth'],
        request=None,
        responses={
            204: OpenApiResponse(description='Đăng xuất thành công, tất cả phiên bị thu hồi.'),
            401: OpenApiResponse(description='Chưa xác thực hoặc token không hợp lệ.'),
        },
    )
    def post(self, request):
        revoke_all_user_sessions(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(APIView):
    """
    Current user profile endpoint.
    GET: retrieve current profile.
    PATCH: update full_name, email, phone only.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Xem thông tin cá nhân',
        tags=['auth'],
        responses={
            200: OpenApiResponse(
                response=UserProfileSerializer,
                description='Thông tin cá nhân người dùng hiện tại.',
            ),
            401: OpenApiResponse(description='Chưa xác thực.'),
        },
    )
    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Cập nhật thông tin cá nhân',
        tags=['auth'],
        request=ProfileUpdateSerializer,
        responses={
            200: OpenApiResponse(
                response=UserProfileSerializer,
                description='Hồ sơ cập nhật thành công.',
            ),
            400: OpenApiResponse(description='Dữ liệu không hợp lệ hoặc cố gắng thay đổi trường bị cấm.'),
            401: OpenApiResponse(description='Chưa xác thực.'),
        },
    )
    def patch(self, request):
        serializer = ProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    Change password endpoint.
    Validates old_password and applies Django password validators to new_password.
    Increments auth_version to invalidate all existing tokens upon success.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Đổi mật khẩu',
        tags=['auth'],
        request=ChangePasswordSerializer,
        responses={
            204: OpenApiResponse(description='Đổi mật khẩu thành công, cần đăng nhập lại.'),
            400: OpenApiResponse(description='Mật khẩu cũ sai hoặc mật khẩu mới không thỏa mãn chính sách.'),
            401: OpenApiResponse(description='Chưa xác thực.'),
        },
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)

        new_password = serializer.validated_data['new_password']
        request.user.set_password(new_password)
        request.user.auth_version += 1
        request.user.save()

        return Response(status=status.HTTP_204_NO_CONTENT)
