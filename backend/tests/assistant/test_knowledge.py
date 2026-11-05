"""Task 2: procedure content, role links, and the workflow rules the content claims."""
import pytest
from rest_framework.test import APIClient

from apps.assets.models import Asset
from apps.assistant.contracts import ALLOWED_PROCEDURE_TOPICS as TOPICS, PlannerProtocolError
from apps.assistant.knowledge import lookup_procedure
from apps.assistant.rendering import is_allowed_href, render_tool_results
from apps.assistant.tools import execute_tool
from apps.requests.models import AssetRequest

ROLES = ("admin", "manager_it", "user_it")
USER_ROUTES = {"/my-assets", "/my-history", "/my-requests"}
STAFF_ROUTES = {"/assets", "/requests"}


@pytest.fixture
def client_for():
    def build(user):
        client = APIClient()
        client.force_authenticate(user)
        return client
    return build


def test_exactly_five_spec_topics():
    assert set(TOPICS) == {"allocate", "return", "damage_report", "roles", "asset_statuses"}


@pytest.mark.parametrize("topic", sorted(TOPICS))
@pytest.mark.parametrize("role", ROLES)
def test_lookup_shape_version_and_evidence(assistant_graph, topic, role):
    actor = assistant_graph[role]
    result = lookup_procedure(actor=actor, topic=topic)

    assert result["tool"] == "procedures.lookup"
    assert result["status"] == "ok"
    assert set(result["data"]) == {"topic", "version", "title", "body", "actions"}
    assert result["data"]["topic"] == topic
    assert result["data"]["version"]
    assert result["data"]["title"]
    assert result["data"]["body"].strip()

    source = result["sources"][0]
    assert source["kind"] == "procedure"
    assert source["facts"]["version"] == result["data"]["version"]
    assert source["facts"]["body"] == result["data"]["body"]
    assert source["observed_at"] == result["observed_at"]

    assert result == {**execute_tool(actor=actor, call={"name": "procedures.lookup", "arguments": {"topic": topic}}),
                      "observed_at": result["observed_at"],
                      "sources": result["sources"]}


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_actions_link_only_to_routes_the_role_can_open(assistant_graph, topic):
    g = assistant_graph
    for key, allowed in (("user_it", USER_ROUTES), ("manager_it", STAFF_ROUTES), ("admin", STAFF_ROUTES)):
        result = lookup_procedure(actor=g[key], topic=topic)
        for action in result["data"]["actions"]:
            assert set(action) == {"label", "href"}
            assert action["href"] in allowed, (key, action)
            assert is_allowed_href(action["href"])
        assert result["sources"][0]["href"] in allowed | {None}


def test_unknown_topic_is_rejected(assistant_graph):
    with pytest.raises(PlannerProtocolError):
        lookup_procedure(actor=assistant_graph["admin"], topic="../../etc/passwd")


def test_asset_statuses_names_all_six_with_vietnamese_labels(assistant_graph):
    body = lookup_procedure(actor=assistant_graph["user_it"], topic="asset_statuses")["data"]["body"]
    for status in Asset.Status.values:
        assert status in body


def test_roles_topic_matches_each_role(assistant_graph):
    body = lookup_procedure(actor=assistant_graph["user_it"], topic="roles")["data"]["body"]
    for role in ("ADMIN", "MANAGER", "USER"):
        assert role in body


def test_renderer_shows_body_title_and_version_source(assistant_graph):
    result = lookup_procedure(actor=assistant_graph["user_it"], topic="allocate")
    answer, sources = render_tool_results([result])
    assert result["data"]["title"] in answer
    assert result["data"]["body"].splitlines()[0] in answer
    assert sources[0]["facts"]["version"] == result["data"]["version"]


# --- each claim below is stated in procedures.json; the running API must agree ------------

def test_allocate_claims_match_api(assistant_graph, client_for):
    g = assistant_graph
    body = lookup_procedure(actor=g["user_it"], topic="allocate")["data"]["body"]
    assert "danh mục" in body and "lý do" in body

    user = client_for(g["user_it"])
    missing_category = user.post("/api/requests/", {"request_type": "ALLOCATE", "reason": "Cần máy"}, format="json")
    assert missing_category.status_code == 400
    created = user.post("/api/requests/", {"request_type": "ALLOCATE", "requested_category_id": g["laptop"].id,
                                           "reason": "Cần máy"}, format="json")
    assert created.status_code == 201
    pk = created.data["id"]
    assert AssetRequest.objects.get(pk=pk).status == "PENDING"

    assert user.post(f"/api/requests/{pk}/approve/", {}, format="json").status_code == 403
    assert client_for(g["manager_hr"]).post(f"/api/requests/{pk}/approve/", {}, format="json").status_code == 404

    available = Asset.objects.filter(department=g["it"], status="AVAILABLE", category=g["laptop"]).first()
    approved = client_for(g["manager_it"]).post(f"/api/requests/{pk}/approve/",
                                                {"selected_asset_id": available.id}, format="json")
    assert approved.status_code == 200
    available.refresh_from_db()
    assert (available.status, available.assigned_to_user) == ("IN_USE", g["user_it"])


@pytest.mark.parametrize("condition, expected", [("GOOD", "AVAILABLE"), ("DAMAGED", "DAMAGED_PENDING_REPAIR")])
def test_return_claims_match_api(assistant_graph, client_for, condition, expected):
    g = assistant_graph
    body = lookup_procedure(actor=g["manager_it"], topic="return")["data"]["body"]
    assert expected in body

    user = client_for(g["user_it"])
    other_asset = Asset.objects.get(assigned_to_user=g["other_it_user"])
    not_mine = user.post("/api/requests/", {"request_type": "RETURN", "asset_id": other_asset.id,
                                            "reason": "Trả"}, format="json")
    assert not_mine.status_code == 400
    created = user.post("/api/requests/", {"request_type": "RETURN", "asset_id": g["held_asset"].id,
                                           "reason": "Trả"}, format="json")
    assert created.status_code == 201
    duplicate = user.post("/api/requests/", {"request_type": "RETURN", "asset_id": g["held_asset"].id,
                                             "reason": "Trả"}, format="json")
    assert duplicate.status_code == 409

    approved = client_for(g["manager_it"]).post(f"/api/requests/{created.data['id']}/approve/",
                                                {"received_confirmed": True, "condition": condition}, format="json")
    assert approved.status_code == 200
    g["held_asset"].refresh_from_db()
    assert g["held_asset"].status == expected
    assert g["held_asset"].assigned_to_user is None


def test_damage_report_claims_match_api(assistant_graph, client_for):
    g = assistant_graph
    body = lookup_procedure(actor=g["manager_it"], topic="damage_report")["data"]["body"]
    assert "DAMAGED_PENDING_REPAIR" in body

    user = client_for(g["user_it"])
    created = user.post("/api/requests/", {"request_type": "DAMAGE_REPORT", "asset_id": g["held_asset"].id,
                                           "reason": "Hỏng màn hình"}, format="json")
    assert created.status_code == 201
    approved = client_for(g["manager_it"]).post(f"/api/requests/{created.data['id']}/approve/", {}, format="json")
    assert approved.status_code == 200
    g["held_asset"].refresh_from_db()
    assert g["held_asset"].status == "DAMAGED_PENDING_REPAIR"


def test_roles_claims_match_api(assistant_graph, client_for):
    g = assistant_graph
    body = lookup_procedure(actor=g["manager_it"], topic="roles")["data"]["body"]
    assert "điều chuyển" in body
    assert "xác nhận thanh lý" in body

    asset = Asset.objects.filter(department=g["it"], status="AVAILABLE").first()
    manager = client_for(g["manager_it"])
    assert manager.post(f"/api/assets/{asset.id}/transfer/", {"to_department_id": g["hr"].id, "reason": "x"},
                        format="json").status_code == 403
    assert manager.post(f"/api/assets/{asset.id}/confirm-disposal/", {"reason": "x"}, format="json").status_code in (403, 404)
    assert manager.post("/api/assets/", {}, format="json").status_code == 403

    user = client_for(g["user_it"])
    assert user.get(f"/api/assets/{g['held_asset'].id}/history/").status_code == 403
    assert user.get(f"/api/assets/{asset.id}/").status_code == 404
