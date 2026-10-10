"""gw-agent: reconciles the gateway's WireGuard peers and quarantine set with the
desired state published by the orchestrator, and reports telemetry back.

Fail-static: if the orchestrator is unreachable the last applied state is kept.
Stdlib only, so it runs on the gateway's system Python.
"""

from __future__ import annotations

import json
import logging
import os
import random
import ssl
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("gw-agent")

NFT_SET = ("inet", "ztna", "quarantine")


@dataclass
class WgPeer:
    allowed_ips: frozenset[str]
    latest_handshake: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0


@dataclass
class WgState:
    public_key: str
    peers: dict[str, WgPeer] = field(default_factory=dict)


def parse_wg_dump(text: str) -> WgState:
    """Parse `wg show <if> dump`. First line is the interface, the rest are peers."""
    lines = [ln for ln in text.strip().splitlines() if ln]
    iface = lines[0].split("\t")
    state = WgState(public_key=iface[1])
    for line in lines[1:]:
        pub, _psk, _endpoint, allowed, handshake, rx, tx, _ka = line.split("\t")
        ips = frozenset() if allowed == "(none)" else frozenset(allowed.split(","))
        state.peers[pub] = WgPeer(ips, int(handshake), int(rx), int(tx))
    return state


def parse_nft_set(text: str) -> set[str]:
    """Extract element IPs from `nft -j list set ...`; elements with a timeout are
    wrapped as {"elem": {"val": ip, ...}}."""
    out: set[str] = set()
    for item in json.loads(text).get("nftables", []):
        for elem in item.get("set", {}).get("elem", []):
            out.add(elem["elem"]["val"] if isinstance(elem, dict) else elem)
    return out


def plan(wg: WgState, quarantine: set[str], desired: dict, wg_if: str = "wg0") -> list[list[str]]:
    """Commands that move the gateway from its current state to `desired`.

    Order matters for safety: new isolations are applied first and lifted last.
    """
    want_peers = {p["public_key"]: f"{p['allowed_ip']}/32" for p in desired["peers"]}
    want_q = set(desired["quarantine"])
    family, table, name = NFT_SET
    cmds: list[list[str]] = []

    for ip in sorted(want_q - quarantine):
        cmds.append(["nft", "add", "element", family, table, name, f"{{ {ip} }}"])
    for pub in sorted(set(wg.peers) - set(want_peers)):
        cmds.append(["wg", "set", wg_if, "peer", pub, "remove"])
    for pub, allowed in sorted(want_peers.items()):
        current = wg.peers.get(pub)
        if current is None or current.allowed_ips != frozenset([allowed]):
            cmds.append(["wg", "set", wg_if, "peer", pub, "allowed-ips", allowed])
    for ip in sorted(quarantine - want_q):
        cmds.append(["nft", "delete", "element", family, table, name, f"{{ {ip} }}"])
    return cmds


def build_report(wg: WgState, applied_revision: int) -> dict:
    return {
        "public_key": wg.public_key,
        "applied_revision": applied_revision,
        "peers": [
            {
                "public_key": pub,
                "latest_handshake": p.latest_handshake,
                "rx_bytes": p.rx_bytes,
                "tx_bytes": p.tx_bytes,
            }
            for pub, p in sorted(wg.peers.items())
        ],
    }


class Gateway:
    """Side-effecting access to wg and nft."""

    def __init__(self, wg_if: str):
        self.wg_if = wg_if

    @staticmethod
    def _run(cmd: list[str]) -> str:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10).stdout

    def read(self) -> tuple[WgState, set[str]]:
        wg = parse_wg_dump(self._run(["wg", "show", self.wg_if, "dump"]))
        q = parse_nft_set(self._run(["nft", "-j", "list", "set", *NFT_SET]))
        return wg, q

    def apply(self, cmds: list[list[str]]) -> bool:
        ok = True
        for cmd in cmds:
            try:
                self._run(cmd)
                log.info("applied: %s", " ".join(cmd))
            except subprocess.CalledProcessError as exc:
                ok = False
                log.error("failed: %s: %s", " ".join(cmd), exc.stderr.strip())
        return ok


class Orchestrator:
    def __init__(self, base_url: str, token: str, ssl_ctx: ssl.SSLContext | None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ssl_ctx = ssl_ctx

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Gateway {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10, context=self.ssl_ctx) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def desired_state(self) -> dict:
        state = self._request("GET", "/api/agent/state")
        assert state is not None
        return state

    def report(self, report: dict) -> None:
        self._request("POST", "/api/agent/report", report)


def make_ssl_context() -> ssl.SSLContext | None:
    ca = os.environ.get("TLS_CA")
    if not ca:
        return None
    ctx = ssl.create_default_context(cafile=ca)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(os.environ["TLS_CERT"], os.environ["TLS_KEY"])
    return ctx


def run_once(gw: Gateway, orch: Orchestrator, applied: int) -> int:
    """One reconcile cycle. Returns the revision now in effect."""
    desired = orch.desired_state()
    wg, q = gw.read()
    cmds = plan(wg, q, desired, gw.wg_if)
    if not cmds or gw.apply(cmds):
        applied = desired["revision"]
    if cmds:
        wg, _ = gw.read()
    orch.report(build_report(wg, applied))
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[gw-agent] %(levelname)s %(message)s")
    interval = float(os.environ.get("RECONCILE_INTERVAL", "5"))
    gw = Gateway(os.environ.get("WG_IF", "wg0"))
    orch = Orchestrator(os.environ["ORCHESTRATOR_AGENT_URL"], os.environ["GATEWAY_TOKEN"], make_ssl_context())
    applied, backoff = 0, interval
    while True:
        try:
            new = run_once(gw, orch, applied)
            if new != applied:
                log.info("in sync at revision %s", new)
            applied, backoff = new, interval
        except Exception as exc:  # keep running; state stays as last applied
            backoff = min(backoff * 2, 60)
            log.warning("reconcile failed (%s: %s); retry in %.0fs", type(exc).__name__, exc, backoff)
        time.sleep(backoff + random.uniform(0, interval / 5))


if __name__ == "__main__":
    main()
