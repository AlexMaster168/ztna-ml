"""Single sign-on: exchange an OpenID Connect ID token for this API's own JWTs.

The dashboard (BFF) runs the authorization-code + PKCE flow with the identity provider
and posts the resulting ID token here. Nothing from the BFF is trusted: the token's
signature is checked against the provider's JWKS, along with issuer, audience, expiry
and the nonce the BFF bound to the login. The ZTNA role comes from a claim mapped by
the provider (realm roles ztna-admin / ztna-analyst / ztna-viewer).
"""

from __future__ import annotations

import logging
from functools import lru_cache

import jwt
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from .models import ROLE_RANK, Role, User

log = logging.getLogger(__name__)


class OIDCError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.OIDC_ISSUER and settings.OIDC_CLIENT_ID and settings.OIDC_JWKS_URL)


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.OIDC_JWKS_URL, cache_keys=True, lifespan=3600)


def signing_key(token: str):
    return _jwks().get_signing_key_from_jwt(token).key


def verify(id_token: str, nonce: str) -> dict:
    if not enabled():
        raise OIDCError(_("single sign-on is not configured"))
    try:
        claims = jwt.decode(
            id_token,
            signing_key(id_token),
            algorithms=["RS256", "ES256"],
            audience=settings.OIDC_CLIENT_ID,
            issuer=settings.OIDC_ISSUER,
            options={"require": ["exp", "iat", "sub", "nonce"]},
            leeway=30,
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(_("invalid id token: %(error)s") % {"error": exc}) from exc
    if not nonce or claims.get("nonce") != nonce:
        raise OIDCError(_("nonce mismatch"))
    return claims


def role_from_claims(claims: dict) -> Role | None:
    values = claims.get(settings.OIDC_ROLE_CLAIM) or []
    if isinstance(values, str):
        values = [values]
    mapped = [settings.OIDC_ROLE_MAP[v] for v in values if v in settings.OIDC_ROLE_MAP]
    if not mapped:
        return None
    return max((Role(r) for r in mapped), key=lambda r: ROLE_RANK[r])


@transaction.atomic
def user_for(claims: dict) -> User:
    """Find or provision the local account for an SSO identity; the role is refreshed
    from the provider on every login."""
    role = role_from_claims(claims)
    if role is None:
        raise OIDCError(_("the identity provider grants no ZTNA role to this user"))
    sub = claims["sub"]
    user = User.objects.filter(sso_subject=sub).first()
    if user is None:
        name = (claims.get("preferred_username") or sub)[:140]
        if User.objects.filter(username=name).exists():  # a local account owns the name
            name = f"{name}@sso"
        user = User(username=name, sso_subject=sub)
        user.set_unusable_password()
    user.role = role
    user.email = claims.get("email", user.email or "")
    user.first_name = (claims.get("given_name") or "")[:150]
    user.last_name = (claims.get("family_name") or "")[:150]
    user.save()
    return user
