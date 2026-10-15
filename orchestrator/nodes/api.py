from django.db.models import Count, Q
from django.utils.translation import gettext as _
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsAnalyst, IsViewer
from audit.services import record

from . import services
from .models import Gateway, Peer, PeerStatus


class PeerSerializer(serializers.ModelSerializer):
    gateway = serializers.SlugRelatedField(slug_field="name", read_only=True)
    owner = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Peer
        fields = [
            "id",
            "name",
            "gateway",
            "owner",
            "public_key",
            "wg_ip",
            "status",
            "status_reason",
            "trust_score",
            "allowlisted",
            "quarantined_by_system",
            "created_at",
            "last_handshake_at",
            "rx_bytes",
            "tx_bytes",
        ]
        read_only_fields = fields


class GatewaySerializer(serializers.ModelSerializer):
    in_sync = serializers.SerializerMethodField()
    peer_count = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Gateway
        fields = [
            "id",
            "name",
            "endpoint",
            "wg_subnet",
            "public_key",
            "revision",
            "applied_revision",
            "in_sync",
            "last_seen_at",
            "peer_count",
        ]
        read_only_fields = fields

    def get_in_sync(self, gw: Gateway) -> bool:
        return gw.applied_revision == gw.revision


class GatewayCreateSerializer(serializers.Serializer):
    name = serializers.SlugField(max_length=50)
    endpoint = serializers.RegexField(r"^[A-Za-z0-9.-]+:[0-9]{1,5}$")
    wg_subnet = serializers.CharField(max_length=43)


class GatewayViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Gateway.objects.annotate(
        peer_count=Count("peers", filter=~Q(peers__status=PeerStatus.REVOKED))
    ).order_by("name")
    serializer_class = GatewaySerializer

    def get_permissions(self):
        return [IsViewer() if self.request.method == "GET" else IsAdmin()]

    def create(self, request):
        data = GatewayCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            gw, token = services.register_gateway(
                **data.validated_data, actor=request.user, request=request
            )
        except (services.PeerError, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {"gateway": GatewaySerializer(gw).data, "token": token},
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance):
        try:
            services.delete_gateway(instance, self.request.user, self.request)
        except services.PeerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

    @action(detail=True, methods=["post"], url_path="rotate-token")
    def rotate_token(self, request, pk=None):
        token = services.rotate_gateway_token(self.get_object(), request.user, request)
        return Response({"token": token})


class PeerCreateSerializer(serializers.Serializer):
    name = serializers.RegexField(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    gateway = serializers.SlugRelatedField(
        slug_field="name", queryset=Gateway.objects.all(), required=False
    )
    public_key = serializers.CharField(required=False, allow_blank=True, default="")


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


class PeerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Peer.objects.select_related("gateway", "owner")
    serializer_class = PeerSerializer

    def get_permissions(self):
        perm = {
            "create": IsAdmin,
            "revoke": IsAdmin,
            "rotate_key": IsAdmin,
            "allowlist": IsAdmin,
            "quarantine": IsAnalyst,
            "release": IsAnalyst,
        }.get(self.action, IsViewer)
        return [perm()]

    def get_queryset(self):
        qs = super().get_queryset()
        if status_filter := self.request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if gateway := self.request.query_params.get("gateway"):
            qs = qs.filter(gateway__name=gateway)
        return qs

    def create(self, request):
        data = PeerCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        gateway = data.validated_data.get("gateway") or Gateway.objects.order_by("pk").first()
        if gateway is None:
            raise ValidationError({"gateway": _("no gateway registered")})
        try:
            peer, config = services.create_peer(
                gateway,
                data.validated_data["name"],
                actor=request.user,
                owner=request.user,
                public_key=data.validated_data["public_key"],
                request=request,
            )
        except services.PeerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {"peer": PeerSerializer(peer).data, "client_config": config},
            status=status.HTTP_201_CREATED,
        )

    def _transition(self, request, new_status: PeerStatus):
        reason = ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        try:
            peer = services.set_status(
                self.get_object(),
                new_status,
                request.user,
                reason.validated_data["reason"],
                request,
            )
        except services.PeerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(PeerSerializer(peer).data)

    @action(detail=True, methods=["post"])
    def quarantine(self, request, pk=None):
        return self._transition(request, PeerStatus.QUARANTINED)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        return self._transition(request, PeerStatus.ACTIVE)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        return self._transition(request, PeerStatus.REVOKED)

    @action(detail=True, methods=["post"])
    def allowlist(self, request, pk=None):
        peer = self.get_object()
        peer.allowlisted = serializers.BooleanField().to_internal_value(
            request.data.get("allowlisted", True)
        )
        peer.save(update_fields=["allowlisted"])
        record(request.user, "peer.allowlist", peer, {"allowlisted": peer.allowlisted}, request)
        return Response(PeerSerializer(peer).data)

    @action(detail=True, methods=["post"], url_path="rotate-key")
    def rotate_key(self, request, pk=None):
        try:
            config = services.rotate_key(self.get_object(), request.user, request)
        except services.PeerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response({"client_config": config})
