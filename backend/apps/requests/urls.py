from django.urls import path
from apps.requests.views import (
    RequestApproveView,
    RequestDetailView,
    RequestListCreateView,
    RequestRejectView,
)

app_name = 'requests'

urlpatterns = [
    path('requests/', RequestListCreateView.as_view(), name='request-list-create'),
    path('requests/<int:pk>/', RequestDetailView.as_view(), name='request-detail'),
    path('requests/<int:pk>/approve/', RequestApproveView.as_view(), name='request-approve'),
    path('requests/<int:pk>/reject/', RequestRejectView.as_view(), name='request-reject'),
]
