from rest_framework import status
from rest_framework.exceptions import APIException


class ConflictError(APIException):
    """HTTP 409 Conflict exception when operation violates state machine or domain state."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Trạng thái hiện tại không hợp lệ hoặc có xung đột thao tác."
    default_code = "conflict"
