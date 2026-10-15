from django.utils.translation import gettext as _
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsViewer
from audit.services import record

from . import services
from .models import MLOpsSettings, ModelStage, ModelVersion


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
        admin_only = ("deploy", "undeploy", "retrain")
        return [IsAdmin() if self.action in admin_only else IsViewer()]

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

    @action(detail=False, methods=["get"])
    def live(self, request):
        """Champion vs challenger on live traffic, workers, drift and trainer state."""
        return Response(services.live_status())

    @action(detail=False, methods=["post"])
    def retrain(self, request):
        services.request_retrain(request.user, request)
        return Response(status=202)


class MLOpsSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MLOpsSettings
        exclude = ["id"]
        read_only_fields = ["updated_at"]

    def validate_promote_max_rate(self, v):
        if not 0 < v <= 1:
            raise serializers.ValidationError(_("must be in (0, 1]"))
        return v

    def validate_retrain_interval_hours(self, v):
        if v < 1:
            raise serializers.ValidationError(_("must be at least 1 hour"))
        return v


class MLOpsSettingsView(APIView):
    def get_permissions(self):
        return [IsAdmin() if self.request.method in ("PUT", "PATCH") else IsViewer()]

    def get(self, request):
        return Response(MLOpsSettingsSerializer(MLOpsSettings.get()).data)

    def patch(self, request):
        obj = MLOpsSettings.get()
        ser = MLOpsSettingsSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        services.push_trainer_config(obj)
        record(request.user, "settings.mlops", obj, request.data, request)
        return Response(ser.data)

    put = patch
