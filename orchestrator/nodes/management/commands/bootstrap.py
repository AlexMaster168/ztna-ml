"""Idempotent first-run setup from environment: admin user and the gateway record."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role
from audit.services import record
from nodes.models import Gateway, hash_token


class Command(BaseCommand):
    help = "Create the admin user and gateway from env vars if they do not exist."

    @transaction.atomic
    def handle(self, *args, **options):
        self._ensure_admin()
        self._ensure_gateway()

    def _ensure_admin(self):
        username = os.environ.get("DJANGO_ADMIN_USERNAME")
        password = os.environ.get("DJANGO_ADMIN_PASSWORD")
        if not (username and password):
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            return
        user = User.objects.create_superuser(username=username, password=password)
        user.role = Role.ADMIN
        user.save(update_fields=["role"])
        record("system", "user.bootstrap_admin", user)
        self.stdout.write(f"created admin {username}")

    def _ensure_gateway(self):
        name = os.environ.get("GATEWAY_NAME")
        token = os.environ.get("GATEWAY_TOKEN")
        if not (name and token):
            return
        gw, created = Gateway.objects.get_or_create(
            name=name,
            defaults={
                "endpoint": os.environ["GATEWAY_ENDPOINT"],
                "wg_subnet": os.environ.get("WG_SUBNET", "10.13.0.0/24"),
                "token_hash": hash_token(token),
            },
        )
        if created:
            record("system", "gateway.bootstrap", gw)
            self.stdout.write(f"created gateway {name}")
        elif gw.token_hash != hash_token(token):
            # Env is the source of truth for the dev/bootstrap token.
            gw.token_hash = hash_token(token)
            gw.save(update_fields=["token_hash"])
            record("system", "gateway.rotate_token", gw)
