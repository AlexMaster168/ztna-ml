from rest_framework.permissions import BasePermission

from .models import Role


def role_required(role: Role) -> type[BasePermission]:
    class HasRole(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.has_role(role))

    HasRole.__name__ = f"Has{role.label}Role"
    return HasRole


IsViewer = role_required(Role.VIEWER)
IsAnalyst = role_required(Role.ANALYST)
IsAdmin = role_required(Role.ADMIN)
