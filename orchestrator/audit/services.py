from django.db import models

from .models import AuditLog


def record(actor, action: str, target: models.Model, data=None, request=None) -> AuditLog:
    """Write an audit entry. `actor` is a User or a plain label for system actors."""
    is_user = hasattr(actor, "pk") and hasattr(actor, "username")
    return AuditLog.objects.create(
        actor=actor if is_user else None,
        actor_label=actor.username if is_user else str(actor),
        action=action,
        target_type=target._meta.label_lower,
        target_id=str(target.pk),
        data=data or {},
        source_ip=request.META.get("REMOTE_ADDR") if request else None,
    )
