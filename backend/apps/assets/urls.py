from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.assets.history_api import PersonalAssetHistoryView
from apps.assets.views import AssetViewSet

router = DefaultRouter()
router.register(r'assets', AssetViewSet, basename='asset')

urlpatterns = [
    path('me/asset-history/', PersonalAssetHistoryView.as_view(), name='personal-asset-history'),
    path('', include(router.urls)),
]
