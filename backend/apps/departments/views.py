from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from apps.authentication.models import User
from apps.common.pagination import StandardPagination
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
    pagination_class = StandardPagination
    optional_pagination = True
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Department.objects.none()
        if user.role == User.Role.ADMIN:
            qs = Department.objects.all()
        elif user.department_id:
            qs = Department.objects.filter(id=user.department_id)
        else:
            return Department.objects.none()

        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            search_str = search.strip()
            qs = qs.filter(
                Q(name__icontains=search_str)
                | Q(code__icontains=search_str)
                | Q(description__icontains=search_str)
            )
        return qs.order_by('id')


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
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = StandardPagination
    optional_pagination = True
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = Category.objects.all()
        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            search_str = search.strip()
            qs = qs.filter(
                Q(name__icontains=search_str)
                | Q(description__icontains=search_str)
            )
        return qs.order_by('id')

