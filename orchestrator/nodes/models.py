import hashlib
import ipaddress
import secrets

from django.conf import settings
from django.db import models


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class Gateway(models.Model):
    name = models.SlugField(unique=True)
    # Reported by gw-agent; the private key never leaves the gateway.
    public_key = models.CharField(max_length=44, blank=True)
    endpoint = models.CharField(max_length=255, help_text="host:port peers connect to")
    wg_subnet = models.CharField(max_length=43, default="10.13.0.0/24")
    token_hash = models.CharField(max_length=64, unique=True)
    # Bumped on every change to the desired state; gw-agent reports what it applied.
    revision = models.PositiveBigIntegerField(default=1)
    applied_revision = models.PositiveBigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(self.wg_subnet)

    @property
    def wg_ip(self) -> str:
        return str(next(self.network.hosts()))

    def set_token(self, raw: str | None = None) -> str:
        raw = raw or secrets.token_urlsafe(32)
        self.token_hash = hash_token(raw)
        return raw


class PeerStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    QUARANTINED = "quarantined", "Quarantined"
    REVOKED = "revoked", "Revoked"


class Peer(models.Model):
    gateway = models.ForeignKey(Gateway, on_delete=models.PROTECT, related_name="peers")
    name = models.CharField(max_length=64)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    public_key = models.CharField(max_length=44, unique=True)
    wg_ip = models.GenericIPAddressField(protocol="IPv4")
    status = models.CharField(max_length=16, choices=PeerStatus.choices, default=PeerStatus.ACTIVE)
    status_reason = models.CharField(max_length=255, blank=True)
    trust_score = models.FloatField(default=100.0)
    # Allowlisted peers are scored but never isolated automatically.
    allowlisted = models.BooleanField(default=False)
    quarantined_at = models.DateTimeField(null=True, blank=True)
    # Only system-made quarantines are lifted automatically; human decisions stick.
    quarantined_by_system = models.BooleanField(default=False)
    # Anomalies before this moment are forgiven (set when a human releases the peer).
    trust_reset_at = models.DateTimeField(null=True, blank=True)
    posture = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Telemetry reported by gw-agent.
    last_handshake_at = models.DateTimeField(null=True, blank=True)
    rx_bytes = models.PositiveBigIntegerField(default=0)
    tx_bytes = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["gateway", "name"], name="uniq_peer_name_per_gateway"),
            models.UniqueConstraint(
                fields=["gateway", "wg_ip"],
                condition=~models.Q(status="revoked"),
                name="uniq_live_peer_ip_per_gateway",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.wg_ip})"
