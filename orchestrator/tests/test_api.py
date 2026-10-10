import pytest

from accounts.models import Role
from nodes import services
from nodes.keys import generate_keypair
from nodes.models import Gateway, PeerStatus


@pytest.fixture
def peer(gateway):
    return services.create_peer(gateway, "existing", actor="t")[0]


def test_unauthenticated_is_rejected(make_client, gateway):
    assert make_client(None).get("/api/peers/").status_code == 401


@pytest.mark.parametrize(
    "role, list_, quarantine, create",
    [
        (Role.VIEWER, 200, 403, 403),
        (Role.ANALYST, 200, 200, 403),
        (Role.ADMIN, 200, 200, 201),
    ],
)
def test_role_matrix(make_client, peer, role, list_, quarantine, create):
    client = make_client(role)
    assert client.get("/api/peers/").status_code == list_
    assert client.post(f"/api/peers/{peer.pk}/quarantine/").status_code == quarantine
    assert client.post("/api/peers/", {"name": "new"}).status_code == create


def test_create_returns_config_once(make_client, gateway):
    resp = make_client(Role.ADMIN).post("/api/peers/", {"name": "laptop"})
    assert resp.status_code == 201
    assert "PrivateKey" in resp.json()["client_config"]
    detail = make_client(Role.VIEWER).get(f"/api/peers/{resp.json()['peer']['id']}/").json()
    assert "client_config" not in detail and "private" not in str(detail).lower()


def test_invalid_transition_is_400(make_client, peer):
    resp = make_client(Role.ANALYST).post(f"/api/peers/{peer.pk}/release/")
    assert resp.status_code == 400


def test_revoke_requires_admin(make_client, peer):
    assert make_client(Role.ANALYST).post(f"/api/peers/{peer.pk}/revoke/").status_code == 403
    assert make_client(Role.ADMIN).post(f"/api/peers/{peer.pk}/revoke/").status_code == 200


def test_audit_visible_to_analyst_not_viewer(make_client, peer):
    assert make_client(Role.VIEWER).get("/api/audit").status_code == 403
    resp = make_client(Role.ANALYST).get("/api/audit")
    assert resp.status_code == 200
    assert resp.json()["results"][0]["action"] == "peer.create"


def test_gateways_list(make_client, gateway):
    data = make_client(Role.VIEWER).get("/api/gateways/").json()["results"]
    assert data[0]["name"] == "gw-1" and data[0]["in_sync"] is False


def test_me(make_client):
    assert make_client(Role.ANALYST).get("/api/me").json()["role"] == "analyst"


def test_agent_routes_not_exposed_on_public_urlconf(agent_client, gateway):
    assert agent_client.get("/api/agent/state").status_code == 404


@pytest.mark.urls("ztna.urls_agent")
class TestAgentApi:
    def test_requires_gateway_token(self, make_client, gateway):
        assert make_client(Role.ADMIN).get("/api/agent/state").status_code in (401, 403)

    def test_rejects_unknown_token(self, agent_client, gateway):
        agent_client.credentials(HTTP_AUTHORIZATION="Gateway wrong")
        assert agent_client.get("/api/agent/state").status_code == 401

    def test_state_and_report(self, agent_client, peer):
        services.set_status(peer, PeerStatus.QUARANTINED, actor="t")
        state = agent_client.get("/api/agent/state").json()
        assert state["quarantine"] == [peer.wg_ip]

        _, new_gw_key = generate_keypair()
        report = {
            "public_key": new_gw_key,
            "applied_revision": state["revision"],
            "peers": [
                {
                    "public_key": peer.public_key,
                    "latest_handshake": 1_700_000_000,
                    "rx_bytes": 10,
                    "tx_bytes": 20,
                }
            ],
        }
        assert agent_client.post("/api/agent/report", report, format="json").status_code == 204
        peer.refresh_from_db()
        gw = Gateway.objects.get(pk=peer.gateway_id)
        assert (peer.rx_bytes, peer.tx_bytes) == (10, 20)
        assert peer.last_handshake_at.timestamp() == 1_700_000_000
        assert gw.public_key == new_gw_key and gw.applied_revision == state["revision"]
        assert gw.last_seen_at is not None
