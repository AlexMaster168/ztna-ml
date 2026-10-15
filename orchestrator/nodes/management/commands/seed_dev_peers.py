"""Dev/simulation only: provision peers and drop their client configs into a directory,
mimicking an admin downloading configs and handing them to users."""

import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from nodes import services
from nodes.models import Gateway, Peer, PeerStatus


class Command(BaseCommand):
    help = "Ensure dev peers exist and their wg configs are present in --out."

    def add_arguments(self, parser):
        parser.add_argument("names", nargs="+")
        parser.add_argument("--gateway", required=True, help="default gateway; name@gw overrides")
        parser.add_argument("--out", required=True, type=Path)
        parser.add_argument("--wait", type=int, default=120, help="seconds to wait for gateway")

    def handle(self, names, gateway, out, wait, **options):
        gateways: dict[str, Gateway] = {}
        out.mkdir(parents=True, exist_ok=True)
        for spec in names:
            name, _, gw_name = spec.partition("@")
            gw_name = gw_name or gateway
            if gw_name not in gateways:
                gateways[gw_name] = self._wait_for_gateway(gw_name, wait)
            gw = gateways[gw_name]
            path = out / f"{name}.conf"
            peer = Peer.objects.filter(gateway=gw, name=name).first()
            if peer and peer.status != PeerStatus.REVOKED and path.exists():
                continue
            if peer is None:
                peer, config = services.create_peer(gw, name, actor="system:seed")
            elif peer.status == PeerStatus.REVOKED:
                self.stdout.write(f"{name}: revoked, skipping")
                continue
            else:
                # Config lost (private keys are never stored): issue a new key.
                config = services.rotate_key(peer, actor="system:seed")
            path.write_text(config)
            path.chmod(0o600)
            self.stdout.write(f"{name}: {peer.wg_ip} -> {path}")

    def _wait_for_gateway(self, name: str, wait: int) -> Gateway:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            gw = Gateway.objects.filter(name=name).exclude(public_key="").first()
            if gw:
                return gw
            time.sleep(2)
        raise CommandError(f"gateway {name!r} did not report its public key within {wait}s")
