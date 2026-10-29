import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework import status
from apps.departments.models import Department
from apps.authentication.models import User


@pytest.mark.django_db
def test_health_check_endpoint(api_client):
    """GET /api/health/ must be public, return 200, and show db ok without leaking config."""
    response = api_client.get('/api/health/')
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok", "db": "ok"}
    # Assert no sensitive config keys are leaked
    body_str = response.content.decode()
    assert "SECRET" not in body_str
    assert "PASSWORD" not in body_str
    assert "DEBUG" not in body_str


@pytest.mark.django_db
def test_health_check_database_failure(api_client, monkeypatch):
    """GET /api/health/ should return 503 if database connection fails."""
    from unittest.mock import MagicMock
    from django.db import connection

    def mock_cursor():
        raise Exception("Database connection failure")

    monkeypatch.setattr(connection, "cursor", mock_cursor)
    response = api_client.get('/api/health/')
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "error", "db": "unavailable"}


@pytest.mark.django_db
def test_custom_user_model_configured():
    """Verify AUTH_USER_MODEL resolves to apps.authentication.models.User."""
    user_model = get_user_model()
    assert user_model is User
    assert user_model._meta.label == 'authentication.User'


@pytest.mark.django_db
def test_user_model_defaults():
    """Verify custom User model default field values."""
    user = User.objects.create_user(
        username="default_user",
        email="default@assethub.local",
        password="ValidPassword123!",
    )
    assert user.role == User.Role.USER
    assert user.auth_version == 1
    assert user.department is None
    assert user.full_name == ""
    assert user.phone == ""
    assert str(user) == "default_user (USER)"


@pytest.mark.django_db
def test_user_roles_and_department_fixtures(admin_user, manager_user, regular_user, dept_it, dept_hr):
    """Verify fixtures: two departments, three role fixtures, FK relationship."""
    assert admin_user.role == User.Role.ADMIN
    assert admin_user.department is None

    assert manager_user.role == User.Role.MANAGER
    assert manager_user.department == dept_it

    assert regular_user.role == User.Role.USER
    assert regular_user.department == dept_it

    # Reverse relationship
    it_users = list(dept_it.users.all())
    assert manager_user in it_users
    assert regular_user in it_users
    assert admin_user not in it_users


@pytest.mark.django_db
def test_department_unique_code():
    """Verify department code uniqueness constraint."""
    Department.objects.create(name="Finance 1", code="FIN")
    with pytest.raises(IntegrityError):
        Department.objects.create(name="Finance 2", code="FIN")


@pytest.mark.django_db
def test_user_department_set_null_on_delete(dept_hr):
    """Deleting a department should set user's department to null (SET_NULL)."""
    user = User.objects.create_user(
        username="temp_hr_user",
        email="temp.hr@assethub.local",
        password="Password123!",
        department=dept_hr,
    )
    assert user.department == dept_hr

    dept_hr.delete()
    user.refresh_from_db()
    assert user.department is None
