from django.conf import settings
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.services import record

from . import oidc


class LoginView(TokenObtainPairView):
    throttle_scope = "login"


class MeView(APIView):
    def get(self, request):
        u = request.user
        return Response(
            {
                "id": u.id,
                "username": u.username,
                "role": "admin" if u.is_superuser else u.role,
                "sso": bool(u.sso_subject),
            }
        )


class OIDCLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(max_length=8192)
    nonce = serializers.CharField(max_length=128)


class OIDCLoginView(APIView):
    """POST {id_token, nonce} -> {access, refresh}. See accounts.oidc."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def get_throttles(self):
        # The status probe runs on every login page render; only attempts are limited.
        return [] if self.request.method == "GET" else super().get_throttles()

    def get(self, request):
        return Response({"enabled": oidc.enabled(), "issuer": settings.OIDC_ISSUER or None})

    def post(self, request):
        data = OIDCLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            claims = oidc.verify(data.validated_data["id_token"], data.validated_data["nonce"])
            user = oidc.user_for(claims)
        except oidc.OIDCError as exc:
            return Response({"detail": str(exc)}, status=403)
        if not user.is_active:
            return Response({"detail": _("account disabled")}, status=403)
        refresh = RefreshToken.for_user(user)
        record(user, "user.sso_login", user, {"role": user.role}, request)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})
