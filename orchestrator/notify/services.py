"""Outbound notifications.

enqueue() is called inside the transaction that changes state, so a notification exists
exactly when the change was committed. The notifier process (manage.py notifier) sends
due deliveries and retries failures with backoff.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import override

from .models import Delivery, DeliveryStatus, NotificationChannel

log = logging.getLogger(__name__)

BACKOFF_SECONDS = [10, 30, 120, 600, 1800]
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
TIMEOUT = 10

MESSAGES = {
    "incident.opened": _("Incident #{incident_id}: {kind} from {peer} (trust {score})"),
    "peer.quarantined": _("Peer {peer} quarantined: {reason}"),
    "peer.revoked": _("Peer {peer} revoked: {reason}"),
    "peer.released": _("Peer {peer} released: {reason}"),
    "model.trained": _("Model {version} trained ({trigger}): {result}"),
    "model.promoted": _("Model {version} promoted to active"),
    "test": _("Test notification from the ZTNA control plane"),
}


class _SafeDict(dict):
    def __missing__(self, key):
        return "?"


def render(event: str, data: dict) -> str:
    with override(settings.NOTIFY_LANGUAGE):
        template = str(MESSAGES.get(event, event))
        return "ZTNA: " + template.format_map(_SafeDict(data))


def enqueue(event: str, severity: str, data: dict) -> int:
    """Create deliveries for every channel interested in this event."""
    now = timezone.now()
    rows = [
        Delivery(
            channel=ch,
            event=event,
            severity=severity,
            payload=data,
            next_attempt_at=now,
        )
        for ch in NotificationChannel.objects.filter(enabled=True)
        if ch.wants(event, severity)
    ]
    Delivery.objects.bulk_create(rows)
    return len(rows)


def _post(url: str, body: dict, headers: dict | None = None) -> None:
    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - admin-set URL
        resp.read()


def send(d: Delivery) -> None:
    ch = d.channel
    text = render(d.event, d.payload)
    if ch.kind == "telegram":
        url = f"{settings.TELEGRAM_API_URL.rstrip('/')}/bot{ch.secret}/sendMessage"
        _post(url, {"chat_id": ch.telegram_chat_id, "text": text})
    elif ch.kind == "slack":
        _post(ch.url, {"text": text})
    else:
        body = {
            "event": d.event,
            "severity": d.severity,
            "text": text,
            "data": d.payload,
            "delivery_id": d.pk,
            "created_at": d.created_at.isoformat(),
        }
        headers = {}
        if ch.secret:
            raw = json.dumps(body).encode()
            sig = hmac.new(ch.secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-ZTNA-Signature"] = f"sha256={sig}"
            # _post re-serialises identically (same dict, same json.dumps defaults)
        _post(ch.url, body, headers)


def process_due(limit: int = 10) -> int:
    """Send deliveries that are due. Safe to run in several processes."""
    done = 0
    with transaction.atomic():
        due = list(
            Delivery.objects.select_for_update(skip_locked=True)
            .select_related("channel")
            .filter(status=DeliveryStatus.PENDING, next_attempt_at__lte=timezone.now())
            .order_by("next_attempt_at")[:limit]
        )
        for d in due:
            d.attempts += 1
            try:
                send(d)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                d.last_error = str(exc)[:500]
                if d.attempts >= MAX_ATTEMPTS:
                    d.status = DeliveryStatus.FAILED
                    log.warning("delivery %s failed permanently: %s", d.pk, exc)
                else:
                    delay = BACKOFF_SECONDS[d.attempts - 1]
                    d.next_attempt_at = timezone.now() + timedelta(seconds=delay)
            else:
                d.status = DeliveryStatus.SENT
                d.sent_at = timezone.now()
                d.last_error = ""
                done += 1
            d.save(update_fields=["attempts", "status", "next_attempt_at", "last_error", "sent_at"])
    return done
