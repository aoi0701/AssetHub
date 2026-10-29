import threading
import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.assets.identifiers import generate_identifier
from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.mark.django_db(transaction=True)
def test_concurrent_allocate_one_winner():
    """
    Two threads concurrently attempt to allocate the same AVAILABLE asset
    to two different pending ALLOCATE requests.
    Exactly one thread must succeed (200), and the second must receive Conflict (409).
    Asset becomes IN_USE with exactly one ALLOCATE history log created.
    """
    connections.close_all()

    dept = Department.objects.create(name="IT Concurrency", code="ITC")
    cat = Category.objects.create(name="Laptop Concurrency")

    manager = User.objects.create_user(
        username="mgr_conc",
        email="mgr_conc@assethub.local",
        password="Password123!",
        role=User.Role.MANAGER,
        department=dept,
        is_active=True,
    )
    user1 = User.objects.create_user(
        username="user_conc1",
        email="u1@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept,
        is_active=True,
    )
    user2 = User.objects.create_user(
        username="user_conc2",
        email="u2@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept,
        is_active=True,
    )

    asset = Asset.objects.create(
        asset_code="AST-CONC-001",
        name="Shared Resource Laptop",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
    )

    req1 = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user1,
        department=dept,
        requested_category=cat,
        reason="User 1 wants the laptop",
    )
    req2 = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user2,
        department=dept,
        requested_category=cat,
        reason="User 2 wants the laptop",
    )

    barrier = threading.Barrier(2)
    results = []

    def worker(req_id):
        try:
            client = auth_client_for(manager)
            barrier.wait(timeout=5)
            response = client.post(
                f"/api/requests/{req_id}/approve/",
                data={"selected_asset_id": asset.id},
                format="json",
            )
            results.append((req_id, response.status_code))
        except Exception as exc:
            results.append((req_id, exc))
        finally:
            connections.close_all()

    t1 = threading.Thread(target=worker, args=(req1.id,))
    t2 = threading.Thread(target=worker, args=(req2.id,))

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    connections.close_all()

    status_codes = sorted([r[1] for r in results if isinstance(r[1], int)])
    assert status_codes == [200, 409], f"Unexpected results: {results}"

    asset.refresh_from_db()
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user in (user1, user2)

    # Exactly one ALLOCATE log was recorded
    logs = AssetHistoryLog.objects.filter(asset=asset, action_type=AssetHistoryLog.ActionType.ALLOCATE)
    assert logs.count() == 1

    # Exactly one request is APPROVED, the other remains PENDING
    req1.refresh_from_db()
    req2.refresh_from_db()
    statuses = {req1.status, req2.status}
    assert statuses == {AssetRequest.Status.APPROVED, AssetRequest.Status.PENDING}


@pytest.mark.django_db(transaction=True)
def test_concurrent_approve_same_request_once():
    """
    Two threads concurrently attempt to approve the SAME pending request.
    Exactly one thread succeeds (200), and the second receives Conflict (409).
    Request is processed only once.
    """
    connections.close_all()

    dept = Department.objects.create(name="IT Concurrency 2", code="ITC2")
    cat = Category.objects.create(name="Monitor Concurrency")

    manager = User.objects.create_user(
        username="mgr_conc_2",
        email="mgr_conc_2@assethub.local",
        password="Password123!",
        role=User.Role.MANAGER,
        department=dept,
        is_active=True,
    )
    user = User.objects.create_user(
        username="user_conc_single",
        email="u_single@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept,
        is_active=True,
    )

    asset = Asset.objects.create(
        asset_code="AST-CONC-002",
        name="Single Approved Monitor",
        category=cat,
        department=dept,
        status=Asset.Status.AVAILABLE,
    )

    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=user,
        department=dept,
        requested_category=cat,
        reason="Single request to approve",
    )

    barrier = threading.Barrier(2)
    results = []

    def worker():
        try:
            client = auth_client_for(manager)
            barrier.wait(timeout=5)
            response = client.post(
                f"/api/requests/{req.id}/approve/",
                data={"selected_asset_id": asset.id},
                format="json",
            )
            results.append(response.status_code)
        except Exception as exc:
            results.append(exc)
        finally:
            connections.close_all()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    connections.close_all()

    status_codes = sorted([r for r in results if isinstance(r, int)])
    assert status_codes == [200, 409], f"Unexpected results: {results}"

    req.refresh_from_db()
    assert req.status == AssetRequest.Status.APPROVED

    asset.refresh_from_db()
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user == user


@pytest.mark.django_db(transaction=True)
def test_concurrent_identifier_creation():
    """
    Multiple concurrent threads generate identifiers (AST/REQ) when the counter record
    for that kind and year does NOT exist yet.
    All generated identifiers must be unique with zero duplicate errors.
    """
    connections.close_all()

    test_year = 2088
    # Ensure no sequence counter exists for REQ, 2088
    IdentifierSequence.objects.filter(kind=IdentifierSequence.Kind.REQ, year=test_year).delete()

    num_threads = 8
    barrier = threading.Barrier(num_threads)
    generated_codes = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            code = generate_identifier('REQ', year=test_year)
            generated_codes.append(code)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    connections.close_all()

    assert not errors, f"Encountered errors during concurrent identifier generation: {errors}"
    assert len(generated_codes) == num_threads
    assert len(set(generated_codes)) == num_threads, f"Duplicates detected: {generated_codes}"
    for code in generated_codes:
        assert code.startswith(f"REQ-{test_year}-")
