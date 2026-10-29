import io
import os
import pytest
from django.core.management import call_command, CommandError

from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.authentication.models import User
from apps.departments.models import Category, Department


@pytest.mark.django_db
def test_seed_command_requires_demo_flag(monkeypatch):
    """Seed command without --demo flag must fail."""
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    monkeypatch.setenv("DEMO_SEED_PASSWORD", "DemoSecretPassword123!")
    out = io.StringIO()
    with pytest.raises(CommandError, match="--demo"):
        call_command("seed_data", stdout=out)


@pytest.mark.django_db
def test_seed_command_requires_allow_demo_seed_env(monkeypatch):
    """Seed command without ALLOW_DEMO_SEED=true must fail."""
    monkeypatch.delenv("ALLOW_DEMO_SEED", raising=False)
    monkeypatch.setenv("DEMO_SEED_PASSWORD", "DemoSecretPassword123!")
    out = io.StringIO()
    with pytest.raises(CommandError, match="ALLOW_DEMO_SEED"):
        call_command("seed_data", "--demo", stdout=out)


@pytest.mark.django_db
def test_seed_command_requires_demo_password(monkeypatch):
    """Seed command without DEMO_SEED_PASSWORD must fail."""
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    monkeypatch.delenv("DEMO_SEED_PASSWORD", raising=False)
    out = io.StringIO()
    with pytest.raises(CommandError, match="DEMO_SEED_PASSWORD"):
        call_command("seed_data", "--demo", stdout=out)


@pytest.mark.django_db
def test_seed_command_creates_data_idempotently_without_leaking_password(monkeypatch):
    """
    Seed command:
    - Creates 2 departments, 5 categories, 7 users (1 Admin, 2 Managers, 4 Users), 15 assets.
    - Consistent history logs for all assets.
    - Does NOT print the password to console.
    - Idempotent: running twice does not duplicate records or reset passwords.
    """
    demo_pass = "SuperSecretDemoPass456!"
    monkeypatch.setenv("ALLOW_DEMO_SEED", "true")
    monkeypatch.setenv("DEMO_SEED_PASSWORD", demo_pass)

    out = io.StringIO()
    call_command("seed_data", "--demo", stdout=out)
    output_str = out.getvalue()

    # Never print password in stdout
    assert demo_pass not in output_str

    # 1. Check departments count
    assert Department.objects.count() == 2
    assert set(Department.objects.values_list('code', flat=True)) == {'IT', 'HR'}

    # 2. Check categories count
    assert Category.objects.count() == 5
    assert set(Category.objects.values_list('name', flat=True)) == {
        'Laptop', 'Monitor', 'Phone', 'Office Furniture', 'Network Equipment'
    }

    # 3. Check users count and roles
    assert User.objects.count() == 7
    admin = User.objects.get(username='admin')
    assert admin.role == User.Role.ADMIN
    assert admin.check_password(demo_pass) is True

    managers = User.objects.filter(role=User.Role.MANAGER)
    assert managers.count() == 2

    users = User.objects.filter(role=User.Role.USER)
    assert users.count() == 4

    # 4. Check assets count and statuses
    assert Asset.objects.count() == 15
    statuses = set(Asset.objects.values_list('status', flat=True))
    assert statuses == {
        Asset.Status.AVAILABLE,
        Asset.Status.IN_USE,
        Asset.Status.DAMAGED_PENDING_REPAIR,
        Asset.Status.UNDER_MAINTENANCE,
        Asset.Status.PENDING_DISPOSAL,
        Asset.Status.DISPOSED,
    }

    # Verify history logs exist and are consistent
    initial_log_count = AssetHistoryLog.objects.count()
    assert initial_log_count >= 15
    for asset in Asset.objects.all():
        logs = list(asset.history_logs.all())
        assert len(logs) >= 1
        # First log is always CREATE
        create_log = logs[-1]  # logs are ordered '-created_at', so earliest is last
        assert create_log.action_type == AssetHistoryLog.ActionType.CREATE

    # Sequence counter advanced
    seq = IdentifierSequence.objects.get(kind=IdentifierSequence.Kind.AST)
    assert seq.value >= 15

    # 5. Test idempotency: change a user's password and run again
    admin.set_password("NewCustomPassword789!")
    admin.save()

    out2 = io.StringIO()
    call_command("seed_data", "--demo", stdout=out2)

    # Counts must NOT change
    assert Department.objects.count() == 2
    assert Category.objects.count() == 5
    assert User.objects.count() == 7
    assert Asset.objects.count() == 15
    assert AssetHistoryLog.objects.count() == initial_log_count

    # Custom password was NOT reset by the idempotent seed
    admin.refresh_from_db()
    assert admin.check_password("NewCustomPassword789!") is True
