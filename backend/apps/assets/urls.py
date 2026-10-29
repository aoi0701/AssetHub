from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.assets.views import AssetViewSet

router = DefaultRouter()
router.register(r'assets', AssetViewSet, basename='asset')

urlpatterns = [
    path('', include(router.urls)),
]
