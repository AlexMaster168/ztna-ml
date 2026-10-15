from django.db import transaction
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsViewer
from audit.services import record
from nodes.models import Peer

from . import policy
from .models import PeerGroup, Resource


class ResourceSerializer(serializers.ModelSerializer):
    groups = serializers.SlugRelatedField(
        slug_field="name", many=True, queryset=PeerGroup.objects.all(), required=False
    )

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "description",
            "path_prefix",
            "upstream",
            "min_trust",
            "groups",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PeerGroupSerializer(serializers.ModelSerializer):
    peers = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Peer.objects.all(), required=False
    )
    resources = serializers.SlugRelatedField(slug_field="name", many=True, read_only=True)

    class Meta:
        model = PeerGroup
        fields = ["id", "name", "description", "peers", "resources"]
        read_only_fields = ["id", "resources"]


class _AuditedViewSet(viewsets.ModelViewSet):
    """Admin-managed objects that are part of every gateway's desired state."""

    audit_prefix = ""

    def get_permissions(self):
        return [IsViewer() if self.request.method in ("GET", "HEAD", "OPTIONS") else IsAdmin()]

    def _changed(self, action: str, obj, data=None):
        policy.bump_all_gateways()
        record(self.request.user, f"{self.audit_prefix}.{action}", obj, data, self.request)

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        self._changed("create", obj, self.request.data)

    @transaction.atomic
    def perform_update(self, serializer):
        obj = serializer.save()
        self._changed("update", obj, self.request.data)

    @transaction.atomic
    def perform_destroy(self, instance):
        self._changed("delete", instance, {"name": instance.name})
        instance.delete()


class ResourceViewSet(_AuditedViewSet):
    queryset = Resource.objects.prefetch_related("groups")
    serializer_class = ResourceSerializer
    audit_prefix = "resource"


class PeerGroupViewSet(_AuditedViewSet):
    queryset = PeerGroup.objects.prefetch_related("peers", "resources")
    serializer_class = PeerGroupSerializer
    audit_prefix = "group"


class PeerAccess(APIView):
    """What a peer can reach right now and why (decision codes from access.policy)."""

    permission_classes = [IsViewer]

    def get(self, request, pk: int):
        peer = Peer.objects.get(pk=pk)
        return Response(
            {
                "groups": list(peer.groups.values_list("name", flat=True)),
                "resources": policy.peer_access(peer),
            }
        )


class PeerGroupsSerializer(serializers.Serializer):
    groups = serializers.SlugRelatedField(
        slug_field="name", many=True, queryset=PeerGroup.objects.all()
    )


class PeerGroups(APIView):
    """Replace a peer's group memberships."""

    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request, pk: int):
        peer = Peer.objects.get(pk=pk)
        data = PeerGroupsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        groups = data.validated_data["groups"]
        peer.groups.set(groups)
        policy.bump_all_gateways()
        record(request.user, "peer.groups", peer, {"groups": [g.name for g in groups]}, request)
        return Response({"groups": [g.name for g in groups]})
