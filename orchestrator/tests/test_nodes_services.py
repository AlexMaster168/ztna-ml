import pytest

from audit.models import AuditLog
from nodes import services
from nodes.keys import generate_keypair, is_valid_key
from nodes.models import PeerStatus


def test_generated_keys_are_valid():
    priv, pub = generate_keypair()
    assert is_valid_key(priv) and is_valid_key(pub) and priv != pub
    assert not is_valid_key("not-a-key")


def test_create_peer_allocates_first_free_ip_and_returns_config(gateway):
    peer, config = services.create_peer(gateway, "laptop", actor="test")
    assert peer.wg_ip == "10.13.0.2"  # .1 is the gateway
    assert f"Address = {peer.wg_ip}/32" in config
    assert f"PublicKey = {gateway.public_key}" in config
    assert "AllowedIPs = 10.13.0.1/32" in config
    assert "PrivateKey = " in config
    gateway.refresh_from_db()
    assert gateway.revision == 2
    assert AuditLog.objects.filter(action="peer.create", target_id=str(peer.pk)).exists()


def test_create_peer_with_own_public_key_returns_no_config(gateway):
    _, pub = generate_keypair()
    peer, config = services.create_peer(gateway, "byok", actor="test", public_key=pub)
    assert config is None and peer.public_key == pub


def test_create_peer_rejects_bad_key_and_duplicate_name(gateway):
    with pytest.raises(services.PeerError):
        services.create_peer(gateway, "x", actor="test", public_key="bogus")
    services.create_peer(gateway, "dup", actor="test")
    with pytest.raises(services.PeerError):
        services.create_peer(gateway, "dup", actor="test")


def test_ip_pool_exhaustion_and_reuse_after_revoke(gateway):
    # /29 -> hosts .1-.6, .1 is the gateway: 5 peer slots.
    peers = [services.create_peer(gateway, f"p{i}", actor="t")[0] for i in range(5)]
    with pytest.raises(services.PeerError, match="exhausted"):
        services.create_peer(gateway, "overflow", actor="t")
    services.set_status(peers[0], PeerStatus.REVOKED, actor="t")
    reused, _ = services.create_peer(gateway, "late", actor="t")
    assert reused.wg_ip == peers[0].wg_ip


def test_status_transitions(gateway):
    peer, _ = services.create_peer(gateway, "p", actor="t")
    services.set_status(peer, PeerStatus.QUARANTINED, actor="t", reason="brute force")
    assert peer.status_reason == "brute force"
    with pytest.raises(services.PeerError):
        services.set_status(peer, PeerStatus.QUARANTINED, actor="t")
    services.set_status(peer, PeerStatus.ACTIVE, actor="t")
    services.set_status(peer, PeerStatus.REVOKED, actor="t")
    with pytest.raises(services.PeerError):
        services.set_status(peer, PeerStatus.ACTIVE, actor="t")


def test_desired_state_excludes_revoked_and_lists_quarantine(gateway):
    a, _ = services.create_peer(gateway, "a", actor="t")
    b, _ = services.create_peer(gateway, "b", actor="t")
    c, _ = services.create_peer(gateway, "c", actor="t")
    services.set_status(b, PeerStatus.QUARANTINED, actor="t")
    services.set_status(c, PeerStatus.REVOKED, actor="t")
    state = services.desired_state(gateway)
    assert {p["public_key"] for p in state["peers"]} == {a.public_key, b.public_key}
    assert state["quarantine"] == [b.wg_ip]
    assert state["revision"] == 6  # 1 + 3 creates + 2 transitions


def test_rotate_key_changes_key_and_bumps_revision(gateway):
    peer, _ = services.create_peer(gateway, "p", actor="t")
    old_key = peer.public_key
    config = services.rotate_key(peer, actor="t")
    assert peer.public_key != old_key and "PrivateKey" in config
    gateway.refresh_from_db()
    assert gateway.revision == 3
