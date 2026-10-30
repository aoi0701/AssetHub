from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import User
from apps.common.pagination import StandardPagination
from apps.requests.models import AssetRequest
from apps.requests.serializers import (
    AssetRequestCreateSerializer,
    AssetRequestSerializer,
    RequestApproveSerializer,
    RequestRejectSerializer,
)
from apps.requests.services import RequestService


class RequestListCreateView(generics.ListCreateAPIView):
    """
    List and create asset requests.
    - User only views own requests.
    - Manager views department requests.
    - Admin views all requests across the company.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AssetRequestCreateSerializer
        return AssetRequestSerializer

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.ADMIN:
            qs = AssetRequest.objects.all()
        elif user.role == User.Role.MANAGER:
            qs = AssetRequest.objects.filter(department=user.department)
        else:
            qs = AssetRequest.objects.filter(requester=user)

        # Filters
        req_status = self.request.query_params.get('status')
        if req_status:
            qs = qs.filter(status=req_status)

        req_type = self.request.query_params.get('request_type')
        if req_type:
            qs = qs.filter(request_type=req_type)

        category_id = self.request.query_params.get('category_id')
        if category_id:
            qs = qs.filter(requested_category_id=category_id)

        department_id = self.request.query_params.get('department_id')
        if department_id and user.role == User.Role.ADMIN:
            qs = qs.filter(department_id=department_id)

        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            search_str = search.strip()
            qs = qs.filter(
                Q(request_code__icontains=search_str)
                | Q(reason__icontains=search_str)
                | Q(requester__full_name__icontains=search_str)
                | Q(requester__username__icontains=search_str)
                | Q(asset__name__icontains=search_str)
                | Q(asset__asset_code__icontains=search_str)
            )

        return qs.select_related(
            'requester', 'department', 'requested_category', 'asset', 'reviewed_by'
        ).order_by('-created_at')


    @extend_schema(
        request=AssetRequestCreateSerializer,
        responses={201: AssetRequestSerializer},
        summary="Tạo phiếu yêu cầu tài sản mới",
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(AssetRequestSerializer(instance).data, status=status.HTTP_201_CREATED)


class RequestDetailView(generics.RetrieveAPIView):
    """
    Retrieve single request details within actor's scope.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = AssetRequestSerializer

    def get_object(self):
        user = self.request.user
        pk = self.kwargs.get('pk')
        try:
            req = AssetRequest.objects.select_related(
                'requester', 'department', 'requested_category', 'asset', 'reviewed_by'
            ).get(pk=pk)
        except AssetRequest.DoesNotExist:
            raise NotFound("Không tìm thấy phiếu yêu cầu.")

        if user.role == User.Role.USER:
            if req.requester_id != user.id:
                raise NotFound("Không tìm thấy phiếu yêu cầu.")
        elif user.role == User.Role.MANAGER:
            if req.department_id != user.department_id:
                raise NotFound("Không tìm thấy phiếu yêu cầu trong phạm vi phòng ban được phân công.")

        return req


class RequestApproveView(APIView):
    """
    Approve an AssetRequest (Manager of matching department or Admin only).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=RequestApproveSerializer,
        responses={200: AssetRequestSerializer},
        summary="Duyệt phiếu yêu cầu tài sản",
    )
    def post(self, request, pk):
        if request.user.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền phê duyệt phiếu yêu cầu.")

        serializer = RequestApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        req = RequestService.approve_request(
            request_id=pk,
            actor=request.user,
            data=serializer.validated_data,
        )
        return Response(AssetRequestSerializer(req).data, status=status.HTTP_200_OK)


class RequestRejectView(APIView):
    """
    Reject an AssetRequest (Manager of matching department or Admin only).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=RequestRejectSerializer,
        responses={200: AssetRequestSerializer},
        summary="Từ chối phiếu yêu cầu tài sản",
    )
    def post(self, request, pk):
        if request.user.role == User.Role.USER:
            raise PermissionDenied("Nhân viên không có quyền từ chối phiếu yêu cầu.")

        serializer = RequestRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        req = RequestService.reject_request(
            request_id=pk,
            actor=request.user,
            rejection_reason=serializer.validated_data['rejection_reason'],
        )
        return Response(AssetRequestSerializer(req).data, status=status.HTTP_200_OK)
