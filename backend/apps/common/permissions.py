from rest_framework import permissions
from apps.authentication.models import User


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Allows read-only access to any authenticated user,
    but write operations (POST, PATCH, PUT, DELETE) are restricted to ADMIN role.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.role == User.Role.ADMIN


class DepartmentScopedPermission(permissions.BasePermission):
    """
    Department endpoint permission:
    - Admin: full read and write access across all departments.
    - Manager and User: read-only access (GET/HEAD/OPTIONS) scoped to their assigned department.
    - Write operations by Manager or User are rejected with 403.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == User.Role.ADMIN:
            return True
        if request.method in permissions.SAFE_METHODS:
            return True
        return False

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == User.Role.ADMIN:
            return True
        if request.method in permissions.SAFE_METHODS:
            return bool(
                request.user.department_id
                and request.user.department_id == obj.id
            )
        return False


class IsAdminOrManagerReadOnly(permissions.BasePermission):
    """
    User administration permission:
    - Admin: full read and write access across all users.
    - Manager: read-only access to users within their assigned department.
    - Regular User: completely forbidden (403).
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == User.Role.ADMIN:
            return True
        if request.user.role == User.Role.MANAGER and request.method in permissions.SAFE_METHODS:
            return True
        return False

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == User.Role.ADMIN:
            return True
        if request.user.role == User.Role.MANAGER and request.method in permissions.SAFE_METHODS:
            return bool(
                request.user.department_id
                and request.user.department_id == obj.department_id
            )
        return False
