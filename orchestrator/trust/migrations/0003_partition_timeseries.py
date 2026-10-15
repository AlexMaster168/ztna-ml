from datetime import UTC, datetime

from django.db import migrations


def partition(apps, schema_editor):
    from core import partitions

    if schema_editor.connection.vendor != "postgresql":
        return
    today = datetime.now(UTC).date()
    with schema_editor.connection.cursor() as cursor:
        for table in partitions.TABLES:
            partitions.convert(cursor, table, today)


class Migration(migrations.Migration):
    dependencies = [("trust", "0002_enforcementsettings_incident_below")]

    operations = [migrations.RunPython(partition, migrations.RunPython.noop)]
