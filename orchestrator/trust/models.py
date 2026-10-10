from django.conf import settings
from django.db import models

from nodes.models import Gateway, Peer


class EnforcementMode(models.TextChoices):
    MONITOR = "monitor", "Monitor only"
    AUTO_QUARANTINE = "auto_quarantine", "Auto-quarantine"
    AUTO_REVOKE = "auto_revoke", "Auto-quarantine and revoke"


class EnforcementSettings(models.Model):
    """Singleton (pk=1) controlling what the trust engine may do on its own."""

    mode = models.CharField(
        max_length=32, choices=EnforcementMode.choices, default=EnforcementMode.AUTO_QUARANTINE
    )
    # Detections are always stored; an incident is opened once trust falls below this.
    incident_below = models.FloatField(default=70)
    quarantine_below = models.FloatField(default=40)
    revoke_below = models.FloatField(default=10)
    # Hysteresis: release only well above the quarantine threshold.
    release_above = models.FloatField(default=80)
    auto_release = models.BooleanField(default=True)
    min_quarantine_minutes = models.PositiveIntegerField(default=5)
    # Time constant of the exponential decay of penalties.
    decay_minutes = models.FloatField(default=10)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"enforcement: {self.mode}"

    @classmethod
    def get(cls) -> "EnforcementSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class IncidentStatus(models.TextChoices):
    OPEN = "open", "Open"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"


class Severity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class Incident(models.Model):
    peer = models.ForeignKey(Peer, on_delete=models.CASCADE, related_name="incidents")
    status = models.CharField(
        max_length=16, choices=IncidentStatus.choices, default=IncidentStatus.OPEN, db_index=True
    )
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.LOW)
    title = models.CharField(max_length=255)
    min_trust_score = models.FloatField(default=100)
    # What the engine did automatically: "", "quarantined", "revoked", "released".
    actions = models.JSONField(default=list, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self) -> str:
        return f"#{self.pk} {self.title}"


class AnomalyEvent(models.Model):
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE)
    peer = models.ForeignKey(
        Peer, null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )
    incident = models.ForeignKey(
        Incident, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    peer_ip = models.GenericIPAddressField(protocol="IPv4")
    source = models.CharField(max_length=16)  # rule | model
    kind = models.CharField(max_length=64, db_index=True)
    penalty = models.FloatField()
    details = models.JSONField(default=dict, blank=True)
    window_start = models.DateTimeField()
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["peer", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} from {self.peer_ip} (-{self.penalty})"


class TrustScoreSnapshot(models.Model):
    peer = models.ForeignKey(Peer, on_delete=models.CASCADE, related_name="scores")
    score = models.FloatField()
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["peer", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.peer_id}: {self.score:.0f} @ {self.created_at:%H:%M:%S}"
