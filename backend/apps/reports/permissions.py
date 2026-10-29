from rest_framework import permissions
from apps.authentication.models import User


class ReportPermission(permissions.BasePermission):
    """
    Access permission for report views:
    - Admin & Manager: Allowed.
    - User: Forbidden (403).
    - Unauthenticated: Handled by IsAuthenticated (401).
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.role in [User.Role.ADMIN, User.Role.MANAGER]
