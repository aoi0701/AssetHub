from rest_framework.routers import DefaultRouter
from apps.departments.views import CategoryViewSet, DepartmentViewSet

router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='department')
router.register(r'categories', CategoryViewSet, basename='category')

urlpatterns = router.urls
