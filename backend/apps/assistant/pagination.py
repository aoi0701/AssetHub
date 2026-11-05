"""Pagination for Assistant endpoints."""
from rest_framework.exceptions import ValidationError

from apps.common.pagination import StandardPagination


class AssistantPagination(StandardPagination):
    """Pagination class for assistant endpoints.

    Strictly forbids 'all' and 'pagination' query parameters to prevent bypassing pagination.
    """
    page_size = 15
    max_page_size = 20

    def paginate_queryset(self, queryset, request, view=None):
        if 'all' in request.query_params or 'pagination' in request.query_params:
            raise ValidationError(
                {'detail': 'all and pagination parameters are not allowed in assistant endpoints.'}
            )
        return super().paginate_queryset(queryset, request, view)
