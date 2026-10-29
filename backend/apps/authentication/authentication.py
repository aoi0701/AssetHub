from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class CustomJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication class verifying:
    1. Standard JWT signature and expiration.
    2. User exists in DB and is_active is True.
    3. Token claim auth_version matches user's current DB auth_version.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        if not user.is_active:
            raise AuthenticationFailed(
                'Tài khoản chưa được kích hoạt hoặc đã bị khóa.',
                code='user_inactive',
            )

        token_version = validated_token.get('auth_version')
        if token_version is None or token_version != user.auth_version:
            raise AuthenticationFailed(
                'Phiên đăng nhập đã bị thu hồi hoặc đã hết hạn.',
                code='token_revoked',
            )

        return user


class CustomJWTScheme(OpenApiAuthenticationExtension):
    """OpenAPI authentication extension for CustomJWTAuthentication."""
    target_class = 'apps.authentication.authentication.CustomJWTAuthentication'
    name = 'jwtAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
        }
