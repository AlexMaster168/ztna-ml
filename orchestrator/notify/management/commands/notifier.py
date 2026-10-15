"""Sends pending notifications and retries failed ones (see notify.services)."""

import logging
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from notify.services import process_due

log = logging.getLogger("notifier")
HEARTBEAT = Path("/tmp/heartbeat")


class Command(BaseCommand):
    help = "Deliver queued notifications."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="[notifier] %(levelname)s %(message)s")
        while True:
            HEARTBEAT.touch()
            close_old_connections()
            try:
                if sent := process_due():
                    log.info("sent %d notification(s)", sent)
                    continue
            except Exception:
                log.exception("notification pass failed")
            time.sleep(2)
