import tempfile
import pytest
from unittest.mock import MagicMock
from rest_framework.test import APIClient
from apps.departments.models import Department
from apps.authentication.models import User


@pytest.fixture
def api_client():
    """DRF test API client fixture."""
    return APIClient()


@pytest.fixture
def dept_it(db):
    """Primary IT department fixture."""
    return Department.objects.create(
        name="Information Technology",
        code="IT",
        description="IT and Systems Infrastructure",
    )


@pytest.fixture
def dept_hr(db):
    """Secondary HR department fixture."""
    return Department.objects.create(
        name="Human Resources",
        code="HR",
        description="Human Resources and Operations",
    )


@pytest.fixture
def admin_user(db):
    """Active Admin user fixture."""
    return User.objects.create_user(
        username="admin_user",
        email="admin@assethub.local",
        password="AdminPassword123!",
        role=User.Role.ADMIN,
        full_name="System Administrator",
        phone="0901234567",
        department=None,
        is_active=True,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def manager_user(db, dept_it):
    """Active Manager user fixture assigned to IT department."""
    return User.objects.create_user(
        username="manager_it",
        email="manager.it@assethub.local",
        password="ManagerPassword123!",
        role=User.Role.MANAGER,
        full_name="IT Department Manager",
        phone="0901234568",
        department=dept_it,
        is_active=True,
    )


@pytest.fixture
def regular_user(db, dept_it):
    """Active standard User fixture assigned to IT department."""
    return User.objects.create_user(
        username="user_it",
        email="user.it@assethub.local",
        password="UserPassword123!",
        role=User.Role.USER,
        full_name="Standard Employee",
        phone="0901234569",
        department=dept_it,
        is_active=True,
    )


@pytest.fixture
def temp_media_root(settings, tmp_path):
    """Temporary media root fixture to avoid polluting real media directory."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_dir)
    return media_dir


@pytest.fixture
def mock_external_services(monkeypatch):
    """Mock external third-party services (Cloudflare AI, Workers, R2)."""
    mock_r2 = MagicMock(name="mock_r2_storage")
    mock_ai = MagicMock(name="mock_workers_ai")
    monkeypatch.setattr(
        "django.core.files.storage.default_storage",
        mock_r2,
        raising=False,
    )
    return {
        "r2": mock_r2,
        "ai": mock_ai,
    }
