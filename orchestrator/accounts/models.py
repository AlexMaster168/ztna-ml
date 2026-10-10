from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    ANALYST = "analyst", "Analyst"
    ADMIN = "admin", "Admin"


# Higher rank includes every permission of the lower ones.
ROLE_RANK = {Role.VIEWER: 0, Role.ANALYST: 1, Role.ADMIN: 2}


class User(AbstractUser):
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)

    def has_role(self, role: Role) -> bool:
        if self.is_superuser:
            return True
        return ROLE_RANK[Role(self.role)] >= ROLE_RANK[role]
