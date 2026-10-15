"""Published resources and the trust/group conditions under which peers may reach them.

Resources are global: every gateway publishes all of them. Paths and upstreams are
rendered into the gateways' nginx config, so both are restricted to a safe alphabet.
"""

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from nodes.models import Peer

PATH_PREFIX_RE = r"^/(?:[A-Za-z0-9_.~-]+/)*(?:[A-Za-z0-9_.~-]+)?$"
UPSTREAM_RE = r"^https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?$"


class PeerGroup(models.Model):
    name = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)
    peers = models.ManyToManyField(Peer, related_name="groups", blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Resource(models.Model):
    name = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)
    path_prefix = models.CharField(
        max_length=128,
        unique=True,
        validators=[RegexValidator(PATH_PREFIX_RE, _("invalid path prefix"))],
        help_text='"/" publishes everything not matched by a longer prefix',
    )
    upstream = models.CharField(
        max_length=255,
        validators=[RegexValidator(UPSTREAM_RE, _("upstream must look like http://host:port"))],
    )
    # Peers below this trust score lose access to the resource but keep the rest.
    min_trust = models.FloatField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    # Empty means every peer; otherwise membership in any of the groups is required.
    groups = models.ManyToManyField(PeerGroup, related_name="resources", blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["path_prefix"]

    def __str__(self) -> str:
        return f"{self.name} ({self.path_prefix})"
