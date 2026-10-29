import yaml
import pytest
from rest_framework import status


@pytest.mark.django_db
def test_swagger_ui_endpoint(api_client):
    """GET /api/docs/ returns HTTP 200 and loads Swagger UI."""
    response = api_client.get('/api/docs/')
    assert response.status_code == status.HTTP_200_OK
    assert b"swagger-ui" in response.content.lower()


@pytest.mark.django_db
def test_schema_endpoint(api_client):
    """GET /api/schema/ returns HTTP 200 and valid OpenAPI schema."""
    response = api_client.get('/api/schema/')
    assert response.status_code == status.HTTP_200_OK

    schema = yaml.safe_load(response.content)
    assert isinstance(schema, dict)
    assert schema.get('openapi', '').startswith('3.')

    # Verify required paths
    paths = schema.get('paths', {})
    required_paths = [
        '/api/auth/register/',
        '/api/auth/login/',
        '/api/auth/refresh/',
        '/api/auth/logout/',
        '/api/auth/profile/',
        '/api/auth/change-password/',
        '/api/users/',
        '/api/departments/',
        '/api/categories/',
        '/api/assets/',
        '/api/assets/{id}/allocate/',
        '/api/assets/{id}/revoke/',
        '/api/assets/{id}/history/',
        '/api/requests/',
        '/api/requests/{id}/approve/',
        '/api/requests/{id}/reject/',
        '/api/me/asset-history/',
        '/api/reports/kpis/',
        '/api/reports/charts/',
    ]
    for p in required_paths:
        assert p in paths, f"Expected path '{p}' not found in OpenAPI schema"

    # Verify security schemes
    components = schema.get('components', {})
    security_schemes = components.get('securitySchemes', {})
    has_bearer = any(
        scheme.get('type') == 'http' and scheme.get('scheme', '').lower() == 'bearer'
        for scheme in security_schemes.values()
    ) or 'jwtAuth' in security_schemes or 'Bearer' in security_schemes
    assert has_bearer, "Expected jwtAuth or Bearer security scheme in schema components"
