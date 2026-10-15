import json
from datetime import timedelta

import redis
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsAnalyst, IsViewer
from audit.services import record
from nodes.models import Peer

from .models import AnomalyEvent, EnforcementSettings, Incident, IncidentStatus, TrustScoreSnapshot


class AnomalyEventSerializer(serializers.ModelSerializer):
    peer_name = serializers.CharField(source="peer.name", default=None, read_only=True)

    class Meta:
        model = AnomalyEvent
        fields = [
            "id",
            "peer",
            "peer_name",
            "peer_ip",
            "source",
            "kind",
            "penalty",
            "details",
            "window_start",
            "created_at",
            "incident",
        ]


class IncidentSerializer(serializers.ModelSerializer):
    peer_name = serializers.CharField(source="peer.name", read_only=True)
    resolved_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    event_count = serializers.IntegerField(source="events.count", read_only=True)

    class Meta:
        model = Incident
        fields = [
            "id",
            "peer",
            "peer_name",
            "status",
            "severity",
            "title",
            "min_trust_score",
            "actions",
            "opened_at",
            "updated_at",
            "resolved_at",
            "resolved_by",
            "resolution_note",
            "event_count",
        ]
        read_only_fields = fields


class IncidentDetailSerializer(IncidentSerializer):
    events = AnomalyEventSerializer(many=True, read_only=True)

    class Meta(IncidentSerializer.Meta):
        fields = [*IncidentSerializer.Meta.fields, "events"]
        read_only_fields = fields


class IncidentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Incident.objects.select_related("peer", "resolved_by")

    def get_serializer_class(self):
        return IncidentDetailSerializer if self.action == "retrieve" else IncidentSerializer

    def get_permissions(self):
        return [IsAnalyst() if self.action in ("acknowledge", "resolve") else IsViewer()]

    def get_queryset(self):
        qs = super().get_queryset()
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status__in=s.split(","))
        if peer := self.request.query_params.get("peer"):
            qs = qs.filter(peer_id=peer)
        return qs

    def _move(self, request, to: IncidentStatus, allowed: set[IncidentStatus]):
        inc = self.get_object()
        if inc.status not in allowed:
            raise ValidationError(
                {"detail": _("incident is already %(status)s") % {"status": inc.status}}
            )
        inc.status = to
        if to == IncidentStatus.RESOLVED:
            inc.resolved_at = timezone.now()
            inc.resolved_by = request.user
            inc.resolution_note = str(request.data.get("note", ""))[:2000]
        inc.save()
        record(request.user, f"incident.{to}", inc, {"note": inc.resolution_note}, request)
        return Response(IncidentSerializer(inc).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        return self._move(request, IncidentStatus.ACKNOWLEDGED, {IncidentStatus.OPEN})

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        return self._move(
            request,
            IncidentStatus.RESOLVED,
            {IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED},
        )


class EventList(APIView):
    permission_classes = [IsViewer]

    def get(self, request):
        qs = AnomalyEvent.objects.select_related("peer")
        if peer := request.query_params.get("peer"):
            qs = qs.filter(peer_id=peer)
        limit = min(int(request.query_params.get("limit", 100)), 500)
        return Response(AnomalyEventSerializer(qs[:limit], many=True).data)


class PeerScores(APIView):
    permission_classes = [IsViewer]

    def get(self, request, pk: int):
        minutes = min(int(request.query_params.get("minutes", 60)), 24 * 60)
        since = timezone.now() - timedelta(minutes=minutes)
        rows = (
            TrustScoreSnapshot.objects.filter(peer_id=pk, created_at__gte=since)
            .order_by("created_at")
            .values("score", "created_at")
        )
        return Response(list(rows))


class PeerMetrics(APIView):
    """Hot metrics produced by the ML worker (Redis), see ztna_ml.worker."""

    permission_classes = [IsViewer]

    def get(self, request, pk: int):
        peer = Peer.objects.select_related("gateway").get(pk=pk)
        r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        key = f"{peer.gateway.name}:{peer.wg_ip}"
        history = [json.loads(x) for x in r.lrange(f"metrics:{key}:history", 0, 119)]
        recent = [json.loads(x) for x in r.lrange(f"recent:{key}", 0, 49)]
        return Response({"history": history[::-1], "recent_requests": recent})


class EnforcementSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnforcementSettings
        exclude = ["id"]
        read_only_fields = ["updated_at"]

    def validate(self, attrs):
        merged = {**self.to_internal_value(self.to_representation(self.instance)), **attrs}
        if not merged["revoke_below"] <= merged["quarantine_below"] < merged["release_above"]:
            raise ValidationError(_("require revoke_below <= quarantine_below < release_above"))
        if merged["incident_below"] < merged["quarantine_below"]:
            raise ValidationError(_("incident_below must be >= quarantine_below"))
        return attrs


class EnforcementSettingsView(APIView):
    def get_permissions(self):
        return [IsAdmin() if self.request.method in ("PUT", "PATCH") else IsViewer()]

    def get(self, request):
        return Response(EnforcementSettingsSerializer(EnforcementSettings.get()).data)

    def patch(self, request):
        obj = EnforcementSettings.get()
        ser = EnforcementSettingsSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        record(request.user, "settings.enforcement", obj, request.data, request)
        return Response(ser.data)

    put = patch
