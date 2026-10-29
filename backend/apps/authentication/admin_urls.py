from rest_framework.routers import DefaultRouter
from apps.authentication.admin_api import UserAdminViewSet

router = DefaultRouter()
router.register(r'users', UserAdminViewSet, basename='user-admin')

urlpatterns = router.urls
