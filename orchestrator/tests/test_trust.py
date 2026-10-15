import json
from datetime import UTC, datetime, timedelta

import pytest

from accounts.models import Role
from nodes import services
from nodes.models import PeerStatus
from trust import engine
from trust.models import (
    AnomalyEvent,
    EnforcementMode,
    EnforcementSettings,
    Incident,
    IncidentStatus,
    TrustScoreSnapshot,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def peer(gateway):
    return services.create_peer(gateway, "laptop", actor="t")[0]


def ev(peer, penalty=60.0, kind="brute_force", source="rule", at=None):
    at = at or NOW
    return {
        "gw": peer.gateway.name,
        "peer_ip": peer.wg_ip,
        "source": source,
        "kind": kind,
        "penalty": str(penalty),
        "window_start": str(int(at.timestamp())),
        "ts": str(at.timestamp()),
        "details": json.dumps({"auth_failures": 12}),
    }


def settings_(**kw):
    cfg = EnforcementSettings.get()
    for k, v in kw.items():
        setattr(cfg, k, v)
    cfg.save()
    return cfg


def test_decayed_score():
    assert engine.decayed_score([], NOW, 10) == 100
    assert engine.decayed_score([(60, NOW)], NOW, 10) == pytest.approx(40)
    # one tau later the penalty is down to 1/e
    later = NOW + timedelta(minutes=10)
    assert engine.decayed_score([(60, NOW)], later, 10) == pytest.approx(100 - 60 / 2.71828, 1e-3)
    assert engine.decayed_score([(80, NOW), (80, NOW)], NOW, 10) == 0


def test_event_creates_incident_and_quarantines(peer):
    e = engine.handle_event(ev(peer, 70), now=NOW)
    peer.refresh_from_db()
    assert e.peer == peer and e.incident is not None
    assert peer.trust_score == 30
    assert peer.status == PeerStatus.QUARANTINED and peer.quarantined_by_system
    inc = Incident.objects.get()
    assert inc.severity == "high" and inc.actions[0]["action"] == "quarantine"
    assert TrustScoreSnapshot.objects.filter(peer=peer).count() == 1
    peer.gateway.refresh_from_db()
    assert services.desired_state(peer.gateway)["quarantine"] == [peer.wg_ip]


def test_small_event_lowers_score_without_action(peer):
    engine.handle_event(ev(peer, 20, kind="missing_user_agent"), now=NOW)
    peer.refresh_from_db()
    assert peer.trust_score == 80 and peer.status == PeerStatus.ACTIVE


def test_weak_signal_records_event_without_incident(peer):
    engine.handle_event(ev(peer, 20, kind="traffic_anomaly", source="model"), now=NOW)
    assert AnomalyEvent.objects.get().incident is None
    assert Incident.objects.count() == 0
    # a second signal pushes trust below incident_below: incident opens with both events
    engine.handle_event(ev(peer, 20, kind="traffic_anomaly", source="model"), now=NOW)
    inc = Incident.objects.get()
    assert inc.events.count() == 2


def test_events_accumulate_into_one_incident(peer):
    engine.handle_event(ev(peer, 30, kind="scanner_user_agent"), now=NOW)
    engine.handle_event(ev(peer, 40, kind="path_enumeration"), now=NOW + timedelta(seconds=5))
    peer.refresh_from_db()
    assert Incident.objects.count() == 1 and AnomalyEvent.objects.count() == 2
    assert peer.status == PeerStatus.QUARANTINED


def test_monitor_mode_never_enforces(peer):
    settings_(mode=EnforcementMode.MONITOR)
    engine.handle_event(ev(peer, 95), now=NOW)
    peer.refresh_from_db()
    assert peer.status == PeerStatus.ACTIVE and peer.trust_score == 5
    assert Incident.objects.get().severity == "critical"


def test_allowlisted_peer_is_not_isolated(peer):
    peer.allowlisted = True
    peer.save()
    engine.handle_event(ev(peer, 95), now=NOW)
    peer.refresh_from_db()
    assert peer.status == PeerStatus.ACTIVE


def test_auto_revoke_mode(peer):
    settings_(mode=EnforcementMode.AUTO_REVOKE)
    engine.handle_event(ev(peer, 95), now=NOW)
    peer.refresh_from_db()
    assert peer.status == PeerStatus.REVOKED


def test_auto_release_after_decay_with_hysteresis(peer):
    settings_(decay_minutes=1, min_quarantine_minutes=0, release_above=80)
    engine.handle_event(ev(peer, 70), now=NOW)
    peer.refresh_from_db()
    peer.quarantined_at = NOW
    peer.save()
    # 1 tau later: 100 - 70/e = 74 -> still below release_above, stays quarantined
    engine.tick(now=NOW + timedelta(minutes=1))
    peer.refresh_from_db()
    assert peer.status == PeerStatus.QUARANTINED and 70 < peer.trust_score < 80
    # 2 tau later: 100 - 70/e^2 = 90.5 -> released
    engine.tick(now=NOW + timedelta(minutes=2))
    peer.refresh_from_db()
    assert peer.status == PeerStatus.ACTIVE
    assert [a["action"] for a in Incident.objects.get().actions] == ["quarantine", "release"]


def test_min_quarantine_time_prevents_flapping(peer):
    settings_(decay_minutes=1, min_quarantine_minutes=30)
    engine.handle_event(ev(peer, 70), now=NOW)
    peer.refresh_from_db()
    peer.quarantined_at = NOW
    peer.save()
    engine.tick(now=NOW + timedelta(minutes=10))
    peer.refresh_from_db()
    assert peer.trust_score > 99 and peer.status == PeerStatus.QUARANTINED


def test_manual_quarantine_is_not_auto_released(peer):
    services.set_status(peer, PeerStatus.QUARANTINED, actor="analyst")
    settings_(decay_minutes=1, min_quarantine_minutes=0)
    engine.handle_event(ev(peer, 10), now=NOW)
    engine.tick(now=NOW + timedelta(minutes=30))
    peer.refresh_from_db()
    assert peer.status == PeerStatus.QUARANTINED and not peer.quarantined_by_system


def test_manual_release_forgives_earlier_anomalies(peer):
    engine.handle_event(ev(peer, 70), now=timezone_now())
    peer.refresh_from_db()
    assert peer.status == PeerStatus.QUARANTINED
    services.set_status(peer, PeerStatus.ACTIVE, actor="analyst", reason="checked, legit")
    engine.tick()
    peer.refresh_from_db()
    assert peer.status == PeerStatus.ACTIVE and peer.trust_score == 100
    assert peer.scores.first().score == 100  # the chart shows the jump back
    # new misbehaviour still counts
    now = timezone_now()
    engine.handle_event(ev(peer, 70, at=now), now=now)
    peer.refresh_from_db()
    assert peer.status == PeerStatus.QUARANTINED


def test_unknown_peer_ip_is_recorded_without_incident(gateway):
    rec = {
        "gw": "gw-1",
        "peer_ip": "10.13.0.200",
        "source": "rule",
        "kind": "brute_force",
        "penalty": "60",
        "window_start": "0",
        "details": "{}",
    }
    e = engine.handle_event(rec, now=NOW)
    assert e.peer is None and Incident.objects.count() == 0


def test_incident_api_flow(make_client, peer):
    engine.handle_event(ev(peer, 70), now=NOW)
    inc = Incident.objects.get()
    viewer, analyst = make_client(Role.VIEWER), make_client(Role.ANALYST)
    assert viewer.get("/api/incidents/?status=open").json()["count"] == 1
    detail = viewer.get(f"/api/incidents/{inc.pk}/").json()
    assert detail["events"][0]["kind"] == "brute_force"
    assert viewer.post(f"/api/incidents/{inc.pk}/acknowledge/").status_code == 403
    assert analyst.post(f"/api/incidents/{inc.pk}/acknowledge/").status_code == 200
    resp = analyst.post(f"/api/incidents/{inc.pk}/resolve/", {"note": "false positive"})
    assert resp.status_code == 200
    inc.refresh_from_db()
    assert inc.status == IncidentStatus.RESOLVED and inc.resolution_note == "false positive"
    assert analyst.post(f"/api/incidents/{inc.pk}/resolve/").status_code == 400


def test_scores_and_events_api(make_client, peer):
    engine.handle_event(ev(peer, 30), now=timezone_now())
    c = make_client(Role.VIEWER)
    assert c.get(f"/api/peers/{peer.pk}/scores").json()[0]["score"] == 70
    assert c.get(f"/api/events?peer={peer.pk}").json()[0]["kind"] == "brute_force"


def test_enforcement_settings_api(make_client):
    assert make_client(Role.VIEWER).get("/api/settings/enforcement").json()["mode"] == (
        "auto_quarantine"
    )
    assert make_client(Role.ANALYST).patch("/api/settings/enforcement", {}).status_code == 403
    admin = make_client(Role.ADMIN)
    assert admin.patch("/api/settings/enforcement", {"mode": "monitor"}).status_code == 200
    assert EnforcementSettings.get().mode == "monitor"
    bad = admin.patch("/api/settings/enforcement", {"quarantine_below": 90})
    assert bad.status_code == 400


def test_allowlist_api(make_client, peer):
    assert make_client(Role.ANALYST).post(f"/api/peers/{peer.pk}/allowlist/").status_code == 403
    admin = make_client(Role.ADMIN)
    url = f"/api/peers/{peer.pk}/allowlist/"
    assert admin.post(url, {"allowlisted": True}, format="json").json()["allowlisted"] is True
    assert admin.post(url, {"allowlisted": "false"}).json()["allowlisted"] is False


def timezone_now():
    from django.utils import timezone

    return timezone.now()


def test_release_forgives_detections_based_on_earlier_traffic(peer):
    engine.handle_event(ev(peer, 70), now=NOW)  # quarantined
    released_at = NOW + timedelta(minutes=1)
    peer.refresh_from_db()
    services.set_status(peer, PeerStatus.ACTIVE, actor="analyst")
    peer.trust_reset_at = released_at
    peer.save(update_fields=["trust_reset_at"])
    later = NOW + timedelta(minutes=2)
    # a rule hit on traffic from before the release: recorded, not counted
    e = engine.handle_event(ev(peer, 70), now=later)
    assert e.penalty == 0 and e.details["forgiven"] == 1
    # a sequence model whose look-back (NOW..later) is half before the release
    seq = ev(peer, 30, kind="traffic_anomaly", source="model", at=later)
    seq["details"] = json.dumps({"since": int(NOW.timestamp())})
    e = engine.handle_event(seq, now=later)
    peer.refresh_from_db()
    assert e.penalty == 15 and e.details["forgiven"] == 0.5
    assert peer.status == PeerStatus.ACTIVE
    # judgements on post-release traffic count as usual
    fresh = ev(peer, 70, at=later)
    assert engine.handle_event(fresh, now=later).penalty == 70
    peer.refresh_from_db()
    assert peer.status == PeerStatus.QUARANTINED
