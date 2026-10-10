from django.contrib import admin

from .models import Gateway, Peer


@admin.register(Gateway)
class GatewayAdmin(admin.ModelAdmin):
    list_display = ("name", "endpoint", "wg_subnet", "revision", "applied_revision", "last_seen_at")
    exclude = ("token_hash",)


@admin.register(Peer)
class PeerAdmin(admin.ModelAdmin):
    list_display = ("name", "gateway", "wg_ip", "status", "trust_score", "last_handshake_at")
    list_filter = ("status", "gateway")
    # Status changes must go through services (revision bump + audit).
    readonly_fields = ("status", "status_reason", "public_key", "wg_ip")
