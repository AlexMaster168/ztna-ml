"""Dev/simulation only: demo groups, resources with graduated trust requirements and a
notification channel pointing at the local webhook sink."""

from django.core.management.base import BaseCommand
from django.db import transaction

from access.models import PeerGroup, Resource
from access.policy import bump_all_gateways
from nodes.models import Peer
from notify.models import NotificationChannel

UPSTREAM = "http://protected-app:8000"
GROUPS = {
    "staff": ("All employees", None),  # None: every simulated peer
    "engineering": ("Engineers with access to admin tools", ["peer-dev"]),
}
RESOURCES = [
    ("api", "/api/", 30, [], "Internal API"),
    ("files", "/files/", 50, [], "File storage"),
    ("admin", "/admin", 80, ["engineering"], "Admin console"),
]


class Command(BaseCommand):
    help = "Create demo groups/resources if they do not exist."

    @transaction.atomic
    def handle(self, *args, **options):
        changed = False
        for name, (desc, members) in GROUPS.items():
            group, created = PeerGroup.objects.get_or_create(
                name=name, defaults={"description": desc}
            )
            peers = Peer.objects.all() if members is None else Peer.objects.filter(name__in=members)
            missing = peers.exclude(groups=group)
            if created or missing.exists():
                group.peers.add(*missing)
                changed = True
        for name, prefix, min_trust, groups, desc in RESOURCES:
            res, created = Resource.objects.get_or_create(
                name=name,
                defaults={
                    "path_prefix": prefix,
                    "upstream": UPSTREAM,
                    "min_trust": min_trust,
                    "description": desc,
                },
            )
            if created:
                res.groups.set(PeerGroup.objects.filter(name__in=groups))
                changed = True
        NotificationChannel.objects.get_or_create(
            name="soc-webhook",
            defaults={"kind": "webhook", "url": "http://webhook-sink:8080/soc", "secret": "dev"},
        )
        if changed:
            bump_all_gateways()
            self.stdout.write("demo access policy seeded")
