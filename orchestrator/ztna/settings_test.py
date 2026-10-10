import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://:memory:")

from .settings import *  # noqa: E402,F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
LIVE_EVENTS = False
