from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from apps.authentication.models import User
from apps.common.permissions import DepartmentScopedPermission, IsAdminOrReadOnly
from apps.departments.models import Category, Department
from apps.departments.serializers import CategorySerializer, DepartmentSerializer


@extend_schema_view(
    list=extend_schema(summary="Danh sách phòng ban", tags=["departments"]),
    retrieve=extend_schema(summary="Chi tiết phòng ban", tags=["departments"]),
    create=extend_schema(summary="Tạo phòng ban mới", tags=["departments"]),
    partial_update=extend_schema(summary="Cập nhật phòng ban", tags=["departments"]),
)
class DepartmentViewSet(viewsets.ModelViewSet):
    """
    Department management API.
    - Admin: full CRUD (except DELETE/PUT).
    - Manager and User: read-only access to their assigned department.
    """
    serializer_class = DepartmentSerializer
    permission_classes = [DepartmentScopedPermission]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Department.objects.none()
        if user.role == User.Role.ADMIN:
            return Department.objects.all()
        if user.department_id:
            return Department.objects.filter(id=user.department_id)
        return Department.objects.none()


@extend_schema_view(
    list=extend_schema(summary="Danh sách danh mục tài sản", tags=["categories"]),
    retrieve=extend_schema(summary="Chi tiết danh mục tài sản", tags=["categories"]),
    create=extend_schema(summary="Tạo danh mục tài sản", tags=["categories"]),
    partial_update=extend_schema(summary="Cập nhật danh mục tài sản", tags=["categories"]),
)
class CategoryViewSet(viewsets.ModelViewSet):
    """
    Category management API.
    - Admin: create and patch categories.
    - Manager and User: read-only list and detail.
    - DELETE and PUT are strictly disabled.
    """
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
