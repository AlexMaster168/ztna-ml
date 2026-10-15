"""All state changes to peers go through here: they bump the gateway revision and audit."""

import ipaddress
from datetime import UTC, datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.services import record
from core import live
from notify.services import enqueue as notify

from .keys import generate_keypair, is_valid_key
from .models import Gateway, Peer, PeerStatus


class PeerError(Exception):
    pass


# Allowed transitions; revoked is terminal.
TRANSITIONS = {
    (PeerStatus.ACTIVE, PeerStatus.QUARANTINED): "peer.quarantine",
    (PeerStatus.QUARANTINED, PeerStatus.ACTIVE): "peer.release",
    (PeerStatus.ACTIVE, PeerStatus.REVOKED): "peer.revoke",
    (PeerStatus.QUARANTINED, PeerStatus.REVOKED): "peer.revoke",
}


def _bump(gateway_id: int) -> None:
    Gateway.objects.filter(pk=gateway_id).update(revision=F("revision") + 1)


def _allocate_ip(gateway: Gateway) -> str:
    used = set(
        Peer.objects.filter(gateway=gateway)
        .exclude(status=PeerStatus.REVOKED)
        .values_list("wg_ip", flat=True)
    )
    used.add(gateway.wg_ip)
    for host in gateway.network.hosts():
        if str(host) not in used:
            return str(host)
    raise PeerError(_("address pool %(subnet)s exhausted") % {"subnet": gateway.wg_subnet})


def register_gateway(name: str, endpoint: str, wg_subnet: str, actor, request=None):
    """Create a gateway record. Returns it with its agent token, shown only once."""
    net = ipaddress.IPv4Network(wg_subnet, strict=True)
    if not 16 <= net.prefixlen <= 30:
        raise PeerError(_("subnet must be between /16 and /30"))
    for other in Gateway.objects.all():
        if net.overlaps(other.network):
            raise PeerError(
                _("subnet overlaps gateway %(name)s (%(subnet)s)")
                % {"name": other.name, "subnet": other.wg_subnet}
            )
    if Gateway.objects.filter(name=name).exists():
        raise PeerError(_("gateway %(name)s already exists") % {"name": name})
    gw = Gateway(name=name, endpoint=endpoint, wg_subnet=str(net))
    token = gw.set_token()
    gw.save()
    record(actor, "gateway.create", gw, {"endpoint": endpoint, "wg_subnet": str(net)}, request)
    return gw, token


def rotate_gateway_token(gateway: Gateway, actor, request=None) -> str:
    token = gateway.set_token()
    gateway.save(update_fields=["token_hash"])
    record(actor, "gateway.rotate_token", gateway, request=request)
    return token


def delete_gateway(gateway: Gateway, actor, request=None) -> None:
    if gateway.peers.exclude(status=PeerStatus.REVOKED).exists():
        raise PeerError(_("gateway still has live peers"))
    record(actor, "gateway.delete", gateway, {"name": gateway.name}, request)
    gateway.peers.all().delete()
    gateway.delete()


def render_client_config(peer: Peer, private_key: str) -> str:
    gw = peer.gateway
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {peer.wg_ip}/32\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {gw.public_key}\n"
        f"Endpoint = {gw.endpoint}\n"
        f"AllowedIPs = {gw.wg_ip}/32\n"
        "PersistentKeepalive = 25\n"
    )


def create_peer(
    gateway: Gateway, name: str, actor, owner=None, public_key: str = "", request=None
) -> tuple[Peer, str | None]:
    """Create a peer. Without `public_key` a keypair is generated and the client config
    (containing the private key) is returned once; it is never stored."""
    if not gateway.public_key:
        raise PeerError(_("gateway has not reported its public key yet"))
    private_key = None
    if public_key:
        if not is_valid_key(public_key):
            raise PeerError(_("invalid WireGuard public key"))
    else:
        private_key, public_key = generate_keypair()

    with transaction.atomic():
        # Row lock serialises IP allocation per gateway.
        gateway = Gateway.objects.select_for_update().get(pk=gateway.pk)
        if Peer.objects.filter(gateway=gateway, name=name).exists():
            raise PeerError(_("peer %(name)s already exists") % {"name": name})
        peer = Peer.objects.create(
            gateway=gateway,
            name=name,
            owner=owner,
            public_key=public_key,
            wg_ip=_allocate_ip(gateway),
        )
        _bump(gateway.pk)
        record(actor, "peer.create", peer, {"wg_ip": peer.wg_ip}, request)

    config = render_client_config(peer, private_key) if private_key else None
    return peer, config


def rotate_key(peer: Peer, actor, request=None) -> str:
    """Issue a new keypair for a live peer and return its fresh client config."""
    if peer.status == PeerStatus.REVOKED:
        raise PeerError(_("peer is revoked"))
    private_key, public_key = generate_keypair()
    with transaction.atomic():
        peer.public_key = public_key
        peer.save(update_fields=["public_key"])
        _bump(peer.gateway_id)
        record(actor, "peer.rotate_key", peer, request=request)
    return render_client_config(peer, private_key)


def set_status(
    peer: Peer,
    new_status: PeerStatus,
    actor,
    reason: str = "",
    request=None,
    system: bool = False,
) -> Peer:
    action = TRANSITIONS.get((PeerStatus(peer.status), new_status))
    if action is None:
        raise PeerError(
            _("cannot change status from %(old)s to %(new)s")
            % {"old": peer.status, "new": new_status}
        )
    with transaction.atomic():
        old = peer.status
        peer.status = new_status
        peer.status_reason = reason
        if new_status == PeerStatus.QUARANTINED:
            peer.quarantined_at = timezone.now()
            peer.quarantined_by_system = system
        else:
            peer.quarantined_at = None
            peer.quarantined_by_system = False
        fields = ["status", "status_reason", "quarantined_at", "quarantined_by_system"]
        if new_status == PeerStatus.ACTIVE and not system:
            # A human release is a decision to trust the peer again: earlier anomalies
            # no longer count, otherwise the engine would re-isolate it on the next tick.
            peer.trust_reset_at = timezone.now()
            peer.trust_score = 100.0
            fields += ["trust_reset_at", "trust_score"]
        peer.save(update_fields=fields)
        if "trust_reset_at" in fields:
            from trust.models import TrustScoreSnapshot  # trust depends on nodes

            TrustScoreSnapshot.objects.create(
                peer=peer, score=100.0, created_at=peer.trust_reset_at
            )
        _bump(peer.gateway_id)
        record(actor, action, peer, {"from": old, "to": new_status, "reason": reason}, request)
        payload = {
            "peer_id": peer.pk,
            "name": peer.name,
            "status": str(new_status),
            "reason": reason,
            "actor": str(getattr(actor, "username", actor)),
        }
        transaction.on_commit(lambda: live.publish("peer.status", payload))
        event, severity = {
            PeerStatus.QUARANTINED: ("peer.quarantined", "high"),
            PeerStatus.REVOKED: ("peer.revoked", "critical"),
            PeerStatus.ACTIVE: ("peer.released", "low"),
        }[new_status]
        notify(event, severity, {**payload, "peer": peer.name})
    return peer


def desired_state(gateway: Gateway) -> dict:
    from access.policy import gateway_policy  # access depends on nodes, not vice versa

    gateway.refresh_from_db(fields=["revision"])
    peers = list(Peer.objects.filter(gateway=gateway).exclude(status=PeerStatus.REVOKED))
    return {
        "revision": gateway.revision,
        "peers": [{"public_key": p.public_key, "allowed_ip": p.wg_ip} for p in peers],
        "quarantine": sorted(p.wg_ip for p in peers if p.status == PeerStatus.QUARANTINED),
        "policy": gateway_policy(peers),
    }


def apply_report(gateway: Gateway, report: dict) -> None:
    fields = {
        "last_seen_at": timezone.now(),
        "applied_revision": int(report.get("applied_revision", 0)),
    }
    if report.get("public_key"):
        fields["public_key"] = report["public_key"]
    Gateway.objects.filter(pk=gateway.pk).update(**fields)

    stats = {s["public_key"]: s for s in report.get("peers", [])}
    peers = list(Peer.objects.filter(gateway=gateway, public_key__in=stats))
    for peer in peers:
        s = stats[peer.public_key]
        hs = int(s.get("latest_handshake", 0))
        peer.last_handshake_at = datetime.fromtimestamp(hs, tz=UTC) if hs else None
        peer.rx_bytes = int(s.get("rx_bytes", 0))
        peer.tx_bytes = int(s.get("tx_bytes", 0))
    Peer.objects.bulk_update(peers, ["last_handshake_at", "rx_bytes", "tx_bytes"])
