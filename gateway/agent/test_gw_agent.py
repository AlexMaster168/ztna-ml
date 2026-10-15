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

        self.policy = ""
        self.policy_ok = True

    def read(self):
        return self.wg, set(self.q)

    def current_policy(self):
        return self.policy

    def apply_policy(self, text):
        if self.policy_ok:
            self.policy = text
        return self.policy_ok

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


POLICY = {
    "resources": [
        {"name": "files", "path_prefix": "/files/", "upstream": "http://app:8000"},
        {"name": "admin", "path_prefix": "/admin", "upstream": "http://app:8000"},
        {"name": "v12", "path_prefix": "/v1.2", "upstream": "http://app:8000"},
        {"name": "portal", "path_prefix": "/", "upstream": "http://app:8000"},
        # never rendered: unsafe values smuggled through the control plane
        {"name": "evil", "path_prefix": '/x"; return 200', "upstream": "http://app:8000"},
        {"name": "evil2", "path_prefix": "/y", "upstream": "http://a;b"},
    ],
    "access": {"10.13.0.2": ["admin", "portal", "evil"], 'bogus"ip': ["portal"]},
}


def test_render_policy_orders_escapes_and_filters():
    text = ga.render_policy(POLICY)
    lines = [ln.strip() for ln in text.splitlines()]
    order = [ln.split()[1].rstrip(";") for ln in lines if ln.startswith('"~^')]
    assert order == ["files", "admin", "v12", "portal"]
    assert '"~^/admin(/|$)" admin;' in lines  # /admin, /admin/x but not /administrator
    assert '"~^/files/" files;' in lines
    assert r'"~^/v1\.2(/|$)" v12;' in lines
    assert '"10.13.0.2|admin" 1;' in lines and '"10.13.0.2|portal" 1;' in lines
    assert "evil" not in text and "bogus" not in text


def test_render_policy_without_policy_denies_everything():
    text = ga.render_policy(None)
    assert "default 0;" in text and "|" not in text.split("$ztna_allow")[1].split("default 0;")[1]


def test_run_once_applies_policy_and_holds_revision_if_rejected():
    gw, orch = FakeGateway(), FakeOrch({**STATE, "policy": POLICY})
    assert ga.run_once(gw, orch, applied=0) == 4
    assert '"10.13.0.2|admin" 1;' in gw.policy
    gw2 = FakeGateway()
    gw2.policy_ok = False
    assert ga.run_once(gw2, FakeOrch({**STATE, "policy": POLICY}), applied=1) == 1
