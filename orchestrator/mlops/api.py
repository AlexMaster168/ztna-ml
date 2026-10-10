from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsViewer

from . import services
from .models import ModelStage, ModelVersion


class ModelVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelVersion
        fields = [
            "id",
            "version",
            "kind",
            "threshold",
            "metrics",
            "trained_at",
            "discovered_at",
            "stage",
            "stage_changed_at",
            "missing",
        ]
        read_only_fields = fields


class ModelVersionDetailSerializer(ModelVersionSerializer):
    class Meta(ModelVersionSerializer.Meta):
        fields = [*ModelVersionSerializer.Meta.fields, "report_md"]
        read_only_fields = fields


class ModelVersionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = ModelVersion.objects.all()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ModelVersionDetailSerializer
        return ModelVersionSerializer

    def get_permissions(self):
        return [IsAdmin() if self.action in ("deploy", "undeploy") else IsViewer()]

    def list(self, request, *args, **kwargs):
        services.sync_models()
        return super().list(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def deploy(self, request, pk=None):
        try:
            stage = ModelStage(request.data.get("stage", ModelStage.SHADOW))
            model = services.deploy(self.get_object(), stage, request.user, request)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(ModelVersionSerializer(model).data)

    @action(detail=False, methods=["post"])
    def undeploy(self, request):
        services.undeploy(request.user, request)
        return Response(status=204)
