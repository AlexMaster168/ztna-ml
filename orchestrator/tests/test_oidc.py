import time
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from accounts import oidc
from accounts.models import Role, User
from audit.models import AuditLog

ISSUER = "http://localhost:8180/realms/ztna"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def oidc_settings(settings):
    from django.core.cache import cache

    cache.clear()  # login throttle state
    settings.OIDC_ISSUER = ISSUER
    settings.OIDC_JWKS_URL = "http://keycloak/jwks"
    settings.OIDC_CLIENT_ID = "ztna-dashboard"
    with mock.patch.object(oidc, "signing_key", return_value=KEY.public_key()):
        yield


def token(key=KEY, **over):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "ztna-dashboard",
        "sub": "sub-1",
        "iat": now,
        "exp": now + 300,
        "nonce": "n1",
        "preferred_username": "olena",
        "email": "olena@example.org",
        "roles": ["ztna-analyst", "offline_access"],
    }
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def login(tok, nonce="n1"):
    return APIClient().post("/api/auth/oidc", {"id_token": tok, "nonce": nonce}, format="json")


def test_sso_login_provisions_user_with_mapped_role(db):
    resp = login(token())
    assert resp.status_code == 200, resp.json()
    user = User.objects.get(sso_subject="sub-1")
    assert user.username == "olena" and user.role == Role.ANALYST
    assert not user.has_usable_password()
    me = APIClient()
    me.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.json()['access']}")
    assert me.get("/api/me").json() == {
        "id": user.pk,
        "username": "olena",
        "role": "analyst",
        "sso": True,
    }
    # the role follows the provider on the next login
    assert login(token(roles=["ztna-viewer", "ztna-admin"])).status_code == 200
    user.refresh_from_db()
    assert user.role == Role.ADMIN
    assert AuditLog.objects.filter(action="user.sso_login").count() == 2


@pytest.mark.parametrize(
    "tok, nonce",
    [
        (token(key=OTHER_KEY), "n1"),  # forged signature
        (token(aud="someone-else"), "n1"),
        (token(iss="http://evil/realms/ztna"), "n1"),
        (token(exp=int(time.time()) - 3600), "n1"),
        (token(), "replayed-nonce"),
        (token(roles=["offline_access"]), "n1"),  # no ZTNA role granted
    ],
)
def test_sso_rejects_bad_tokens(db, tok, nonce):
    assert login(tok, nonce).status_code == 403
    assert not User.objects.filter(sso_subject="sub-1").exists()


def test_sso_does_not_take_over_local_account(db):
    local = User.objects.create_user("olena", password="local-pass", role=Role.ADMIN)
    assert login(token()).status_code == 200
    sso_user = User.objects.get(sso_subject="sub-1")
    assert sso_user.pk != local.pk and sso_user.username == "olena@sso"
    local.refresh_from_db()
    assert local.check_password("local-pass") and local.role == Role.ADMIN


def test_sso_status_endpoint(db, settings):
    assert APIClient().get("/api/auth/oidc").json()["enabled"] is True
    settings.OIDC_ISSUER = ""
    assert APIClient().get("/api/auth/oidc").json()["enabled"] is False
    assert login(token()).status_code == 403
