"""API consumed by gw-agent. Served only by the mTLS listener (ztna.urls_agent)."""

import hmac

from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Gateway, hash_token


class GatewayTokenAuthentication(BaseAuthentication):
    keyword = b"Gateway"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0] != self.keyword:
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("malformed gateway token header")
        digest = hash_token(parts[1].decode())
        gateway = Gateway.objects.filter(token_hash=digest).first()
        if gateway is None or not hmac.compare_digest(gateway.token_hash, digest):
            raise AuthenticationFailed("unknown gateway")
        return AnonymousUser(), gateway

    def authenticate_header(self, request):
        return self.keyword.decode()


class IsGateway(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, Gateway)


class PeerStatsSerializer(serializers.Serializer):
    public_key = serializers.CharField(max_length=44)
    latest_handshake = serializers.IntegerField(min_value=0)
    rx_bytes = serializers.IntegerField(min_value=0)
    tx_bytes = serializers.IntegerField(min_value=0)


class ReportSerializer(serializers.Serializer):
    public_key = serializers.CharField(max_length=44)
    applied_revision = serializers.IntegerField(min_value=0)
    peers = PeerStatsSerializer(many=True)


class AgentView(APIView):
    authentication_classes = [GatewayTokenAuthentication]
    permission_classes = [IsGateway]
    throttle_classes = []


class DesiredStateView(AgentView):
    def get(self, request):
        return Response(services.desired_state(request.auth))


class ReportView(AgentView):
    def post(self, request):
        report = ReportSerializer(data=request.data)
        report.is_valid(raise_exception=True)
        services.apply_report(request.auth, report.validated_data)
        return Response(status=204)
