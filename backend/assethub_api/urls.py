from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common.storage_backends import MediaDownloadView
from assethub_api.views import HealthCheckView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/media/download/', MediaDownloadView.as_view(), name='media-download'),
    path('api/auth/', include('apps.authentication.urls', namespace='auth')),
    path('api/', include('apps.authentication.admin_urls')),
    path('api/', include('apps.departments.urls')),
    path('api/', include('apps.assets.urls')),
    path('api/', include('apps.requests.urls')),
    path('api/reports/', include('apps.reports.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
