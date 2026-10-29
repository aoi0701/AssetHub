from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai.serializers import (
    AiCategorySuggestRequestSerializer,
    AiCategorySuggestResponseSerializer,
)
from apps.ai.services import AiProxyService


class AiCategorySuggestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="AI gợi ý danh mục tài sản",
        description="Gợi ý danh mục phù hợp dựa trên tên và mô tả tài sản thông qua Cloudflare AI Worker. Chỉ Quản trị viên (ADMIN) mới có quyền truy cập.",
        request=AiCategorySuggestRequestSerializer,
        responses={
            200: AiCategorySuggestResponseSerializer,
            400: dict,
            401: dict,
            403: dict,
            503: dict,
            504: dict,
        },
        tags=["ai"],
    )
    def post(self, request):
        serializer = AiCategorySuggestRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = AiProxyService.suggest_category(
            actor=request.user,
            name=serializer.validated_data.get('name'),
            description=serializer.validated_data.get('description', ''),
        )
        return Response(result, status=status.HTTP_200_OK)
