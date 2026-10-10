from rest_framework import serializers
from rest_framework.generics import ListAPIView

from accounts.permissions import IsAnalyst

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "actor_label",
            "action",
            "target_type",
            "target_id",
            "data",
            "source_ip",
        ]


class AuditLogList(ListAPIView):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAnalyst]
