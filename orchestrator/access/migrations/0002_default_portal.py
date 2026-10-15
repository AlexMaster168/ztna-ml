import os

from django.db import migrations


def create_portal(apps, schema_editor):
    # Same behaviour as before resources existed: everything goes to one upstream.
    Resource = apps.get_model("access", "Resource")
    if not Resource.objects.exists():
        Resource.objects.create(
            name="portal",
            description="Internal portal",
            path_prefix="/",
            upstream=os.environ.get("ZTNA_DEFAULT_UPSTREAM", "http://protected-app:8000"),
            min_trust=0,
        )


class Migration(migrations.Migration):
    dependencies = [("access", "0001_initial")]

    operations = [migrations.RunPython(create_portal, migrations.RunPython.noop)]
