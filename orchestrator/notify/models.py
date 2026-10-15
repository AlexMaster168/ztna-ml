from django.core.validators import RegexValidator
from django.db import models

URL_RE = r"^https?://[^\s]+$"


class ChannelKind(models.TextChoices):
    WEBHOOK = "webhook", "Webhook (JSON)"
    SLACK = "slack", "Slack incoming webhook"
    TELEGRAM = "telegram", "Telegram bot"


class Event(models.TextChoices):
    INCIDENT_OPENED = "incident.opened", "Incident opened"
    PEER_QUARANTINED = "peer.quarantined", "Peer quarantined"
    PEER_REVOKED = "peer.revoked", "Peer revoked"
    PEER_RELEASED = "peer.released", "Peer released"
    MODEL_TRAINED = "model.trained", "Model trained"
    MODEL_PROMOTED = "model.promoted", "Model promoted"
    TEST = "test", "Test message"


SEVERITIES = ["low", "medium", "high", "critical"]


class NotificationChannel(models.Model):
    name = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=ChannelKind.choices)
    # webhook / slack target; for telegram the bot API is used instead.
    url = models.CharField(max_length=500, blank=True, validators=[RegexValidator(URL_RE)])
    # webhook: HMAC-SHA256 signing key; telegram: bot token. Never returned by the API.
    secret = models.CharField(max_length=255, blank=True)
    telegram_chat_id = models.CharField(max_length=64, blank=True)
    events = models.JSONField(default=list, help_text="event types; empty = all")
    min_severity = models.CharField(
        max_length=16, default="medium", choices=[(s, s) for s in SEVERITIES]
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"

    def wants(self, event: str, severity: str) -> bool:
        if not self.enabled:
            return False
        if event == Event.TEST:
            return True
        if self.events and event not in self.events:
            return False
        return SEVERITIES.index(severity) >= SEVERITIES.index(self.min_severity)


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class Delivery(models.Model):
    channel = models.ForeignKey(
        NotificationChannel, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=32)
    severity = models.CharField(max_length=16)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self) -> str:
        return f"{self.event} -> {self.channel_id} ({self.status})"
