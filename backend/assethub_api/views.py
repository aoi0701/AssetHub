from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse


class HealthCheckView(APIView):
    """
    Public health check endpoint reporting app and database status.
    Does not leak secrets or internal configuration.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Service health check",
        description="Public health endpoint checking application and database status without exposing internal config.",
        responses={
            200: OpenApiResponse(description="Service and database operational"),
            503: OpenApiResponse(description="Database service unavailable"),
        },
        auth=[],
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
        except Exception:
            return Response(
                {"status": "error", "db": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"status": "ok", "db": "ok"}, status=status.HTTP_200_OK)
