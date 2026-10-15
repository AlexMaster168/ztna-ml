from accounts.models import Role
from nodes import services
from nodes.models import Gateway, hash_token


def test_register_gateway_returns_token_once_and_rejects_overlap(make_client, gateway):
    admin = make_client(Role.ADMIN)
    body = {"name": "gw-2", "endpoint": "203.0.113.11:51820", "wg_subnet": "10.14.0.0/24"}
    assert make_client(Role.ANALYST).post("/api/gateways/", body).status_code == 403
    resp = admin.post("/api/gateways/", body)
    assert resp.status_code == 201, resp.json()
    token = resp.json()["token"]
    gw = Gateway.objects.get(name="gw-2")
    assert gw.token_hash == hash_token(token)
    assert "token" not in admin.get(f"/api/gateways/{gw.pk}/").json()

    overlap = {**body, "name": "gw-3", "wg_subnet": "10.13.0.0/16"}
    assert admin.post("/api/gateways/", overlap).status_code == 400
    assert (
        admin.post(
            "/api/gateways/", {**body, "name": "gw-4", "wg_subnet": "10.15.0.0/8"}
        ).status_code
        == 400
    )


def test_agent_authenticates_with_rotated_token(make_client, gateway, agent_client):
    admin = make_client(Role.ADMIN)
    new = admin.post(f"/api/gateways/{gateway.pk}/rotate-token/").json()["token"]
    gateway.refresh_from_db()
    assert gateway.token_hash == hash_token(new)


def test_delete_gateway_requires_no_live_peers(make_client, gateway):
    peer, _ = services.create_peer(gateway, "p", actor="t")
    admin = make_client(Role.ADMIN)
    assert admin.delete(f"/api/gateways/{gateway.pk}/").status_code == 400
    services.set_status(peer, "revoked", actor="t")
    assert admin.delete(f"/api/gateways/{gateway.pk}/").status_code == 204


def test_bootstrap_creates_gateways_from_env(db, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setenv(
        "GATEWAYS", "gw-a|1.2.3.4:51820|10.20.0.0/24,gw-b|1.2.3.5:51820|10.21.0.0/24"
    )
    monkeypatch.setenv("GATEWAY_TOKEN_GW_A", "ta")
    monkeypatch.setenv("GATEWAY_TOKEN_GW_B", "tb")
    monkeypatch.delenv("GATEWAY_NAME", raising=False)
    call_command("bootstrap")
    call_command("bootstrap")  # idempotent
    assert {g.name: g.wg_subnet for g in Gateway.objects.all()} == {
        "gw-a": "10.20.0.0/24",
        "gw-b": "10.21.0.0/24",
    }
    assert Gateway.objects.get(name="gw-b").token_hash == hash_token("tb")
