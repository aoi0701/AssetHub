import pytest
from rest_framework.test import APIClient
from apps.assets.models import Asset
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def auth_client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_asset_pagination_returns_standard_page_metadata(admin_user, dept_it):
    cat = Category.objects.create(name="Pagination Laptops", description="For pagination test")
    for i in range(20):
        Asset.objects.create(
            asset_code=f"AST-PAG-{i:03d}",
            name=f"Laptop Pag {i}",
            category=cat,
            department=dept_it,
            status=Asset.Status.AVAILABLE,
        )

    client = auth_client_for(admin_user)
    res = client.get("/api/assets/?page=1")
    assert res.status_code == 200
    assert "count" in res.data
    assert "total_pages" in res.data
    assert "current_page" in res.data
    assert "page_size" in res.data
    assert res.data["page_size"] == 15
    assert len(res.data["results"]) == 15
    assert res.data["total_pages"] == 2
    assert res.data["current_page"] == 1


@pytest.mark.django_db
def test_category_pagination_and_dropdown_fallback(admin_user):
    for i in range(25):
        Category.objects.create(name=f"Category Pag {i:02d}", description=f"Desc {i}")

    client = auth_client_for(admin_user)

    # With page=1: paginated with standard metadata
    res_page = client.get("/api/categories/?page=1")
    assert res_page.status_code == 200
    assert isinstance(res_page.data, dict)
    assert res_page.data["count"] == 25
    assert res_page.data["page_size"] == 15
    assert len(res_page.data["results"]) == 15
    assert res_page.data["total_pages"] == 2

    # Without page (or with all=true): unpaginated list for dropdowns
    res_all = client.get("/api/categories/")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) == 25


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


@pytest.mark.django_db
def test_department_pagination_and_dropdown_fallback(admin_user):
    for i in range(20):
        Department.objects.create(name=f"Branch {i:02d}", code=f"BR{i:02d}", description=f"Dept {i}")

    client = auth_client_for(admin_user)

    res_page = client.get("/api/departments/?page=1")
    assert res_page.status_code == 200
    assert isinstance(res_page.data, dict)
    assert res_page.data["page_size"] == 15
    assert len(res_page.data["results"]) == 15
    assert res_page.data["total_pages"] == 2

    res_all = client.get("/api/departments/")
    assert res_all.status_code == 200
    assert isinstance(res_all.data, list)
    assert len(res_all.data) >= 20


@pytest.mark.django_db
def test_requests_pagination(admin_user, regular_user, dept_it):
    cat = Category.objects.create(name="Request Category")
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
    res = client.get("/api/requests/?page=1")
    assert res.status_code == 200
    assert isinstance(res.data, dict)
    assert res.data["count"] == 18
    assert res.data["page_size"] == 15
    assert len(res.data["results"]) == 15
    assert res.data["total_pages"] == 2
