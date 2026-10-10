"""End-to-end scenarios against a running compose stack.

    python deploy/e2e.py [--attacks bruteforce,scanner,exfiltration] [--skip-restart]

Requires deploy/.env (admin credentials) and `docker compose up -d`. Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
API = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000")
NORMAL_PEERS = ["peer-normal", "peer-normal-2", "peer-dev"]
ATTACKER = "peer-attacker"
ISOLATION_SLA = 20  # seconds from attack start to network isolation (rule-detected attacks)
# Attacks only the model catches are scored when a 30 s window closes and need several
# detections to push trust below the quarantine threshold. Slow exfiltration is caught by
# the cumulative 10-minute volume rule (200 MB minimum), so it takes minutes by design.
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


def reachable(peer: str) -> bool:
    r = compose("exec", "-T", peer, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--max-time", "2", "http://10.13.0.1/")
    return r.stdout.strip() == "200"


class Api:
    def __init__(self) -> None:
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
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


def nft_quarantine() -> str:
    return compose("exec", "-T", "gateway", "nft", "list", "set", "inet", "ztna", "quarantine").stdout


def ensure_attacker_active(api: Api) -> dict:
    p = api.peer(ATTACKER)
    if p["status"] == "quarantined":
        api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e reset"})
    wait_for(lambda: reachable(ATTACKER), 20)
    return api.peer(ATTACKER)


def scenario_attack(api: Api, attack: str) -> None:
    print(f"\n[attack: {attack}]")
    p = ensure_attacker_active(api)
    check(p["status"] == "active" and reachable(ATTACKER), "attacker starts active and connected")
    sla = SLOW_SLA.get(attack, ISOLATION_SLA)
    started = time.monotonic()
    compose("exec", "-d", ATTACKER, "traffic", attack, "--duration", str(sla + 30))
    t = wait_for(lambda: not reachable(ATTACKER), sla + 10, interval=1)
    elapsed = None if t is None else time.monotonic() - started
    check(elapsed is not None and elapsed <= sla,
          f"attacker isolated within {sla}s (took {elapsed and round(elapsed, 1)}s)")
    p = api.peer(ATTACKER)
    check(p["status"] == "quarantined", f"API status quarantined (score {p['trust_score']})")
    check(p["wg_ip"] in nft_quarantine(), "attacker IP is in the gateway quarantine set")
    for peer in NORMAL_PEERS:
        check(reachable(peer), f"{peer} unaffected")
    inc = api.get(f"/api/incidents/?peer={p['id']}&status=open,acknowledged")["results"]
    check(bool(inc), f"open incident exists ({inc and inc[0]['title']})")
    compose("exec", "-T", ATTACKER, "pkill", "-f", "run.py")
    api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e"})
    check(wait_for(lambda: reachable(ATTACKER), 15) is not None, "release restores access")
    for i in inc:
        api.post(f"/api/incidents/{i['id']}/resolve/", {"note": "e2e"})


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
    p = ensure_attacker_active(api)
    api.post(f"/api/peers/{p['id']}/quarantine/", {"reason": "e2e restart test"})
    wait_for(lambda: p["wg_ip"] in nft_quarantine(), 15)
    compose("restart", "gateway", timeout=60)
    wait_for(lambda: compose("ps", "gateway", "--format", "{{.Health}}").stdout.strip() == "healthy", 60)
    check(wait_for(lambda: p["wg_ip"] in nft_quarantine(), 30) is not None,
          "gw-agent restored quarantine after restart")
    for peer in NORMAL_PEERS:
        check(wait_for(lambda peer=peer: reachable(peer), 30) is not None,
              f"{peer} reconnected after restart")
    api.post(f"/api/peers/{p['id']}/release/", {"reason": "e2e"})
    check(wait_for(lambda: reachable(ATTACKER), 30) is not None, "attacker released")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attacks", default="bruteforce,scanner,exfiltration")
    ap.add_argument("--skip-restart", action="store_true")
    args = ap.parse_args()

    api = Api()
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    ensure_attacker_active(api)  # leftovers from earlier runs
    print("[tunnel]")
    for peer in [*NORMAL_PEERS, ATTACKER]:
        check(wait_for(lambda peer=peer: reachable(peer), 30) is not None, f"{peer} reaches the app")
    check(compose("exec", "-T", NORMAL_PEERS[0], "curl", "-s", "--max-time", "2",
                  "http://172.30.0.10:8081/healthz").returncode != 0,
          "peer cannot reach the backend network directly")
    gws = api.get("/api/gateways/")["results"]
    check(all(g["in_sync"] for g in gws), "gateway in sync with desired state")

    for attack in filter(None, args.attacks.split(",")):
        scenario_attack(api, attack)
    scenario_no_false_positives(api, started_at)
    if not args.skip_restart:
        scenario_gateway_restart(api)

    print(f"\n{'FAILED' if failures else 'PASSED'}: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
