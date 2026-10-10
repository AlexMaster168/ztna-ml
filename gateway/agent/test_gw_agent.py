import json

import gw_agent as ga

DUMP = (
    "PRIVKEY\tSERVERPUB\t51820\toff\n"
    "PEER_A\t(none)\t172.31.0.2:41000\t10.13.0.2/32\t1700000000\t100\t200\t25\n"
    "PEER_STALE\t(none)\t(none)\t10.13.0.9/32\t0\t0\t0\toff\n"
    "PEER_B\t(none)\t(none)\t(none)\t0\t0\t0\toff\n"
)


def test_parse_wg_dump():
    s = ga.parse_wg_dump(DUMP)
    assert s.public_key == "SERVERPUB"
    assert s.peers["PEER_A"] == ga.WgPeer(frozenset({"10.13.0.2/32"}), 1700000000, 100, 200)
    assert s.peers["PEER_B"].allowed_ips == frozenset()


def test_parse_nft_set_plain_and_timeout_elements():
    doc = {
        "nftables": [
            {"metainfo": {}},
            {"set": {"name": "quarantine", "elem": ["10.13.0.3", {"elem": {"val": "10.13.0.4", "timeout": 60}}]}},
        ]
    }
    assert ga.parse_nft_set(json.dumps(doc)) == {"10.13.0.3", "10.13.0.4"}
    assert ga.parse_nft_set(json.dumps({"nftables": [{"set": {"name": "q"}}]})) == set()


def test_plan_converges_and_orders_isolation_first():
    wg = ga.parse_wg_dump(DUMP)
    desired = {
        "revision": 7,
        "peers": [
            {"public_key": "PEER_A", "allowed_ip": "10.13.0.2"},  # unchanged
            {"public_key": "PEER_B", "allowed_ip": "10.13.0.3"},  # needs allowed-ips
            {"public_key": "PEER_NEW", "allowed_ip": "10.13.0.4"},  # missing
        ],
        "quarantine": ["10.13.0.3"],
    }
    cmds = ga.plan(wg, {"10.13.0.8"}, desired)
    assert cmds == [
        ["nft", "add", "element", "inet", "ztna", "quarantine", "{ 10.13.0.3 }"],
        ["wg", "set", "wg0", "peer", "PEER_STALE", "remove"],
        ["wg", "set", "wg0", "peer", "PEER_B", "allowed-ips", "10.13.0.3/32"],
        ["wg", "set", "wg0", "peer", "PEER_NEW", "allowed-ips", "10.13.0.4/32"],
        ["nft", "delete", "element", "inet", "ztna", "quarantine", "{ 10.13.0.8 }"],
    ]


def test_plan_is_empty_when_in_sync():
    wg = ga.parse_wg_dump("PRIV\tPUB\t51820\toff\nPEER_A\t(none)\t(none)\t10.13.0.2/32\t0\t0\t0\toff\n")
    desired = {"revision": 3, "peers": [{"public_key": "PEER_A", "allowed_ip": "10.13.0.2"}], "quarantine": []}
    assert ga.plan(wg, set(), desired) == []


class FakeGateway:
    wg_if = "wg0"

    def __init__(self, fail=False):
        self.wg = ga.WgState("PUB")
        self.q: set[str] = set()
        self.fail = fail

    def read(self):
        return self.wg, set(self.q)

    def apply(self, cmds):
        if self.fail:
            return False
        for c in cmds:
            if c[0] == "wg" and c[-1] != "remove":
                self.wg.peers[c[4]] = ga.WgPeer(frozenset([c[-1]]))
            elif c[0] == "nft" and c[1] == "add":
                self.q.add(c[-1].strip("{} "))
        return True


class FakeOrch:
    def __init__(self, state):
        self.state = state
        self.reports: list[dict] = []

    def desired_state(self):
        return self.state

    def report(self, report):
        self.reports.append(report)


STATE = {"revision": 4, "peers": [{"public_key": "P1", "allowed_ip": "10.13.0.2"}], "quarantine": ["10.13.0.2"]}


def test_run_once_applies_and_reports_revision():
    gw, orch = FakeGateway(), FakeOrch(STATE)
    assert ga.run_once(gw, orch, applied=0) == 4
    assert gw.q == {"10.13.0.2"} and "P1" in gw.wg.peers
    assert orch.reports[-1]["applied_revision"] == 4
    assert orch.reports[-1]["peers"][0]["public_key"] == "P1"


def test_run_once_keeps_old_revision_on_failure():
    gw, orch = FakeGateway(fail=True), FakeOrch(STATE)
    assert ga.run_once(gw, orch, applied=2) == 2
    assert orch.reports[-1]["applied_revision"] == 2
