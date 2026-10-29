import pytest
from unittest.mock import patch
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def extract_items(response_data):
    if isinstance(response_data, dict):
        return response_data.get("results", [])
    return response_data


@pytest.fixture
def cat_laptop(db):
    return Category.objects.create(name="Laptop", description="Portable computers")


@pytest.fixture
def cat_monitor(db):
    return Category.objects.create(name="Monitor", description="Display screens")


@pytest.fixture
def manager_hr(db, dept_hr):
    return User.objects.create_user(
        username="manager_hr",
        email="manager.hr@assethub.local",
        password="ManagerPassword123!",
        role=User.Role.MANAGER,
        full_name="HR Manager",
        department=dept_hr,
        is_active=True,
    )


@pytest.fixture
def hr_user(db, dept_hr):
    return User.objects.create_user(
        username="user_hr",
        email="user.hr@assethub.local",
        password="UserPassword123!",
        role=User.Role.USER,
        full_name="HR Staff",
        department=dept_hr,
        is_active=True,
    )


@pytest.fixture
def available_laptop(db, dept_it, cat_laptop):
    return Asset.objects.create(
        asset_code="AST-2026-001",
        name="Dell Latitude 5420",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
        assigned_to_user=None,
    )


@pytest.fixture
def in_use_laptop(db, dept_it, cat_laptop, regular_user):
    return Asset.objects.create(
        asset_code="AST-2026-002",
        name="ThinkPad T14",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
    )


# ===========================================================================
# 1. ALLOCATE Request Creation Tests
# ===========================================================================

@pytest.mark.django_db
def test_user_can_create_allocate_request_success(regular_user, dept_it, cat_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "ALLOCATE",
        "requested_category_id": cat_laptop.id,
        "reason": "Cần laptop làm việc dự án mới.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 201
    data = response.data
    assert data["request_code"].startswith("REQ-")
    assert data["request_type"] == "ALLOCATE"
    assert data["status"] == "PENDING"
    assert data["requester_id"] == regular_user.id
    assert data["department_id"] == dept_it.id
    assert data["requested_category_id"] == cat_laptop.id
    assert data["asset_id"] is None
    assert data["reason"] == "Cần laptop làm việc dự án mới."

    # Verify database
    req = AssetRequest.objects.get(id=data["id"])
    assert req.status == AssetRequest.Status.PENDING
    assert req.requester == regular_user
    assert req.department == dept_it


@pytest.mark.django_db
def test_user_create_allocate_rejects_client_asset_id(regular_user, cat_laptop, available_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "ALLOCATE",
        "requested_category_id": cat_laptop.id,
        "asset_id": available_laptop.id,
        "reason": "Muốn chọn trước máy này.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 400
    assert "asset_id" in response.data or "non_field_errors" in response.data or "detail" in response.data


@pytest.mark.django_db
def test_user_create_request_requires_non_empty_reason(regular_user, cat_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "ALLOCATE",
        "requested_category_id": cat_laptop.id,
        "reason": "   ",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 400
    assert "reason" in response.data


@pytest.mark.django_db
def test_user_cannot_tamper_request_server_managed_fields(regular_user, dept_it, dept_hr, admin_user, cat_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "ALLOCATE",
        "requested_category_id": cat_laptop.id,
        "reason": "Thử can thiệp quyền và trạng thái.",
        "status": "APPROVED",
        "department_id": dept_hr.id,
        "requester_id": admin_user.id,
        "request_code": "REQ-HACK-999",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 201
    data = response.data
    # Server must ignore or overwrite client-provided managed fields
    assert data["status"] == "PENDING"
    assert data["requester_id"] == regular_user.id
    assert data["department_id"] == dept_it.id
    assert data["request_code"] != "REQ-HACK-999"


# ===========================================================================
# 2. RETURN Request Creation Tests
# ===========================================================================

@pytest.mark.django_db
def test_user_can_create_return_request_success(regular_user, in_use_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "RETURN",
        "asset_id": in_use_laptop.id,
        "reason": "Trả máy do hoàn thành dự án.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 201
    data = response.data
    assert data["request_type"] == "RETURN"
    assert data["status"] == "PENDING"
    assert data["asset_id"] == in_use_laptop.id
    assert data["requester_id"] == regular_user.id

    # Creation of a request DOES NOT change asset status or assignment
    in_use_laptop.refresh_from_db()
    assert in_use_laptop.status == Asset.Status.IN_USE
    assert in_use_laptop.assigned_to_user == regular_user


@pytest.mark.django_db
def test_user_cannot_create_return_for_available_asset(regular_user, available_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "RETURN",
        "asset_id": available_laptop.id,
        "reason": "Trả máy không phải của mình.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_user_cannot_create_return_for_asset_assigned_to_another_user(regular_user, hr_user, cat_laptop, dept_hr):
    other_asset = Asset.objects.create(
        asset_code="AST-2026-003",
        name="HR Laptop",
        category=cat_laptop,
        department=dept_hr,
        status=Asset.Status.IN_USE,
        assigned_to_user=hr_user,
    )
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "RETURN",
        "asset_id": other_asset.id,
        "reason": "Trả máy của người khác.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_user_cannot_create_duplicate_pending_return_request(regular_user, in_use_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "RETURN",
        "asset_id": in_use_laptop.id,
        "reason": "Lần 1 trả máy.",
    }
    resp1 = client.post("/api/requests/", data=payload, format="json")
    assert resp1.status_code == 201

    # Second pending request for same user + RETURN + asset -> rejected
    payload["reason"] = "Lần 2 trả máy trùng lặp."
    resp2 = client.post("/api/requests/", data=payload, format="json")
    assert resp2.status_code in (400, 409)


# ===========================================================================
# 3. DAMAGE_REPORT Request Creation Tests
# ===========================================================================

@pytest.mark.django_db
def test_user_can_create_damage_report_success(regular_user, in_use_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "DAMAGE_REPORT",
        "asset_id": in_use_laptop.id,
        "reason": "Màn hình bị nứt góc phải.",
    }
    response = client.post("/api/requests/", data=payload, format="json")
    assert response.status_code == 201
    data = response.data
    assert data["request_type"] == "DAMAGE_REPORT"
    assert data["status"] == "PENDING"
    assert data["asset_id"] == in_use_laptop.id

    # Creation of a request DOES NOT change asset status or assignment
    in_use_laptop.refresh_from_db()
    assert in_use_laptop.status == Asset.Status.IN_USE
    assert in_use_laptop.assigned_to_user == regular_user


@pytest.mark.django_db
def test_user_cannot_create_duplicate_pending_damage_report(regular_user, in_use_laptop):
    client = auth_client_for(regular_user)
    payload = {
        "request_type": "DAMAGE_REPORT",
        "asset_id": in_use_laptop.id,
        "reason": "Báo hỏng lần 1.",
    }
    resp1 = client.post("/api/requests/", data=payload, format="json")
    assert resp1.status_code == 201

    payload["reason"] = "Báo hỏng lần 2 lặp lại."
    resp2 = client.post("/api/requests/", data=payload, format="json")
    assert resp2.status_code in (400, 409)


# ===========================================================================
# 4. Scoping & Visibility Tests (List & Detail)
# ===========================================================================

@pytest.mark.django_db
def test_request_scoping_for_user_manager_and_admin(
    regular_user, hr_user, manager_user, manager_hr, admin_user, dept_it, dept_hr, cat_laptop
):
    # IT User request
    req_it = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="IT req",
    )
    # HR User request
    req_hr = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=hr_user,
        department=dept_hr,
        requested_category=cat_laptop,
        reason="HR req",
    )

    # 1. User IT only sees req_it
    client_user = auth_client_for(regular_user)
    res_user = client_user.get("/api/requests/")
    assert res_user.status_code == 200
    user_items = extract_items(res_user.data)
    assert len(user_items) == 1
    assert user_items[0]["id"] == req_it.id

    # User IT detail on req_hr -> 404
    assert client_user.get(f"/api/requests/{req_hr.id}/").status_code == 404

    # 2. Manager IT only sees req_it
    client_mgr = auth_client_for(manager_user)
    res_mgr = client_mgr.get("/api/requests/")
    assert res_mgr.status_code == 200
    mgr_items = extract_items(res_mgr.data)
    assert len(mgr_items) == 1
    assert mgr_items[0]["id"] == req_it.id

    # Manager IT detail on req_hr -> 404
    assert client_mgr.get(f"/api/requests/{req_hr.id}/").status_code == 404

    # 3. Admin sees both
    client_admin = auth_client_for(admin_user)
    res_admin = client_admin.get("/api/requests/")
    assert res_admin.status_code == 200
    admin_items = extract_items(res_admin.data)
    assert len(admin_items) == 2
    assert client_admin.get(f"/api/requests/{req_hr.id}/").status_code == 200
    assert client_admin.get(f"/api/requests/{req_it.id}/").status_code == 200


# ===========================================================================
# 5. ALLOCATE Approval Tests
# ===========================================================================

@pytest.mark.django_db
def test_manager_can_approve_allocate_request_success(
    manager_user, regular_user, dept_it, cat_laptop, available_laptop
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Cần máy làm việc.",
    )

    client = auth_client_for(manager_user)
    payload = {"selected_asset_id": available_laptop.id}
    response = client.post(f"/api/requests/{req.id}/approve/", data=payload, format="json")
    assert response.status_code == 200

    # Request state updated
    req.refresh_from_db()
    assert req.status == AssetRequest.Status.APPROVED
    assert req.asset == available_laptop
    assert req.reviewed_by == manager_user
    assert req.reviewed_at is not None

    # Asset state updated
    available_laptop.refresh_from_db()
    assert available_laptop.status == Asset.Status.IN_USE
    assert available_laptop.assigned_to_user == regular_user

    # Exactly one ALLOCATE log created
    logs = AssetHistoryLog.objects.filter(asset=available_laptop)
    assert logs.count() == 1
    log = logs.first()
    assert log.action_type == AssetHistoryLog.ActionType.ALLOCATE
    assert log.actor == manager_user
    assert log.to_user == regular_user


@pytest.mark.django_db
def test_approve_allocate_requires_selected_asset_id(manager_user, regular_user, dept_it, cat_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Cần máy.",
    )
    client = auth_client_for(manager_user)
    response = client.post(f"/api/requests/{req.id}/approve/", data={}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_approve_allocate_rejects_asset_with_mismatched_category(
    manager_user, regular_user, dept_it, cat_laptop, cat_monitor
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Cần laptop.",
    )
    monitor_asset = Asset.objects.create(
        asset_code="AST-2026-099",
        name="Dell Monitor P2419H",
        category=cat_monitor,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    client = auth_client_for(manager_user)
    payload = {"selected_asset_id": monitor_asset.id}
    response = client.post(f"/api/requests/{req.id}/approve/", data=payload, format="json")
    assert response.status_code in (400, 409)


@pytest.mark.django_db
def test_approve_allocate_rejects_asset_not_available(
    manager_user, regular_user, dept_it, cat_laptop, in_use_laptop
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Cần laptop.",
    )
    client = auth_client_for(manager_user)
    payload = {"selected_asset_id": in_use_laptop.id}
    response = client.post(f"/api/requests/{req.id}/approve/", data=payload, format="json")
    assert response.status_code == 409
    req.refresh_from_db()
    assert req.status == AssetRequest.Status.PENDING


# ===========================================================================
# 6. RETURN Approval Tests
# ===========================================================================

@pytest.mark.django_db
def test_approve_return_success_condition_good(manager_user, regular_user, dept_it, in_use_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.RETURN,
        requester=regular_user,
        department=dept_it,
        asset=in_use_laptop,
        reason="Trả laptop.",
    )
    client = auth_client_for(manager_user)
    payload = {
        "received_confirmed": True,
        "condition": "GOOD",
    }
    response = client.post(f"/api/requests/{req.id}/approve/", data=payload, format="json")
    assert response.status_code == 200

    req.refresh_from_db()
    assert req.status == AssetRequest.Status.APPROVED
    assert req.reviewed_by == manager_user

    in_use_laptop.refresh_from_db()
    assert in_use_laptop.status == Asset.Status.AVAILABLE
    assert in_use_laptop.assigned_to_user is None


@pytest.mark.django_db
def test_approve_return_success_condition_damaged(manager_user, regular_user, dept_it, in_use_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.RETURN,
        requester=regular_user,
        department=dept_it,
        asset=in_use_laptop,
        reason="Trả laptop bị hỏng.",
    )
    client = auth_client_for(manager_user)
    payload = {
        "received_confirmed": True,
        "condition": "DAMAGED",
    }
    response = client.post(f"/api/requests/{req.id}/approve/", data=payload, format="json")
    assert response.status_code == 200

    req.refresh_from_db()
    assert req.status == AssetRequest.Status.APPROVED

    in_use_laptop.refresh_from_db()
    assert in_use_laptop.status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert in_use_laptop.assigned_to_user is None


@pytest.mark.django_db
def test_approve_return_requires_received_confirmed_and_valid_condition(
    manager_user, regular_user, dept_it, in_use_laptop
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.RETURN,
        requester=regular_user,
        department=dept_it,
        asset=in_use_laptop,
        reason="Trả laptop.",
    )
    client = auth_client_for(manager_user)

    # Missing confirmation -> 400
    res1 = client.post(f"/api/requests/{req.id}/approve/", data={"received_confirmed": False, "condition": "GOOD"}, format="json")
    assert res1.status_code == 400

    # Invalid condition -> 400
    res2 = client.post(f"/api/requests/{req.id}/approve/", data={"received_confirmed": True, "condition": "BROKEN"}, format="json")
    assert res2.status_code == 400


# ===========================================================================
# 7. DAMAGE_REPORT Approval Tests
# ===========================================================================

@pytest.mark.django_db
def test_approve_damage_report_success_retains_holder(manager_user, regular_user, dept_it, in_use_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=regular_user,
        department=dept_it,
        asset=in_use_laptop,
        reason="Màn hình sọc ngang.",
    )
    client = auth_client_for(manager_user)
    response = client.post(f"/api/requests/{req.id}/approve/", data={}, format="json")
    assert response.status_code == 200

    req.refresh_from_db()
    assert req.status == AssetRequest.Status.APPROVED
    assert req.reviewed_by == manager_user

    # Transitions to DAMAGED_PENDING_REPAIR, retaining holder for accountability
    in_use_laptop.refresh_from_db()
    assert in_use_laptop.status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert in_use_laptop.assigned_to_user == regular_user

    # Log action_type is DAMAGE
    log = AssetHistoryLog.objects.filter(asset=in_use_laptop).latest('created_at')
    assert log.action_type == AssetHistoryLog.ActionType.DAMAGE
    assert log.to_status == Asset.Status.DAMAGED_PENDING_REPAIR
    assert log.to_user == regular_user


@pytest.mark.django_db
def test_approve_damage_report_fails_if_asset_not_in_use(manager_user, regular_user, dept_it, available_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.DAMAGE_REPORT,
        requester=regular_user,
        department=dept_it,
        asset=available_laptop,
        reason="Báo hỏng nhưng máy đã về kho.",
    )
    client = auth_client_for(manager_user)
    response = client.post(f"/api/requests/{req.id}/approve/", data={}, format="json")
    assert response.status_code == 409
    req.refresh_from_db()
    assert req.status == AssetRequest.Status.PENDING


# ===========================================================================
# 8. Rejection Tests
# ===========================================================================

@pytest.mark.django_db
def test_manager_can_reject_request_with_reason(manager_user, regular_user, dept_it, cat_laptop, available_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Xin laptop cấu hình cao.",
    )
    client = auth_client_for(manager_user)
    payload = {"rejection_reason": "Chưa đủ điều kiện cấp phát."}
    response = client.post(f"/api/requests/{req.id}/reject/", data=payload, format="json")
    assert response.status_code == 200

    req.refresh_from_db()
    assert req.status == AssetRequest.Status.REJECTED
    assert req.rejection_reason == "Chưa đủ điều kiện cấp phát."
    assert req.reviewed_by == manager_user
    assert req.reviewed_at is not None

    # Available laptop untouched
    available_laptop.refresh_from_db()
    assert available_laptop.status == Asset.Status.AVAILABLE


@pytest.mark.django_db
def test_reject_request_requires_non_empty_rejection_reason(manager_user, regular_user, dept_it, cat_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Xin laptop.",
    )
    client = auth_client_for(manager_user)
    response = client.post(f"/api/requests/{req.id}/reject/", data={"rejection_reason": "   "}, format="json")
    assert response.status_code == 400


# ===========================================================================
# 9. Permission and Repeated Actions Tests
# ===========================================================================

@pytest.mark.django_db
def test_user_cannot_approve_or_reject(regular_user, dept_it, cat_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Xin laptop.",
    )
    client = auth_client_for(regular_user)
    assert client.post(f"/api/requests/{req.id}/approve/", data={}, format="json").status_code == 403
    assert client.post(f"/api/requests/{req.id}/reject/", data={"rejection_reason": "No"}, format="json").status_code == 403


@pytest.mark.django_db
def test_manager_cannot_approve_or_reject_other_department_request(
    manager_hr, regular_user, dept_it, cat_laptop
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Xin laptop IT.",
    )
    client = auth_client_for(manager_hr)
    assert client.post(f"/api/requests/{req.id}/approve/", data={}, format="json").status_code == 404
    assert client.post(f"/api/requests/{req.id}/reject/", data={"rejection_reason": "No"}, format="json").status_code == 404


@pytest.mark.django_db
def test_repeated_approve_or_reject_returns_conflict(manager_user, regular_user, dept_it, cat_laptop, available_laptop):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Xin laptop.",
    )
    client = auth_client_for(manager_user)
    res1 = client.post(f"/api/requests/{req.id}/approve/", data={"selected_asset_id": available_laptop.id}, format="json")
    assert res1.status_code == 200

    # Repeated approve -> 409 Conflict
    res2 = client.post(f"/api/requests/{req.id}/approve/", data={"selected_asset_id": available_laptop.id}, format="json")
    assert res2.status_code == 409

    # Rejection after approval -> 409 Conflict
    res3 = client.post(f"/api/requests/{req.id}/reject/", data={"rejection_reason": "Late reject"}, format="json")
    assert res3.status_code == 409


# ===========================================================================
# 10. Rollback / Fault-Injection Test
# ===========================================================================

@pytest.mark.django_db
def test_fault_injection_log_creation_failure_rolls_back_asset_and_request(
    manager_user, regular_user, dept_it, cat_laptop, available_laptop
):
    req = AssetRequest.objects.create(
        request_type=AssetRequest.RequestType.ALLOCATE,
        requester=regular_user,
        department=dept_it,
        requested_category=cat_laptop,
        reason="Test rollback.",
    )
    client = auth_client_for(manager_user)

    # Simulate unexpected failure during history log creation inside transaction
    with patch.object(AssetHistoryLog.objects, 'create', side_effect=RuntimeError("Simulated DB failure")):
        with pytest.raises(RuntimeError):
            client.post(
                f"/api/requests/{req.id}/approve/",
                data={"selected_asset_id": available_laptop.id},
                format="json",
            )

    # Verify atomic rollback: Request remains PENDING, Asset remains AVAILABLE
    req.refresh_from_db()
    assert req.status == AssetRequest.Status.PENDING
    assert req.asset is None
    assert req.reviewed_by is None

    available_laptop.refresh_from_db()
    assert available_laptop.status == Asset.Status.AVAILABLE
    assert available_laptop.assigned_to_user is None
