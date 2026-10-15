"""Trust scoring and automatic enforcement.

score(peer, t) = clamp(100 - sum(penalty_i * exp(-(t - t_i) / tau)), 0, 100)

Decisions use thresholds from EnforcementSettings; releases require the score to climb
back above a higher threshold (hysteresis) and a minimum quarantine time (anti-flapping).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from access import policy as access_policy
from core import live
from nodes import services as node_services
from nodes.models import Gateway, Peer, PeerStatus
from notify.services import enqueue as notify

from .models import (
    AnomalyEvent,
    EnforcementMode,
    EnforcementSettings,
    Incident,
    IncidentStatus,
    Severity,
    TrustScoreSnapshot,
)

log = logging.getLogger(__name__)

ACTOR = "system:trust-engine"
HORIZON_TAUS = 6  # penalties older than 6 tau contribute < 0.25%


def decayed_score(
    penalties: list[tuple[float, datetime]], now: datetime, tau_minutes: float
) -> float:
    tau = tau_minutes * 60
    total = sum(p * math.exp(-max((now - t).total_seconds(), 0) / tau) for p, t in penalties)
    return max(0.0, min(100.0, 100.0 - total))


def severity_for(score: float) -> Severity:
    if score < 10:
        return Severity.CRITICAL
    if score < 40:
        return Severity.HIGH
    if score < 70:
        return Severity.MEDIUM
    return Severity.LOW


SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


@dataclass
class Decision:
    action: str  # "", "quarantine", "revoke", "release"
    reason: str = ""


def decide(peer: Peer, score: float, cfg: EnforcementSettings, now: datetime) -> Decision:
    if peer.status == PeerStatus.REVOKED:
        return Decision("")
    if peer.status == PeerStatus.QUARANTINED:
        if (
            cfg.mode == EnforcementMode.AUTO_REVOKE
            and score < cfg.revoke_below
            and not peer.allowlisted
        ):
            return Decision("revoke", f"trust score {score:.0f} < {cfg.revoke_below:.0f}")
        held_long_enough = peer.quarantined_at and peer.quarantined_at <= now - timedelta(
            minutes=cfg.min_quarantine_minutes
        )
        if (
            cfg.auto_release
            and peer.quarantined_by_system
            and score >= cfg.release_above
            and held_long_enough
        ):
            return Decision("release", f"trust score recovered to {score:.0f}")
        return Decision("")
    # active
    if cfg.mode == EnforcementMode.MONITOR or peer.allowlisted:
        return Decision("")
    if cfg.mode == EnforcementMode.AUTO_REVOKE and score < cfg.revoke_below:
        return Decision("revoke", f"trust score {score:.0f} < {cfg.revoke_below:.0f}")
    if score < cfg.quarantine_below:
        return Decision("quarantine", f"trust score {score:.0f} < {cfg.quarantine_below:.0f}")
    return Decision("")


def current_score(peer: Peer, cfg: EnforcementSettings, now: datetime) -> float:
    since = now - timedelta(minutes=cfg.decay_minutes * HORIZON_TAUS)
    if peer.trust_reset_at and peer.trust_reset_at > since:
        since = peer.trust_reset_at
    penalties = list(
        AnomalyEvent.objects.filter(peer=peer, created_at__gte=since).values_list(
            "penalty", "created_at"
        )
    )
    return decayed_score(penalties, now, cfg.decay_minutes)


def _apply(
    peer: Peer, score: float, cfg: EnforcementSettings, now: datetime, incident: Incident | None
) -> str:
    """Store the score and enforce the decision. Returns the action taken."""
    old = peer.trust_score
    changed = abs(old - score) >= 0.5
    peer.trust_score = round(score, 2)
    peer.save(update_fields=["trust_score"])
    if peer.status == PeerStatus.ACTIVE and access_policy.crosses(
        old, peer.trust_score, access_policy.thresholds()
    ):
        # Resource access depends on the score: the gateway must re-render its policy.
        Gateway.objects.filter(pk=peer.gateway_id).update(revision=F("revision") + 1)
        transaction.on_commit(
            lambda: live.publish("peer.access", {"peer_id": peer.pk, "score": peer.trust_score})
        )
    if changed:
        TrustScoreSnapshot.objects.create(peer=peer, score=peer.trust_score, created_at=now)
        transaction.on_commit(
            lambda: live.publish("peer.score", {"peer_id": peer.pk, "score": peer.trust_score})
        )

    decision = decide(peer, score, cfg, now)
    if not decision.action:
        return ""
    target = {
        "quarantine": PeerStatus.QUARANTINED,
        "revoke": PeerStatus.REVOKED,
        "release": PeerStatus.ACTIVE,
    }[decision.action]
    node_services.set_status(peer, target, ACTOR, decision.reason, system=True)
    log.info("peer %s: %s (%s)", peer.name, decision.action, decision.reason)
    incident = incident or peer.incidents.exclude(status=IncidentStatus.RESOLVED).first()
    if incident:
        incident.actions = [
            *incident.actions,
            {"action": decision.action, "at": now.isoformat(), "reason": decision.reason},
        ]
        incident.save(update_fields=["actions", "updated_at"])
    return decision.action


def post_release_share(
    record: dict[str, str], details: dict, reset_at: datetime, now: datetime
) -> float:
    """How much of a detection rests on traffic after the last human release.

    A release forgives what the peer did before it. A rule judges one moment: it counts
    in full or not at all. A sequence model judges a look-back period (`since` .. the
    detection), so it costs the share of that period after the release: minutes-old
    history weighs little, while an attack that starts right after a release still
    gains weight window by window.
    """
    reset = reset_at.timestamp()
    end = float(record.get("ts") or now.timestamp())
    since = details.get("since")
    if since is None or end <= float(since):
        return 1.0 if end >= reset else 0.0
    return min(1.0, max(0.0, (end - reset) / (end - float(since))))


@transaction.atomic
def handle_event(record: dict[str, str], now: datetime | None = None) -> AnomalyEvent | None:
    """Ingest one record from events:anomaly (see ztna_ml.worker.event_record)."""
    now = now or timezone.now()
    gateway = Gateway.objects.filter(name=record["gw"]).first()
    if gateway is None:
        log.warning("event for unknown gateway %s", record["gw"])
        return None
    peer = (
        Peer.objects.select_for_update()
        .filter(gateway=gateway, wg_ip=record["peer_ip"])
        .exclude(status=PeerStatus.REVOKED)
        .first()
    )
    event = AnomalyEvent(
        gateway=gateway,
        peer=peer,
        peer_ip=record["peer_ip"],
        source=record["source"],
        kind=record["kind"],
        penalty=float(record["penalty"]),
        details=json.loads(record.get("details") or "{}"),
        window_start=datetime.fromtimestamp(int(record["window_start"]), tz=UTC),
        created_at=now,
    )
    if peer is None:
        event.save()
        return event

    cfg = EnforcementSettings.get()
    if peer.trust_reset_at:
        share = post_release_share(record, event.details, peer.trust_reset_at, now)
        if share < 1:
            event.penalty = round(event.penalty * share, 1)
            event.details = {**event.details, "forgiven": round(1 - share, 2)}
    event.save()
    if event.penalty == 0:
        return event
    score = current_score(peer, cfg, now)

    # Every detection is recorded, but an incident is opened only once trust has dropped
    # noticeably, so isolated weak signals don't flood analysts.
    incident = peer.incidents.exclude(status=IncidentStatus.RESOLVED).first()
    if incident is None and score < cfg.incident_below:
        incident = Incident.objects.create(peer=peer, title=f"{event.kind} from {peer.name}")
        transaction.on_commit(lambda ip=peer.wg_ip: live.mark_suspicious(ip))
        notify(
            "incident.opened",
            severity_for(score),
            {
                "incident_id": incident.pk,
                "peer": peer.name,
                "peer_id": peer.pk,
                "kind": event.kind,
                "score": round(score),
            },
        )
        # Bring along the recent detections that contributed to the drop.
        since = now - timedelta(minutes=cfg.decay_minutes * HORIZON_TAUS)
        peer.events.filter(incident__isnull=True, created_at__gte=since).update(incident=incident)
        event.incident = incident
    elif incident is not None:
        event.incident = incident
        event.save(update_fields=["incident"])

    if incident is not None:
        incident.min_trust_score = min(incident.min_trust_score, score)
        sev = severity_for(incident.min_trust_score)
        if SEVERITY_ORDER.index(sev) > SEVERITY_ORDER.index(Severity(incident.severity)):
            incident.severity = sev
        incident.save(update_fields=["min_trust_score", "severity", "updated_at"])

    _apply(peer, score, cfg, now, incident)
    transaction.on_commit(
        lambda: live.publish(
            "anomaly",
            {
                "peer_id": peer.pk,
                "name": peer.name,
                "kind": event.kind,
                "source": event.source,
                "penalty": event.penalty,
                "incident_id": incident.pk if incident else None,
                "score": peer.trust_score,
            },
        )
    )
    return event


def tick(now: datetime | None = None) -> None:
    """Periodic pass: let scores recover (decay) and lift expired system quarantines."""
    now = now or timezone.now()
    cfg = EnforcementSettings.get()
    candidates = Peer.objects.exclude(status=PeerStatus.REVOKED).filter(trust_score__lt=100)
    for peer_id in candidates.values_list("pk", flat=True):
        with transaction.atomic():
            peer = Peer.objects.select_for_update().get(pk=peer_id)
            _apply(peer, current_score(peer, cfg, now), cfg, now, None)
