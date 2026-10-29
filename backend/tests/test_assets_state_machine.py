import pytest
from rest_framework.test import APIClient

from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.fixture
def cat_laptop(db):
    return Category.objects.create(name="Laptop", description="Portable computers")


@pytest.fixture
def other_user(db, dept_it):
    return User.objects.create_user(
        username="user_it_2",
        email="user2@assethub.local",
        password="Password123!",
        role=User.Role.USER,
        department=dept_it,
        is_active=True,
    )


# ===========================================================================
# 1. Full Lifecycle Test (CREATE -> ALLOCATE -> REVOKE -> TRANSFER ->
#    DAMAGE -> MAINTENANCE_START -> DISPOSAL_PROPOSE -> DISPOSAL_CONFIRM)
# ===========================================================================

@pytest.mark.django_db
def test_full_asset_lifecycle_transitions_and_history_logs(
    admin_user, dept_it, dept_hr, regular_user, cat_laptop
):
    """
    Test full lifecycle via API:
    CREATE AVAILABLE -> allocate IN_USE -> revoke GOOD AVAILABLE -> transfer AVAILABLE ->
    mark-damaged DAMAGED_PENDING_REPAIR -> send-maintenance UNDER_MAINTENANCE ->
    propose-disposal PENDING_DISPOSAL -> confirm-disposal DISPOSED.
    Assert HTTP status code for each response; exactly 8 audit logs expected in chronological order.
    """
    client = auth_client_for(admin_user)

    # 1. CREATE AVAILABLE
    create_resp = client.post(
        "/api/assets/",
        data={
            "name": "Lifecycle Test ThinkPad",
            "category_id": cat_laptop.id,
            "department_id": dept_it.id,
            "serial_number": "SN-LC-001",
            "reason": "Mua sắm mới cho phòng IT",
        },
        format="json",
    )
    assert create_resp.status_code == 201
    asset_id = create_resp.data["id"]
    asset_code = create_resp.data["asset_code"]
    assert create_resp.data["status"] == "AVAILABLE"
    assert create_resp.data["assigned_to_user_id"] is None
    assert create_resp.data["department_id"] == dept_it.id

    # 2. allocate -> IN_USE
    alloc_resp = client.post(
        f"/api/assets/{asset_id}/allocate/",
        data={
            "assigned_to_user_id": regular_user.id,
            "reason": "Cấp phát cho nhân viên IT thử việc",
        },
        format="json",
    )
    assert alloc_resp.status_code == 200
    assert alloc_resp.data["status"] == "IN_USE"
    assert alloc_resp.data["assigned_to_user_id"] == regular_user.id

    # 3. revoke GOOD -> AVAILABLE
    revoke_resp = client.post(
        f"/api/assets/{asset_id}/revoke/",
        data={
            "received_confirmed": True,
            "condition": "GOOD",
            "reason": "Thu hồi máy định kỳ, tình trạng tốt",
        },
        format="json",
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.data["status"] == "AVAILABLE"
    assert revoke_resp.data["assigned_to_user_id"] is None

    # 4. transfer -> AVAILABLE (to HR department)
    transfer_resp = client.post(
        f"/api/assets/{asset_id}/transfer/",
        data={
            "to_department_id": dept_hr.id,
            "reason": "Điều chuyển thiết bị sang phòng Nhân sự",
        },
        format="json",
    )
    assert transfer_resp.status_code == 200
    assert transfer_resp.data["status"] == "AVAILABLE"
    assert transfer_resp.data["department_id"] == dept_hr.id
    assert transfer_resp.data["assigned_to_user_id"] is None

    # 5. mark-damaged -> DAMAGED_PENDING_REPAIR
    damage_resp = client.post(
        f"/api/assets/{asset_id}/mark-damaged/",
        data={
            "reason": "Kiểm tra kho HR phát hiện màn hình nứt",
        },
        format="json",
    )
    assert damage_resp.status_code == 200
    assert damage_resp.data["status"] == "DAMAGED_PENDING_REPAIR"

    # 6. send-maintenance -> UNDER_MAINTENANCE
    maint_resp = client.post(
        f"/api/assets/{asset_id}/send-maintenance/",
        data={
            "reason": "Gửi sang trung tâm bảo hành Lenovo",
        },
        format="json",
    )
    assert maint_resp.status_code == 200
    assert maint_resp.data["status"] == "UNDER_MAINTENANCE"

    # 7. propose-disposal -> PENDING_DISPOSAL
    prop_resp = client.post(
        f"/api/assets/{asset_id}/propose-disposal/",
        data={
            "reason": "Chi phí sửa mainboard quá cao, đề xuất thanh lý",
        },
        format="json",
    )
    assert prop_resp.status_code == 200
    assert prop_resp.data["status"] == "PENDING_DISPOSAL"
    assert prop_resp.data["assigned_to_user_id"] is None

    # 8. confirm-disposal -> DISPOSED
    conf_resp = client.post(
        f"/api/assets/{asset_id}/confirm-disposal/",
        data={
            "reason": "Hội đồng phê duyệt thanh lý thiết bị hỏng",
        },
        format="json",
    )
    assert conf_resp.status_code == 200
    assert conf_resp.data["status"] == "DISPOSED"

    # Verify history endpoint returns exactly 8 logs
    hist_resp = client.get(f"/api/assets/{asset_id}/history/")
    assert hist_resp.status_code == 200
    logs = hist_resp.data
    assert len(logs) == 8

    expected_actions = [
        AssetHistoryLog.ActionType.CREATE,
        AssetHistoryLog.ActionType.ALLOCATE,
        AssetHistoryLog.ActionType.RETURN,
        AssetHistoryLog.ActionType.TRANSFER,
        AssetHistoryLog.ActionType.DAMAGE,
        AssetHistoryLog.ActionType.MAINTENANCE_START,
        AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
        AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
    ]
    assert [log["action_type"] for log in logs] == expected_actions

    # Check snapshots and details in logs
    assert logs[0]["from_status"] == ""
    assert logs[0]["to_status"] == "AVAILABLE"
    assert logs[0]["asset_code_snapshot"] == asset_code

    assert logs[1]["from_status"] == "AVAILABLE"
    assert logs[1]["to_status"] == "IN_USE"
    assert logs[1]["to_user_id"] == regular_user.id

    assert logs[2]["from_status"] == "IN_USE"
    assert logs[2]["to_status"] == "AVAILABLE"
    assert logs[2]["from_user_id"] == regular_user.id
    assert logs[2]["to_user_id"] is None

    assert logs[3]["action_type"] == "TRANSFER"
    assert logs[3]["from_status"] == "AVAILABLE"
    assert logs[3]["to_status"] == "AVAILABLE"
    assert logs[3]["from_department_id"] == dept_it.id
    assert logs[3]["to_department_id"] == dept_hr.id

    assert logs[4]["from_status"] == "AVAILABLE"
    assert logs[4]["to_status"] == "DAMAGED_PENDING_REPAIR"

    assert logs[5]["from_status"] == "DAMAGED_PENDING_REPAIR"
    assert logs[5]["to_status"] == "UNDER_MAINTENANCE"

    assert logs[6]["from_status"] == "UNDER_MAINTENANCE"
    assert logs[6]["to_status"] == "PENDING_DISPOSAL"

    assert logs[7]["from_status"] == "PENDING_DISPOSAL"
    assert logs[7]["to_status"] == "DISPOSED"


# ===========================================================================
# 2. Invalid Transition Checks (409 Conflict)
# ===========================================================================

@pytest.mark.django_db
def test_available_cannot_skip_damage_confirmation(admin_user, dept_it, cat_laptop):
    """
    AVAILABLE asset calling send-maintenance returns 409 Conflict;
    database and history log remain unchanged.
    """
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-SKIP-001",
        name="Skipping Asset",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )
    initial_log_count = asset.history_logs.count()

    response = client.post(
        f"/api/assets/{asset.id}/send-maintenance/",
        data={"reason": "Trực tiếp đi sửa mà không qua xác nhận hỏng"},
        format="json",
    )
    assert response.status_code == 409

    asset.refresh_from_db()
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.history_logs.count() == initial_log_count


@pytest.mark.django_db
def test_invalid_state_transitions_return_409(admin_user, dept_it, regular_user, cat_laptop):
    """Attempting transitions from invalid source states returns 409 Conflict."""
    client = auth_client_for(admin_user)

    # Asset is IN_USE: cannot mark-damaged directly (must revoke or use damage request)
    asset = Asset.objects.create(
        asset_code="AST-INV-001",
        name="In Use Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
    )
    res_mark = client.post(
        f"/api/assets/{asset.id}/mark-damaged/",
        data={"reason": "Báo hỏng khi đang dùng"},
        format="json",
    )
    assert res_mark.status_code == 409

    # Asset is IN_USE: cannot transfer
    res_transfer = client.post(
        f"/api/assets/{asset.id}/transfer/",
        data={"to_department_id": None, "reason": "Chuyển kho khi đang dùng"},
        format="json",
    )
    assert res_transfer.status_code == 409

    # Asset is IN_USE: cannot allocate again
    res_alloc = client.post(
        f"/api/assets/{asset.id}/allocate/",
        data={"assigned_to_user_id": regular_user.id, "reason": "Cấp đúp"},
        format="json",
    )
    assert res_alloc.status_code == 409

    # Asset is IN_USE: cannot confirm-disposal
    res_conf = client.post(
        f"/api/assets/{asset.id}/confirm-disposal/",
        data={"reason": "Thanh lý khi đang dùng"},
        format="json",
    )
    assert res_conf.status_code == 409


@pytest.mark.django_db
def test_disposed_asset_is_strictly_terminal(admin_user, dept_it, cat_laptop):
    """DISPOSED asset cannot transition to any other status or be modified via PATCH."""
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-DISP-001",
        name="Disposed Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DISPOSED,
    )

    # Any lifecycle action returns 409
    assert client.post(f"/api/assets/{asset.id}/allocate/", data={"assigned_to_user_id": 1, "reason": "x"}, format="json").status_code == 409
    assert client.post(f"/api/assets/{asset.id}/mark-damaged/", data={"reason": "x"}, format="json").status_code == 409
    assert client.post(f"/api/assets/{asset.id}/send-maintenance/", data={"reason": "x"}, format="json").status_code == 409
    assert client.post(f"/api/assets/{asset.id}/transfer/", data={"to_department_id": None, "reason": "x"}, format="json").status_code == 409
    assert client.post(f"/api/assets/{asset.id}/propose-disposal/", data={"reason": "x"}, format="json").status_code == 409

    # PATCH also blocked on DISPOSED
    patch_res = client.patch(f"/api/assets/{asset.id}/", data={"name": "New Name"}, format="json")
    assert patch_res.status_code in (400, 409)


# ===========================================================================
# 3. Complete Maintenance: RETURN_TO_STOCK and RETURN_TO_USER
# ===========================================================================

@pytest.mark.django_db
def test_complete_maintenance_return_to_stock(admin_user, dept_it, regular_user, cat_laptop):
    """
    complete-maintenance with RETURN_TO_STOCK transitions asset to AVAILABLE
    and clears the assigned holder.
    """
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-MAINT-001",
        name="Maintenance Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.UNDER_MAINTENANCE,
        assigned_to_user=regular_user,
    )

    response = client.post(
        f"/api/assets/{asset.id}/complete-maintenance/",
        data={
            "result": "RETURN_TO_STOCK",
            "reason": "Đã bảo trì xong, lưu kho dự phòng",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["status"] == "AVAILABLE"
    assert response.data["assigned_to_user_id"] is None

    asset.refresh_from_db()
    assert asset.status == Asset.Status.AVAILABLE
    assert asset.assigned_to_user is None


@pytest.mark.django_db
def test_complete_maintenance_return_to_user_success(admin_user, dept_it, regular_user, cat_laptop):
    """
    complete-maintenance with RETURN_TO_USER returns asset to original active holder
    in the same department with IN_USE status.
    """
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-MAINT-002",
        name="Maintenance Laptop 2",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.UNDER_MAINTENANCE,
        assigned_to_user=regular_user,
    )

    response = client.post(
        f"/api/assets/{asset.id}/complete-maintenance/",
        data={
            "result": "RETURN_TO_USER",
            "reason": "Bảo dưỡng quạt tản nhiệt xong, bàn giao lại cho nhân viên",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["status"] == "IN_USE"
    assert response.data["assigned_to_user_id"] == regular_user.id

    asset.refresh_from_db()
    assert asset.status == Asset.Status.IN_USE
    assert asset.assigned_to_user == regular_user


@pytest.mark.django_db
def test_complete_maintenance_return_to_user_rejects_missing_or_invalid_holder(
    admin_user, dept_it, dept_hr, regular_user, cat_laptop
):
    """
    complete-maintenance with RETURN_TO_USER fails with 409 if:
    - Asset has no holder
    - Holder was deactivated
    - Holder moved to another department
    """
    client = auth_client_for(admin_user)

    # 1. No holder on asset
    asset_no_holder = Asset.objects.create(
        asset_code="AST-MAINT-003",
        name="No Holder Maint",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.UNDER_MAINTENANCE,
        assigned_to_user=None,
    )
    res1 = client.post(
        f"/api/assets/{asset_no_holder.id}/complete-maintenance/",
        data={"result": "RETURN_TO_USER", "reason": "Bàn giao lại"},
        format="json",
    )
    assert res1.status_code == 409

    # 2. Holder deactivated
    regular_user.is_active = False
    regular_user.save()

    asset_deactivated = Asset.objects.create(
        asset_code="AST-MAINT-004",
        name="Inactive Holder Maint",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.UNDER_MAINTENANCE,
        assigned_to_user=regular_user,
    )
    res2 = client.post(
        f"/api/assets/{asset_deactivated.id}/complete-maintenance/",
        data={"result": "RETURN_TO_USER", "reason": "Bàn giao lại"},
        format="json",
    )
    assert res2.status_code == 409

    # 3. Holder moved to another department
    regular_user.is_active = True
    regular_user.department = dept_hr
    regular_user.save()

    res3 = client.post(
        f"/api/assets/{asset_deactivated.id}/complete-maintenance/",
        data={"result": "RETURN_TO_USER", "reason": "Bàn giao lại"},
        format="json",
    )
    assert res3.status_code == 409


# ===========================================================================
# 4. Propose Disposal: Confirmation Required When Holder Present
# ===========================================================================

@pytest.mark.django_db
def test_propose_disposal_with_holder_requires_received_confirmation(
    admin_user, dept_it, regular_user, cat_laptop
):
    """
    propose-disposal on asset that currently has a holder requires received_confirmed=True.
    Clears holder and writes snapshot to audit log.
    """
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-PROP-001",
        name="Damaged Laptop with Holder",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.DAMAGED_PENDING_REPAIR,
        assigned_to_user=regular_user,
    )

    # Without received_confirmed -> 400 Bad Request
    res_fail = client.post(
        f"/api/assets/{asset.id}/propose-disposal/",
        data={"reason": "Không sửa được"},
        format="json",
    )
    assert res_fail.status_code == 400

    # With received_confirmed=True -> 200 OK, holder cleared
    res_ok = client.post(
        f"/api/assets/{asset.id}/propose-disposal/",
        data={
            "received_confirmed": True,
            "reason": "Đã thu lại xác máy, đề xuất thanh lý",
        },
        format="json",
    )
    assert res_ok.status_code == 200
    assert res_ok.data["status"] == "PENDING_DISPOSAL"
    assert res_ok.data["assigned_to_user_id"] is None

    asset.refresh_from_db()
    assert asset.status == Asset.Status.PENDING_DISPOSAL
    assert asset.assigned_to_user is None


# ===========================================================================
# 5. Revoke condition: DAMAGED path
# ===========================================================================

@pytest.mark.django_db
def test_revoke_damaged_transitions_to_damaged_pending_repair(
    admin_user, dept_it, regular_user, cat_laptop
):
    """
    revoke with condition='DAMAGED' transitions asset to DAMAGED_PENDING_REPAIR
    and clears holder.
    """
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-REVOKE-001",
        name="Revoke Damaged Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.IN_USE,
        assigned_to_user=regular_user,
    )

    response = client.post(
        f"/api/assets/{asset.id}/revoke/",
        data={
            "received_confirmed": True,
            "condition": "DAMAGED",
            "reason": "Thu hồi máy hỏng màn hình từ nhân viên nghỉ việc",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["status"] == "DAMAGED_PENDING_REPAIR"
    assert response.data["assigned_to_user_id"] is None


# ===========================================================================
# 6. Validation Errors (400 Bad Request)
# ===========================================================================

@pytest.mark.django_db
def test_actions_require_non_empty_reason_and_valid_inputs(
    admin_user, dept_it, cat_laptop, regular_user
):
    """Missing or empty reason, or invalid action inputs return 400 Bad Request."""
    client = auth_client_for(admin_user)
    asset = Asset.objects.create(
        asset_code="AST-VAL-001",
        name="Validation Laptop",
        category=cat_laptop,
        department=dept_it,
        status=Asset.Status.AVAILABLE,
    )

    # Empty reason on allocate
    r1 = client.post(
        f"/api/assets/{asset.id}/allocate/",
        data={"assigned_to_user_id": regular_user.id, "reason": "   "},
        format="json",
    )
    assert r1.status_code == 400

    # Missing user on allocate
    r2 = client.post(
        f"/api/assets/{asset.id}/allocate/",
        data={"reason": "Valid reason"},
        format="json",
    )
    assert r2.status_code == 400

    # Revoke missing received_confirmed
    r3 = client.post(
        f"/api/assets/{asset.id}/revoke/",
        data={"condition": "GOOD", "reason": "Valid reason"},
        format="json",
    )
    assert r3.status_code == 400

    # Revoke invalid condition
    r4 = client.post(
        f"/api/assets/{asset.id}/revoke/",
        data={"received_confirmed": True, "condition": "BROKEN", "reason": "Valid reason"},
        format="json",
    )
    assert r4.status_code == 400

    # Complete maintenance invalid result
    r5 = client.post(
        f"/api/assets/{asset.id}/complete-maintenance/",
        data={"result": "UNKNOWN", "reason": "Valid reason"},
        format="json",
    )
    assert r5.status_code == 400
