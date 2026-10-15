"""Daily range partitioning of the high-volume time series tables (PostgreSQL only).

Detections and trust score snapshots grow with traffic. Partitioning by day keeps their
indexes small and makes retention a cheap DROP of whole partitions instead of DELETEs.

The trust engine calls maintain() periodically: it creates partitions ahead of time
and drops the ones older than the retention period. On other databases (SQLite in
tests) everything here is a no-op and the tables stay ordinary tables.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta

from django.conf import settings
from django.db import DatabaseError, connection, transaction

log = logging.getLogger(__name__)

# table -> setting holding its retention in days
TABLES = {
    "trust_anomalyevent": "EVENTS_RETENTION_DAYS",
    "trust_trustscoresnapshot": "SCORES_RETENTION_DAYS",
}
DAYS_AHEAD = 14
PART_RE = re.compile(r"_p(\d{8})$")


def enabled() -> bool:
    return connection.vendor == "postgresql"


def _name(table: str, day: date) -> str:
    return f"{table}_p{day:%Y%m%d}"


def is_partitioned(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND n.nspname = current_schema()",
        [table],
    )
    row = cursor.fetchone()
    return bool(row) and row[0] == "p"


def partitions(cursor, table: str) -> dict[date, str]:
    cursor.execute(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s",
        [table],
    )
    out = {}
    for (name,) in cursor.fetchall():
        if m := PART_RE.search(name):
            out[datetime.strptime(m.group(1), "%Y%m%d").date()] = name
    return out


def create_partition(cursor, table: str, day: date) -> bool:
    """Create one day's partition. Fails softly (False) if rows for that day already sit
    in the default partition, e.g. after a clock jump; they stay readable there."""
    lo = datetime(day.year, day.month, day.day, tzinfo=UTC)
    hi = lo + timedelta(days=1)
    try:
        with transaction.atomic(using=connection.alias):
            cursor.execute(
                f'CREATE TABLE IF NOT EXISTS "{_name(table, day)}" PARTITION OF "{table}" '
                "FOR VALUES FROM (%s) TO (%s)",
                [lo.isoformat(), hi.isoformat()],
            )
        return True
    except DatabaseError as exc:
        log.warning("cannot create partition %s: %s", _name(table, day), exc)
        return False


def _retention(table: str) -> int:
    return int(getattr(settings, TABLES[table]))


def _ensure_range(cursor, table: str, start: date, today: date, existing) -> int:
    created = 0
    day = start
    while day <= today + timedelta(days=DAYS_AHEAD):
        if day not in existing and create_partition(cursor, table, day):
            created += 1
        day += timedelta(days=1)
    return created


def convert(cursor, table: str, today: date) -> None:
    """Turn an ordinary Django table into a partitioned one, keeping data, defaults,
    foreign keys and index names. Idempotent."""
    if is_partitioned(cursor, table):
        return
    old = f"{table}_unpartitioned"
    cursor.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s AND indexname NOT LIKE %s",
        [table, "%pkey"],
    )
    indexes = cursor.fetchall()
    cursor.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = %s::regclass AND contype = 'f'",
        [table],
    )
    fks = cursor.fetchall()
    cursor.execute("SELECT MIN(created_at)::date, COALESCE(MAX(id), 0) FROM " + f'"{table}"')
    first_day, max_id = cursor.fetchone()

    cursor.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    for name, _ in fks:
        cursor.execute(f'ALTER TABLE "{old}" DROP CONSTRAINT "{name}"')
    cursor.execute(
        f'CREATE TABLE "{table}" (LIKE "{old}" INCLUDING DEFAULTS INCLUDING CONSTRAINTS) '
        "PARTITION BY RANGE (created_at)"
    )
    # Identity columns cannot live on partitioned tables before PostgreSQL 17.
    seq = f"{table}_pid_seq"
    cursor.execute(f'CREATE SEQUENCE "{seq}"')
    cursor.execute(f'ALTER TABLE "{table}" ALTER COLUMN id SET DEFAULT nextval(%s)', [seq])
    cursor.execute(f'ALTER SEQUENCE "{seq}" OWNED BY "{table}".id')
    cursor.execute("SELECT setval(%s, %s, true)", [seq, max(max_id, 1)])
    # The partition key must be part of the primary key.
    cursor.execute(f'ALTER TABLE "{table}" ADD PRIMARY KEY (id, created_at)')

    # Everything inside the retention period gets a daily partition; the default one
    # only catches rows with timestamps far outside it.
    keep_from = today - timedelta(days=_retention(table))
    start = min(first_day, keep_from) if first_day else keep_from
    _ensure_range(cursor, table, start, today, {})
    cursor.execute(f'CREATE TABLE "{table}_default" PARTITION OF "{table}" DEFAULT')
    cursor.execute(f'INSERT INTO "{table}" SELECT * FROM "{old}"')
    cursor.execute(f'DROP TABLE "{old}"')
    for name, definition in fks:
        cursor.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" {definition}')
    for _, indexdef in indexes:
        cursor.execute(indexdef)  # same names; the definitions still point at `table`
    log.info("partitioned %s", table)


def maintain(today: date | None = None) -> dict[str, dict[str, int]]:
    """Create upcoming partitions and drop expired ones. Returns what was done."""
    if not enabled():
        return {}
    today = today or datetime.now(UTC).date()
    report = {}
    with connection.cursor() as cursor:
        for table in TABLES:
            if not is_partitioned(cursor, table):
                continue
            existing = partitions(cursor, table)
            keep_from = today - timedelta(days=_retention(table))
            created = _ensure_range(cursor, table, keep_from, today, existing)
            dropped = 0
            for day, name in sorted(existing.items()):
                if day < keep_from:
                    cursor.execute(f'DROP TABLE "{name}"')
                    dropped += 1
            report[table] = {"created": created, "dropped": dropped, "partitions": len(existing)}
    return report
