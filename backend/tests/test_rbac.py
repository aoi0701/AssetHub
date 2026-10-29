import pytest
from rest_framework.test import APIClient
from apps.authentication.services import generate_tokens_for_user
from apps.departments.models import Category, Department


def auth_client_for(user):
    client = APIClient()
    tokens = generate_tokens_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.fixture
def category_laptop(db):
    return Category.objects.create(
        name="Laptop",
        description="Laptops and notebooks",
    )


@pytest.fixture
def category_monitor(db):
    return Category.objects.create(
        name="Monitor",
        description="External monitors and displays",
    )


# ---------------------------------------------------------------------------
# Department RBAC Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_list_all_departments(admin_user, dept_it, dept_hr):
    client = auth_client_for(admin_user)
    response = client.get("/api/departments/")
    assert response.status_code == 200
    dept_ids = [d["id"] for d in response.data]
    assert dept_it.id in dept_ids
    assert dept_hr.id in dept_ids


@pytest.mark.django_db
def test_admin_can_create_department(admin_user):
    client = auth_client_for(admin_user)
    payload = {
        "name": "Finance & Accounting",
        "code": "FA",
        "description": "Financial management",
    }
    response = client.post("/api/departments/", data=payload, format="json")
    assert response.status_code == 201
    assert response.data["name"] == "Finance & Accounting"
    assert response.data["code"] == "FA"
    assert Department.objects.filter(code="FA").exists()


@pytest.mark.django_db
def test_admin_can_patch_department(admin_user, dept_it):
    client = auth_client_for(admin_user)
    payload = {"description": "Updated IT description"}
    response = client.patch(f"/api/departments/{dept_it.id}/", data=payload, format="json")
    assert response.status_code == 200
    dept_it.refresh_from_db()
    assert dept_it.description == "Updated IT description"


@pytest.mark.django_db
def test_department_delete_not_allowed(admin_user, dept_it):
    client = auth_client_for(admin_user)
    response = client.delete(f"/api/departments/{dept_it.id}/")
    assert response.status_code == 405


@pytest.mark.django_db
def test_manager_only_sees_own_department_in_list(manager_user, dept_it, dept_hr):
    client = auth_client_for(manager_user)
    response = client.get("/api/departments/")
    assert response.status_code == 200
    dept_ids = [d["id"] for d in response.data]
    assert dept_ids == [dept_it.id]


@pytest.mark.django_db
def test_manager_can_get_own_department_detail(manager_user, dept_it):
    client = auth_client_for(manager_user)
    response = client.get(f"/api/departments/{dept_it.id}/")
    assert response.status_code == 200
    assert response.data["id"] == dept_it.id
    assert response.data["code"] == dept_it.code


@pytest.mark.django_db
def test_manager_cannot_get_other_department_detail(manager_user, dept_hr):
    client = auth_client_for(manager_user)
    response = client.get(f"/api/departments/{dept_hr.id}/")
    # Outside scope must return 404 per spec
    assert response.status_code == 404


@pytest.mark.django_db
def test_manager_cannot_create_or_patch_department(manager_user, dept_it):
    client = auth_client_for(manager_user)
    create_resp = client.post(
        "/api/departments/",
        data={"name": "Sales", "code": "SL", "description": "Sales dept"},
        format="json",
    )
    assert create_resp.status_code == 403

    patch_resp = client.patch(
        f"/api/departments/{dept_it.id}/",
        data={"name": "Hacked Name"},
        format="json",
    )
    assert patch_resp.status_code == 403


@pytest.mark.django_db
def test_user_only_sees_own_department_in_list(regular_user, dept_it, dept_hr):
    client = auth_client_for(regular_user)
    response = client.get("/api/departments/")
    assert response.status_code == 200
    dept_ids = [d["id"] for d in response.data]
    assert dept_ids == [dept_it.id]


@pytest.mark.django_db
def test_user_can_get_own_department_detail(regular_user, dept_it):
    client = auth_client_for(regular_user)
    response = client.get(f"/api/departments/{dept_it.id}/")
    assert response.status_code == 200
    assert response.data["id"] == dept_it.id


@pytest.mark.django_db
def test_user_cannot_get_other_department_detail(regular_user, dept_hr):
    client = auth_client_for(regular_user)
    response = client.get(f"/api/departments/{dept_hr.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_user_cannot_create_or_patch_department(regular_user, dept_it):
    client = auth_client_for(regular_user)
    create_resp = client.post(
        "/api/departments/",
        data={"name": "Marketing", "code": "MK", "description": "Marketing"},
        format="json",
    )
    assert create_resp.status_code == 403

    patch_resp = client.patch(
        f"/api/departments/{dept_it.id}/",
        data={"name": "Hacked Name"},
        format="json",
    )
    assert patch_resp.status_code == 403


@pytest.mark.django_db
def test_unauthenticated_cannot_access_departments(api_client, dept_it):
    assert api_client.get("/api/departments/").status_code == 401
    assert api_client.get(f"/api/departments/{dept_it.id}/").status_code == 401
    assert api_client.post("/api/departments/", data={}).status_code == 401


# ---------------------------------------------------------------------------
# Category RBAC Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_can_create_category(admin_user):
    client = auth_client_for(admin_user)
    payload = {"name": "Server", "description": "Rack and blade servers"}
    response = client.post("/api/categories/", data=payload, format="json")
    assert response.status_code == 201
    assert response.data["name"] == "Server"
    assert Category.objects.filter(name="Server").exists()


@pytest.mark.django_db
def test_admin_can_patch_category(admin_user, category_laptop):
    client = auth_client_for(admin_user)
    payload = {"description": "Updated laptops description"}
    response = client.patch(f"/api/categories/{category_laptop.id}/", data=payload, format="json")
    assert response.status_code == 200
    category_laptop.refresh_from_db()
    assert category_laptop.description == "Updated laptops description"


@pytest.mark.django_db
def test_category_delete_not_allowed(admin_user, category_laptop):
    client = auth_client_for(admin_user)
    response = client.delete(f"/api/categories/{category_laptop.id}/")
    assert response.status_code == 405


@pytest.mark.django_db
def test_manager_can_read_categories(manager_user, category_laptop, category_monitor):
    client = auth_client_for(manager_user)
    list_resp = client.get("/api/categories/")
    assert list_resp.status_code == 200
    cat_ids = [c["id"] for c in list_resp.data]
    assert category_laptop.id in cat_ids
    assert category_monitor.id in cat_ids

    detail_resp = client.get(f"/api/categories/{category_laptop.id}/")
    assert detail_resp.status_code == 200
    assert detail_resp.data["id"] == category_laptop.id


@pytest.mark.django_db
def test_manager_cannot_create_or_patch_category(manager_user, category_laptop):
    client = auth_client_for(manager_user)
    create_resp = client.post(
        "/api/categories/",
        data={"name": "Printer", "description": "Office printers"},
        format="json",
    )
    assert create_resp.status_code == 403

    patch_resp = client.patch(
        f"/api/categories/{category_laptop.id}/",
        data={"name": "Manager Renamed"},
        format="json",
    )
    assert patch_resp.status_code == 403


@pytest.mark.django_db
def test_user_can_read_categories(regular_user, category_laptop, category_monitor):
    client = auth_client_for(regular_user)
    list_resp = client.get("/api/categories/")
    assert list_resp.status_code == 200
    cat_ids = [c["id"] for c in list_resp.data]
    assert category_laptop.id in cat_ids
    assert category_monitor.id in cat_ids

    detail_resp = client.get(f"/api/categories/{category_laptop.id}/")
    assert detail_resp.status_code == 200
    assert detail_resp.data["id"] == category_laptop.id


@pytest.mark.django_db
def test_user_cannot_create_or_patch_category(regular_user, category_laptop):
    client = auth_client_for(regular_user)
    create_resp = client.post(
        "/api/categories/",
        data={"name": "Printer", "description": "Office printers"},
        format="json",
    )
    assert create_resp.status_code == 403

    patch_resp = client.patch(
        f"/api/categories/{category_laptop.id}/",
        data={"name": "User Renamed"},
        format="json",
    )
    assert patch_resp.status_code == 403


@pytest.mark.django_db
def test_unauthenticated_cannot_access_categories(api_client, category_laptop):
    assert api_client.get("/api/categories/").status_code == 401
    assert api_client.get(f"/api/categories/{category_laptop.id}/").status_code == 401
    assert api_client.post("/api/categories/", data={}).status_code == 401
