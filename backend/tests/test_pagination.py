import pytest
from rest_framework.test import APIClient
from apps.assets.models import Asset
from apps.authentication.models import User
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def auth_client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def assert_standard_pagination_metadata(data, expected_count, expected_page_size=15, current_page=1):
    assert isinstance(data, dict)
    assert "count" in data
    assert "total_pages" in data
    assert "current_page" in data
    assert "page_size" in data
    assert "next" in data
    assert "previous" in data
    assert "results" in data

    assert data["count"] == expected_count
    assert data["page_size"] == expected_page_size
    assert data["current_page"] == current_page
    expected_total_pages = (expected_count + expected_page_size - 1) // expected_page_size
    assert data["total_pages"] == expected_total_pages
    assert len(data["results"]) == min(expected_page_size, expected_count)


# ---------------------------------------------------------------------------
# 1. /api/assets/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_assets_pagination_and_ordering(admin_user, dept_it):
    cat = Category.objects.create(name="Asset Test Cat", description="Cat for asset pagination")
    for i in range(22):
        Asset.objects.create(
            asset_code=f"AST-PAG-{i:03d}",
            name=f"Laptop Pag {i}",
            category=cat,
            department=dept_it,
            status=Asset.Status.AVAILABLE,
        )

    client = auth_client_for(admin_user)

    # 1. Paginated page 1
    res = client.get("/api/assets/?page=1")
    assert res.status_code == 200
    assert_standard_pagination_metadata(res.data, expected_count=22, expected_page_size=15, current_page=1)

    # Deterministic ordering: -created_at, -id
    expected_ids = list(Asset.objects.all().order_by('-created_at', '-id').values_list('id', flat=True)[:15])
    actual_ids = [a["id"] for a in res.data["results"]]
    assert actual_ids == expected_ids

    # 2. Paginated page 2
    res_p2 = client.get("/api/assets/?page=2")
    assert res_p2.status_code == 200
    assert len(res_p2.data["results"]) == 7
    assert res_p2.data["current_page"] == 2

    # 3. all=true fallback
    res_all = client.get("/api/assets/?all=true")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == 22

    # 4. pagination=false fallback
    res_no_pag = client.get("/api/assets/?pagination=false")
    assert res_no_pag.status_code == 200
    assert isinstance(res_no_pag.data, list)
    assert len(res_no_pag.data) == 22


# ---------------------------------------------------------------------------
# 2. /api/requests/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_requests_pagination_and_ordering(admin_user, regular_user, dept_it):
    cat = Category.objects.create(name="Req Test Cat")
    for i in range(18):
        AssetRequest.objects.create(
            request_code=f"REQ-PAG-{i:03d}",
            requester=regular_user,
            department=dept_it,
            request_type=AssetRequest.RequestType.ALLOCATE,
            requested_category=cat,
            reason=f"Need laptop for project {i}",
            status=AssetRequest.Status.PENDING,
        )

    client = auth_client_for(admin_user)

    # 1. Paginated page 1
    res = client.get("/api/requests/?page=1")
    assert res.status_code == 200
    assert_standard_pagination_metadata(res.data, expected_count=18, expected_page_size=15, current_page=1)

    # Deterministic ordering: -created_at, -id
    expected_ids = list(AssetRequest.objects.all().order_by('-created_at', '-id').values_list('id', flat=True)[:15])
    actual_ids = [r["id"] for r in res.data["results"]]
    assert actual_ids == expected_ids

    # 2. all=true fallback
    res_all = client.get("/api/requests/?all=true")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == 18

    # 3. pagination=false fallback
    res_no_pag = client.get("/api/requests/?pagination=false")
    assert res_no_pag.status_code == 200
    assert isinstance(res_no_pag.data, list)
    assert len(res_no_pag.data) == 18


# ---------------------------------------------------------------------------
# 3. /api/users/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_users_pagination_and_ordering(admin_user, dept_it):
    for i in range(20):
        User.objects.create_user(
            username=f"user_pag_{i:02d}",
            email=f"user_pag_{i:02d}@assethub.local",
            password="TestPassword123!",
            role=User.Role.USER,
            department=dept_it,
            is_active=True,
        )

    client = auth_client_for(admin_user)
    total_users = User.objects.count()

    # 1. Paginated page 1
    res_page = client.get("/api/users/?page=1")
    assert res_page.status_code == 200
    assert_standard_pagination_metadata(res_page.data, expected_count=total_users, expected_page_size=15, current_page=1)

    # Deterministic ordering: -date_joined, -id
    expected_ids = list(User.objects.all().order_by('-date_joined', '-id').values_list('id', flat=True)[:15])
    actual_ids = [u["id"] for u in res_page.data["results"]]
    assert actual_ids == expected_ids

    # 2. Optional pagination dropdown fallback (unpaginated list when no page param)
    res_dropdown = client.get("/api/users/")
    assert res_dropdown.status_code == 200
    assert isinstance(res_dropdown.data, list)
    assert len(res_dropdown.data) == total_users

    # 3. all=true fallback
    res_all = client.get("/api/users/?all=true")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == total_users

    # 4. pagination=false fallback
    res_no_pag = client.get("/api/users/?pagination=false")
    assert res_no_pag.status_code == 200
    assert isinstance(res_no_pag.data, list)
    assert len(res_no_pag.data) == total_users


# ---------------------------------------------------------------------------
# 4. /api/departments/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_departments_pagination_and_ordering(admin_user):
    for i in range(20):
        Department.objects.create(name=f"Branch {i:02d}", code=f"BR{i:02d}", description=f"Dept {i}")

    client = auth_client_for(admin_user)
    total_depts = Department.objects.count()

    # 1. Paginated page 1
    res_page = client.get("/api/departments/?page=1")
    assert res_page.status_code == 200
    assert_standard_pagination_metadata(res_page.data, expected_count=total_depts, expected_page_size=15, current_page=1)

    # Deterministic ordering: id
    expected_ids = list(Department.objects.all().order_by('id').values_list('id', flat=True)[:15])
    actual_ids = [d["id"] for d in res_page.data["results"]]
    assert actual_ids == expected_ids

    # 2. Optional pagination dropdown fallback (unpaginated list when no page param)
    res_dropdown = client.get("/api/departments/")
    assert res_dropdown.status_code == 200
    assert isinstance(res_dropdown.data, list)
    assert len(res_dropdown.data) == total_depts

    # 3. all=true fallback
    res_all = client.get("/api/departments/?all=true")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == total_depts

    # 4. pagination=false fallback
    res_no_pag = client.get("/api/departments/?pagination=false")
    assert res_no_pag.status_code == 200
    assert isinstance(res_no_pag.data, list)
    assert len(res_no_pag.data) == total_depts


# ---------------------------------------------------------------------------
# 5. /api/categories/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_categories_pagination_and_ordering(admin_user):
    for i in range(25):
        Category.objects.create(name=f"Category Pag {i:02d}", description=f"Desc {i}")

    client = auth_client_for(admin_user)
    total_cats = Category.objects.count()

    # 1. Paginated page 1
    res_page = client.get("/api/categories/?page=1")
    assert res_page.status_code == 200
    assert_standard_pagination_metadata(res_page.data, expected_count=total_cats, expected_page_size=15, current_page=1)

    # Deterministic ordering: id
    expected_ids = list(Category.objects.all().order_by('id').values_list('id', flat=True)[:15])
    actual_ids = [c["id"] for c in res_page.data["results"]]
    assert actual_ids == expected_ids

    # 2. Optional pagination dropdown fallback (unpaginated list when no page param)
    res_dropdown = client.get("/api/categories/")
    assert res_dropdown.status_code == 200
    assert isinstance(res_dropdown.data, list)
    assert len(res_dropdown.data) == total_cats

    # 3. all=true fallback
    res_all = client.get("/api/categories/?all=true")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == total_cats

    # 4. pagination=false fallback
    res_no_pag = client.get("/api/categories/?pagination=false")
    assert res_no_pag.status_code == 200
    assert isinstance(res_no_pag.data, list)
    assert len(res_no_pag.data) == total_cats


# ---------------------------------------------------------------------------
# Search across unpaginated / paginated data
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_category_search_applies_across_all_data(admin_user):
    for i in range(20):
        Category.objects.create(name=f"Standard Category {i}", description="General item")
    Category.objects.create(name="Special Server Rack", description="High performance rack")

    client = auth_client_for(admin_user)
    res = client.get("/api/categories/?search=Special&page=1")
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["name"] == "Special Server Rack"
