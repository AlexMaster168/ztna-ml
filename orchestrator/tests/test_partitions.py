from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection

from core import partitions
from nodes import services
from trust.models import AnomalyEvent, TrustScoreSnapshot

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="partitioning is PostgreSQL-only"
)


def test_tables_are_partitioned_and_orm_works_across_days(gateway, settings):
    peer, _ = services.create_peer(gateway, "p", actor="t")
    with connection.cursor() as c:
        for table in partitions.TABLES:
            assert partitions.is_partitioned(c, table)
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    ids = []
    for days_ago in (0, 1, 3):
        e = AnomalyEvent.objects.create(
            gateway=gateway, peer=peer, peer_ip=peer.wg_ip, source="rule", kind="k",
            penalty=1, window_start=today, created_at=today - timedelta(days=days_ago),
        )  # fmt: skip
        ids.append(e.pk)
        TrustScoreSnapshot.objects.create(peer=peer, score=50, created_at=e.created_at)
    assert len(set(ids)) == 3
    ev = AnomalyEvent.objects.get(pk=ids[1])
    ev.kind = "updated"
    ev.save(update_fields=["kind"])  # UPDATE by id only must hit the right partition
    assert AnomalyEvent.objects.get(pk=ids[1]).kind == "updated"

    with connection.cursor() as c:  # the test runs in one transaction; prod autocommits
        c.execute("SET CONSTRAINTS ALL IMMEDIATE")
    settings.EVENTS_RETENTION_DAYS = 2
    settings.SCORES_RETENTION_DAYS = 2
    report = partitions.maintain(today.date())
    assert report["trust_anomalyevent"]["dropped"] >= 1
    assert sorted(AnomalyEvent.objects.values_list("pk", flat=True)) == sorted(ids[:2])
    assert TrustScoreSnapshot.objects.count() == 2
    assert partitions.maintain(today.date())["trust_anomalyevent"]["created"] == 0
