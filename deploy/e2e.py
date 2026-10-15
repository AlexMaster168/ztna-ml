"""End-to-end scenarios against a running compose stack.

    python deploy/e2e.py [--attacks bruteforce,scanner,exfiltration] [--skip-restart]
                         [--skip-extended] [--train-timeout SECONDS]

Requires deploy/.env (admin credentials) and `docker compose up -d`. Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
API = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000")
SINK = os.environ.get("WEBHOOK_SINK_URL", "http://localhost:8090")
NORMAL_PEERS = ["peer-normal", "peer-normal-2", "peer-dev", "peer-branch"]
ATTACKER = "peer-attacker"
BRANCH_ATTACKER = "peer-branch-attacker"  # behind the second gateway
SHARDS = 8
ISOLATION_SLA = 20  # seconds from attack start to network isolation (rule-detected attacks)
# Attacks only the model catches need several detections to push trust below the
# quarantine threshold. Slow exfiltration is caught by the cumulative 10-minute volume
# rule (200 MB minimum), so it takes minutes by design.
SLOW_SLA = {
    "odd-client": 120,
    "stealth-bruteforce": 240,
    "stealth-enumeration": 240,
    "stealth-exfiltration": 600,
}

failures: list[str] = []


def env() -> dict[str, str]:
    out = {}
    for line in (DEPLOY / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def compose(*args: str, check: bool = False, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(DEPLOY / "docker-compose.yml"), *args],
        capture_output=True, text=True, timeout=timeout, check=check,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )


def http_status(peer: str, path: str = "/", ua: str | None = None) -> str:
    """Status code of a request made inside the peer, through its own gateway."""
    agent = "" if ua is None else f"-A '{ua}'"
    script = (
        f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 2 {agent} "
        f"http://$(gw-ip){path}"
    )
    return compose("exec", "-T", peer, "sh", "-c", script).stdout.strip()


def reachable(peer: str) -> bool:
    return http_status(peer) == "200"


class Api:
    def __init__(self) -> None:
        self.login()

    def login(self) -> None:
        e = env()
        self.token = self._call("POST", "/api/auth/token", {
            "username": e["DJANGO_ADMIN_USERNAME"], "password": e["DJANGO_ADMIN_PASSWORD"]
        }, auth=False)["access"]

    def _call(self, method: str, path: str, body: dict | None = None, auth: bool = True):
        req = urllib.request.Request(
            API + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.token}"} if auth else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 401 or not auth:
                raise
            self.login()  # the access token outlived a long run
            return self._call(method, path, body)
        return json.loads(raw) if raw else None

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self._call("POST", path, body or {})

    def peer(self, name: str) -> dict:
        return next(p for p in self.get("/api/peers/?limit=1000")["results"] if p["name"] == name)


def check(cond: bool, msg: str) -> bool:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)
    return cond


def wait_for(pred, timeout: float, interval: float = 0.5) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if pred():
            return time.monotonic() - start
        time.sleep(interval)
    return None


def nft_quarantine(gateway: str = "gateway") -> str:
    return compose("exec", "-T", gateway, "nft", "list", "set", "inet", "ztna", "quarantine").stdout


def sink_messages() -> list[dict]:
    with urllib.request.urlopen(SINK + "/messages", timeout=5) as resp:
        return json.loads(resp.read())


def ensure_active(api: Api, name: str = ATTACKER) -> dict:
    p = api.peer(name)
    if p["status"] == "quarantined":
        api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e reset"})
    wait_for(lambda: reachable(name), 20)
    return api.peer(name)


def reset_trust(api: Api, name: str) -> None:
    """A human release forgives earlier anomalies; quarantine first if needed."""
    p = api.peer(name)
    if p["status"] == "active":
        api.post(f"/api/peers/{p['id']}/quarantine/", {"reason": "e2e reset"})
    api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e reset"})
    wait_for(lambda: reachable(name), 20)


def resolve_open(api: Api, peer_id: int) -> None:
    for i in api.get(f"/api/incidents/?peer={peer_id}&status=open,acknowledged")["results"]:
        api.post(f"/api/incidents/{i['id']}/resolve/", {"note": "e2e"})


def scenario_attack(api: Api, attack: str, peer: str = ATTACKER, gateway: str = "gateway") -> None:
    print(f"\n[attack: {attack} from {peer}]")
    p = ensure_active(api, peer)
    check(p["status"] == "active" and reachable(peer), "attacker starts active and connected")
    sla = SLOW_SLA.get(attack, ISOLATION_SLA)
    started = time.monotonic()
    compose("exec", "-d", peer, "traffic", attack, "--duration", str(sla + 30))
    t = wait_for(lambda: not reachable(peer), sla + 10, interval=1)
    elapsed = None if t is None else time.monotonic() - started
    check(elapsed is not None and elapsed <= sla,
          f"attacker isolated within {sla}s (took {elapsed and round(elapsed, 1)}s)")
    p = api.peer(peer)
    check(p["status"] == "quarantined", f"API status quarantined (score {p['trust_score']})")
    check(p["wg_ip"] in nft_quarantine(gateway), f"attacker IP is in {gateway}'s quarantine set")
    for normal in NORMAL_PEERS:
        check(reachable(normal), f"{normal} unaffected")
    inc = api.get(f"/api/incidents/?peer={p['id']}&status=open,acknowledged")["results"]
    check(bool(inc), f"open incident exists ({inc and inc[0]['title']})")
    compose("exec", "-T", peer, "pkill", "-f", "run.py")
    if api.peer(peer)["status"] == "quarantined":
        api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e"})
    check(wait_for(lambda: reachable(peer), 15) is not None, "release restores access")
    resolve_open(api, p["id"])


def scenario_notifications() -> None:
    print("\n[notifications]")

    def quarantines():
        return [m for m in sink_messages() if m["body"].get("event") == "peer.quarantined"]

    check(wait_for(lambda: bool(quarantines()), 30) is not None,
          f"quarantine delivered to the webhook ({len(quarantines())} so far)")
    msgs = quarantines()
    check(bool(msgs) and (msgs[-1]["signature"] or "").startswith("sha256="),
          "webhook is HMAC-signed")
    check(bool(msgs) and "карантин" in msgs[-1]["body"].get("text", ""),
          "message text is Ukrainian")


def scenario_graduated_access(api: Api) -> None:
    """A moderate trust drop closes sensitive resources without a quarantine."""
    print("\n[graduated access]")
    reset_trust(api, ATTACKER)
    # Allowlisted peers are scored and subject to resource thresholds but never isolated,
    # so later model detections cannot turn this into a quarantine mid-check.
    pid = api.peer(ATTACKER)["id"]
    api.post(f"/api/peers/{pid}/allowlist/", {"allowlisted": True})
    check(http_status(ATTACKER, "/files/1") == "200", "files reachable at full trust")
    check(http_status(ATTACKER, "/admin") == "403", "admin closed: not in the engineering group")
    http_status(ATTACKER, "/", ua="Nikto/2.5.0")  # scanner user agent: -45
    for _ in range(5):  # requests without a user agent: -10
        http_status(ATTACKER, "/", ua="")
    t = wait_for(lambda: http_status(ATTACKER, "/files/1") == "403", 20)
    p = api.peer(ATTACKER)
    check(t is not None, f"files closed after the trust drop (trust {p['trust_score']:.0f})")
    check(p["status"] == "active" and http_status(ATTACKER, "/") == "200",
          "peer stays active and keeps the portal")
    decisions = {
        r["resource"]: r["decision"]
        for r in api.get(f"/api/peers/{p['id']}/access")["resources"]
    }
    check(decisions.get("files") == "low_trust" and decisions.get("portal") == "allowed",
          f"API explains the decision {decisions}")
    api.post(f"/api/peers/{pid}/allowlist/", {"allowlisted": False})
    reset_trust(api, ATTACKER)
    resolve_open(api, p["id"])
    check(wait_for(lambda: http_status(ATTACKER, "/files/1") == "200", 20) is not None,
          "files reopen after a human release")


def scenario_second_gateway(api: Api) -> None:
    print("\n[second gateway]")
    gws = {g["name"]: g for g in api.get("/api/gateways/")["results"]}
    check("gw-2" in gws and gws["gw-2"]["in_sync"], "gw-2 registered and in sync")
    p = api.peer(BRANCH_ATTACKER)
    check(p["gateway"] == "gw-2" and p["wg_ip"].startswith("10.14."),
          f"branch attacker is on gw-2 ({p['wg_ip']})")
    scenario_attack(api, "bruteforce", BRANCH_ATTACKER, "gateway-2")
    check(p["wg_ip"] not in nft_quarantine("gateway"), "gw-1's quarantine set is untouched")


def lease_owners() -> list[str]:
    script = "for k in $(redis-cli --scan --pattern 'ml:lease:*'); do redis-cli get $k; done"
    return compose("exec", "-T", "redis", "sh", "-c", script).stdout.split()


def scenario_worker_failover(api: Api) -> None:
    """Kill one ML worker: its shards move to the other with their checkpointed state,
    and detection keeps working."""
    print("\n[ml worker failover]")
    ids = compose("ps", "-q", "ml-worker").stdout.split()
    check(len(ids) >= 2, f"{len(ids)} ml-worker replicas running")
    owners = set(lease_owners())
    check(len(owners) >= 2, f"shards are split between workers ({len(owners)} owners)")
    if len(ids) < 2:
        return
    victim = ids[0]
    subprocess.run(["docker", "kill", victim], capture_output=True, check=False)

    def survivors_own_all() -> bool:
        current = lease_owners()
        return len(current) == SHARDS and victim[:12] not in current

    t = wait_for(survivors_own_all, 90, interval=2)
    check(t is not None, f"all shards taken over by the surviving worker ({t and round(t)}s)")
    scenario_attack(api, "bruteforce")
    subprocess.run(["docker", "start", victim], capture_output=True, check=False)
    wait_for(lambda: len(set(lease_owners())) >= 2, 90, interval=3)
    check(len(set(lease_owners())) >= 2, "restarted worker gets its share of shards back")


def scenario_trainer(api: Api, timeout: float) -> None:
    print("\n[continuous training]")
    before = {m["version"] for m in api.get("/api/models/?limit=200")["results"]}
    api.post("/api/models/retrain/")

    def new_shadow() -> bool:
        live = api.get("/api/models/live/")
        idle = (live.get("trainer") or {}).get("state") == "idle"
        return bool(live["shadow"]) and live["shadow"]["version"] not in before and idle

    t = wait_for(new_shadow, timeout, interval=10)
    live = api.get("/api/models/live/")
    trainer = live.get("trainer") or {}
    check(t is not None,
          f"retrained model deployed as shadow ({live['shadow'] and live['shadow']['version']}, "
          f"{t and round(t)}s, gates: {trainer.get('last_reason')})")


def scenario_no_false_positives(api: Api, since: str) -> None:
    print("\n[normal peers]")
    for name in NORMAL_PEERS:
        p = api.peer(name)
        incidents = api.get(f"/api/incidents/?peer={p['id']}&limit=100")["results"]
        inc = sum(1 for i in incidents if i["opened_at"] >= since)  # opened during this run
        check(p["status"] == "active" and inc == 0,
              f"{name}: active, trust {p['trust_score']:.0f}, incidents {inc}")


def scenario_gateway_restart(api: Api) -> None:
    print("\n[gateway restart keeps state]")
    p = ensure_active(api)
    api.post(f"/api/peers/{p['id']}/quarantine/", {"reason": "e2e restart test"})
    wait_for(lambda: p["wg_ip"] in nft_quarantine(), 15)
    compose("restart", "gateway", timeout=60)
    wait_for(lambda: compose("ps", "gateway", "--format", "{{.Health}}").stdout.strip() == "healthy", 60)
    check(wait_for(lambda: p["wg_ip"] in nft_quarantine(), 30) is not None,
          "gw-agent restored quarantine after restart")
    for peer in NORMAL_PEERS:
        check(wait_for(lambda peer=peer: reachable(peer), 30) is not None,
              f"{peer} reconnected after restart")
    check(http_status("peer-dev", "/admin") == "200", "access policy restored after restart")
    api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e"})
    check(wait_for(lambda: reachable(ATTACKER), 30) is not None, "attacker released")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attacks", default="bruteforce,scanner,exfiltration")
    ap.add_argument("--skip-restart", action="store_true")
    ap.add_argument("--skip-extended", action="store_true",
                    help="only the tunnel, attack and restart scenarios")
    ap.add_argument("--train-timeout", type=float, default=0,
                    help="also retrain through the trainer, waiting up to N seconds (0 = skip)")
    args = ap.parse_args()

    api = Api()
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for name in (ATTACKER, BRANCH_ATTACKER):
        ensure_active(api, name)  # leftovers from earlier runs
    for name in NORMAL_PEERS:
        if api.peer(name)["status"] != "active":
            ensure_active(api, name)
    print("[tunnel]")
    for peer in [*NORMAL_PEERS, ATTACKER, BRANCH_ATTACKER]:
        check(wait_for(lambda peer=peer: reachable(peer), 30) is not None, f"{peer} reaches the app")
    check(compose("exec", "-T", NORMAL_PEERS[0], "curl", "-s", "--max-time", "2",
                  "http://172.30.0.10:8081/healthz").returncode != 0,
          "peer cannot reach the backend network directly")
    gws = api.get("/api/gateways/")["results"]
    check(wait_for(lambda: all(g["in_sync"] for g in api.get("/api/gateways/")["results"]), 20)
          is not None, f"{len(gws)} gateways in sync with desired state")
    # a peer reset above changes the policy; the gateway applies it within a few seconds
    check(wait_for(lambda: http_status("peer-dev", "/admin") == "200"
                   and http_status("peer-normal", "/admin") == "403", 20) is not None,
          "group policy: engineering reaches /admin, others get 403")

    for attack in filter(None, args.attacks.split(",")):
        scenario_attack(api, attack)
    if not args.skip_extended:
        scenario_notifications()
        scenario_graduated_access(api)
        scenario_second_gateway(api)
        scenario_worker_failover(api)
    if args.train_timeout:
        scenario_trainer(api, args.train_timeout)
    scenario_no_false_positives(api, started_at)
    if not args.skip_restart:
        scenario_gateway_restart(api)

    print(f"\n{'FAILED' if failures else 'PASSED'}: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
