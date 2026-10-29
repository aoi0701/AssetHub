from rest_framework_simplejwt.tokens import RefreshToken


class AssetHubRefreshToken(RefreshToken):
    """
    Custom RefreshToken that embeds auth_version and role into payload claims.
    These claims are automatically inherited by the generated AccessToken.
    """
    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        token['auth_version'] = user.auth_version
        token['role'] = user.role
        return token


def generate_tokens_for_user(user) -> dict:
    """Generate access and refresh tokens embedding current auth_version and role."""
    refresh = AssetHubRefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }


def revoke_all_user_sessions(user) -> int:
    """
    Increment user's auth_version to invalidate all existing tokens immediately.
    Returns the new auth_version.
    """
    user.auth_version += 1
    user.save(update_fields=['auth_version'])
    return user.auth_version


def check_can_modify_user_department_or_status(user) -> bool:
    """
    Guard service checking whether user's department or active status can be modified.
    Rejects modification (returns False) if the user currently holds active assets
    or has pending asset requests awaiting review.
    Ready for Task 3/5 integration.
    """
    if hasattr(user, 'assigned_assets') and user.assigned_assets.filter(status='IN_USE').exists():
        return False
    if hasattr(user, 'asset_requests') and user.asset_requests.filter(status='PENDING').exists():
        return False
    return True
