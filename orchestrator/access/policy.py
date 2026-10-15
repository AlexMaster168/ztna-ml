"""Access decisions: which published resources a peer may reach right now.

The result is part of each gateway's desired state; gw-agent renders it into nginx maps.
A peer's access changes when its trust score crosses a resource threshold, so the trust
engine only bumps the gateway revision on such crossings, not on every score update.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import F

from nodes.models import Gateway, Peer, PeerStatus

from .models import Resource

# Reasons are codes; the dashboard renders them in the user's language.
ALLOWED = "allowed"
DISABLED = "disabled"
NOT_ACTIVE = "peer_not_active"
NOT_IN_GROUP = "not_in_group"
LOW_TRUST = "low_trust"


@dataclass(frozen=True)
class Rule:
    name: str
    path_prefix: str
    upstream: str
    min_trust: float
    group_ids: frozenset[int]
    enabled: bool = True


def load_rules() -> list[Rule]:
    rules = []
    for r in Resource.objects.prefetch_related("groups"):
        rules.append(
            Rule(
                r.name,
                r.path_prefix,
                r.upstream,
                r.min_trust,
                frozenset(g.pk for g in r.groups.all()),
                r.enabled,
            )
        )
    return rules


def decide(status: str, score: float, group_ids: set[int], rule: Rule) -> str:
    if not rule.enabled:
        return DISABLED
    if status != PeerStatus.ACTIVE:
        return NOT_ACTIVE
    if rule.group_ids and not rule.group_ids & group_ids:
        return NOT_IN_GROUP
    if score < rule.min_trust:
        return LOW_TRUST
    return ALLOWED


def thresholds() -> list[float]:
    return sorted(
        set(
            Resource.objects.filter(enabled=True, min_trust__gt=0).values_list(
                "min_trust", flat=True
            )
        )
    )


def crosses(old: float, new: float, limits: list[float]) -> bool:
    """True if moving from `old` to `new` changes which thresholds are met."""
    return any((old >= t) != (new >= t) for t in limits)


def _group_ids(peers: list[Peer]) -> dict[int, set[int]]:
    through = Peer.groups.through
    out: dict[int, set[int]] = {p.pk: set() for p in peers}
    for peer_id, group_id in through.objects.filter(peer__in=peers).values_list(
        "peer_id", "peergroup_id"
    ):
        out[peer_id].add(group_id)
    return out


def gateway_policy(peers: list[Peer]) -> dict:
    """The access part of a gateway's desired state.

    resources are ordered longest prefix first, which is the order nginx must try them in.
    """
    rules = [r for r in load_rules() if r.enabled]
    rules.sort(key=lambda r: (-len(r.path_prefix), r.path_prefix))
    groups = _group_ids(peers)
    access = {}
    for p in peers:
        names = [
            r.name for r in rules if decide(p.status, p.trust_score, groups[p.pk], r) == ALLOWED
        ]
        if names:
            access[p.wg_ip] = sorted(names)
    return {
        "resources": [
            {"name": r.name, "path_prefix": r.path_prefix, "upstream": r.upstream} for r in rules
        ],
        "access": access,
    }


def peer_access(peer: Peer) -> list[dict]:
    groups = _group_ids([peer])[peer.pk]
    return [
        {
            "resource": r.name,
            "path_prefix": r.path_prefix,
            "min_trust": r.min_trust,
            "decision": decide(peer.status, peer.trust_score, groups, r),
        }
        for r in sorted(load_rules(), key=lambda r: r.path_prefix)
    ]


def bump_all_gateways() -> None:
    """Resources and groups are global, so any change is new desired state everywhere."""
    Gateway.objects.update(revision=F("revision") + 1)
