from datetime import timedelta

import pytest
from django.utils import timezone

from access import policy
from access.models import PeerGroup, Resource
from accounts.models import Role
from nodes import services
from nodes.models import PeerStatus
from trust import engine
from trust.models import EnforcementSettings


@pytest.fixture
def setup(gateway):
    a, _ = services.create_peer(gateway, "a", actor="t")
    b, _ = services.create_peer(gateway, "b", actor="t")
    eng = PeerGroup.objects.create(name="engineering")
    eng.peers.add(a)
    files = Resource.objects.create(
        name="files", path_prefix="/files/", upstream="http://app:8000", min_trust=50
    )
    admin = Resource.objects.create(
        name="admin", path_prefix="/admin", upstream="http://app:8000", min_trust=80
    )
    admin.groups.add(eng)
    return a, b, files, admin


def test_policy_respects_groups_trust_and_status(gateway, setup):
    a, b, _, _ = setup
    state = services.desired_state(gateway)["policy"]
    # Longest prefix first; the default "/" portal from the migration comes last.
    assert [r["name"] for r in state["resources"]] == ["files", "admin", "portal"]
    assert state["access"][a.wg_ip] == ["admin", "files", "portal"]
    assert state["access"][b.wg_ip] == ["files", "portal"]  # not in engineering

    a.trust_score = 60
    a.save()
    assert services.desired_state(gateway)["policy"]["access"][a.wg_ip] == ["files", "portal"]

    services.set_status(b, PeerStatus.QUARANTINED, actor="t")
    assert b.wg_ip not in services.desired_state(gateway)["policy"]["access"]


def test_peer_access_explains_decisions(setup):
    a, b, _, _ = setup
    b.trust_score = 40
    b.save()
    decisions = {d["resource"]: d["decision"] for d in policy.peer_access(b)}
    assert decisions == {
        "admin": policy.NOT_IN_GROUP,
        "files": policy.LOW_TRUST,
        "portal": policy.ALLOWED,
    }


def test_crosses():
    assert policy.crosses(90, 70, [50, 80])
    assert not policy.crosses(90, 85, [50, 80])
    assert policy.crosses(45, 55, [50])


def test_trust_drop_across_threshold_bumps_revision(gateway, setup):
    a, _, _, _ = setup
    gateway.refresh_from_db()
    rev = gateway.revision
    now = timezone.now()
    cfg = EnforcementSettings.get()
    engine._apply(a, 90, cfg, now, None)  # 100 -> 90: no threshold crossed
    gateway.refresh_from_db()
    assert gateway.revision == rev
    engine._apply(a, 70, cfg, now + timedelta(seconds=1), None)  # below admin's 80
    gateway.refresh_from_db()
    assert gateway.revision == rev + 1


def test_resource_api_permissions_validation_and_bump(make_client, gateway):
    payload = {
        "name": "wiki",
        "path_prefix": "/wiki",
        "upstream": "http://wiki:8080",
        "min_trust": 40,
    }
    assert make_client(Role.ANALYST).post("/api/resources/", payload).status_code == 403
    admin = make_client(Role.ADMIN)
    rev = gateway.revision
    resp = admin.post("/api/resources/", payload, format="json")
    assert resp.status_code == 201, resp.json()
    gateway.refresh_from_db()
    assert gateway.revision == rev + 1
    for bad in [
        {"path_prefix": "/x; return 200"},
        {"path_prefix": "no-slash"},
        {"upstream": "http://evil host"},
        {"upstream": "file:///etc/passwd"},
    ]:
        resp = admin.post("/api/resources/", {**payload, "name": "bad", **bad}, format="json")
        assert resp.status_code == 400, bad


def test_peer_groups_endpoint(make_client, gateway, setup):
    _, b, _, _ = setup
    admin = make_client(Role.ADMIN)
    resp = admin.post(f"/api/peers/{b.pk}/groups", {"groups": ["engineering"]}, format="json")
    assert resp.status_code == 200
    access = make_client(Role.VIEWER).get(f"/api/peers/{b.pk}/access").json()
    assert access["groups"] == ["engineering"]
    assert {d["resource"]: d["decision"] for d in access["resources"]}["admin"] == "allowed"
