from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.permissions import ReportPermission
from apps.reports.serializers import ReportChartsSerializer, ReportKPISerializer
from apps.reports.services import ReportService


class ReportKPIView(APIView):
    """
    KPI metrics view for Assets and pending requests.
    - User: 403 Forbidden.
    - Manager: Locked to user's assigned department.
    - Admin: Full company scope by default, with optional department/unallocated filters.
    """
    permission_classes = [IsAuthenticated, ReportPermission]

    @extend_schema(
        summary="Thống kê KPIs tổng quan tài sản",
        tags=["reports"],
        parameters=[
            OpenApiParameter(
                name="department_id",
                type=str,
                description="Lọc theo ID phòng ban hoặc 'unallocated' (Chỉ Admin)",
            ),
            OpenApiParameter(
                name="category_id",
                type=int,
                description="Lọc theo ID danh mục tài sản",
            ),
        ],
        responses={200: ReportKPISerializer},
    )
    def get(self, request):
        department_param = request.query_params.get('department_id')
        category_param = request.query_params.get('category_id')
        data = ReportService.get_kpis(
            user=request.user,
            department_param=department_param,
            category_param=category_param,
        )
        return Response(data)


class ReportChartsView(APIView):
    """
    Charts distribution and monthly trend view.
    - status_distribution: Active asset count per status.
    - category_distribution: Active asset count per category.
    - department_distribution: Company-wide department asset count (Admin only; Manager receives empty list).
    - monthly_trend: New vs Disposed assets per month in Asia/Bangkok time.
    """
    permission_classes = [IsAuthenticated, ReportPermission]

    @extend_schema(
        summary="Dữ liệu biểu đồ phân bố và xu hướng tháng",
        tags=["reports"],
        parameters=[
            OpenApiParameter(
                name="department_id",
                type=str,
                description="Lọc theo ID phòng ban hoặc 'unallocated' (Chỉ Admin)",
            ),
            OpenApiParameter(
                name="category_id",
                type=int,
                description="Lọc theo ID danh mục tài sản",
            ),
            OpenApiParameter(
                name="start_month",
                type=str,
                description="Tháng bắt đầu (YYYY-MM)",
            ),
            OpenApiParameter(
                name="end_month",
                type=str,
                description="Tháng kết thúc (YYYY-MM)",
            ),
        ],
        responses={200: ReportChartsSerializer},
    )
    def get(self, request):
        department_param = request.query_params.get('department_id')
        category_param = request.query_params.get('category_id')
        start_month = request.query_params.get('start_month')
        end_month = request.query_params.get('end_month')
        data = ReportService.get_charts(
            user=request.user,
            department_param=department_param,
            category_param=category_param,
            start_month=start_month,
            end_month=end_month,
        )
        return Response(data)
