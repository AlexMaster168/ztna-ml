from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsAnalyst
from audit.services import record

from . import services
from .models import Delivery, Event, NotificationChannel


class ChannelSerializer(serializers.ModelSerializer):
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=Event.choices), required=False
    )
    has_secret = serializers.SerializerMethodField()

    class Meta:
        model = NotificationChannel
        fields = [
            "id",
            "name",
            "kind",
            "url",
            "secret",
            "has_secret",
            "telegram_chat_id",
            "events",
            "min_severity",
            "enabled",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "has_secret"]
        extra_kwargs = {"secret": {"write_only": True, "required": False}}

    def get_has_secret(self, ch) -> bool:
        return bool(ch.secret)

    def validate(self, attrs):
        def current(name, default=""):
            return attrs.get(name, getattr(self.instance, name, default))

        kind = current("kind", None)
        if kind == "telegram" and not (current("secret") and current("telegram_chat_id")):
            raise serializers.ValidationError(_("telegram needs a bot token and a chat id"))
        if kind in ("webhook", "slack") and not current("url"):
            raise serializers.ValidationError(_("url is required"))
        return attrs


class ChannelViewSet(viewsets.ModelViewSet):
    queryset = NotificationChannel.objects.all()
    serializer_class = ChannelSerializer
    permission_classes = [IsAdmin]

    def perform_create(self, serializer):
        ch = serializer.save()
        record(self.request.user, "notify.channel_create", ch, {"name": ch.name}, self.request)

    def perform_update(self, serializer):
        ch = serializer.save()
        record(self.request.user, "notify.channel_update", ch, {"name": ch.name}, self.request)

    def perform_destroy(self, instance):
        record(self.request.user, "notify.channel_delete", instance, {"name": instance.name})
        instance.delete()

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        Delivery.objects.create(
            channel=self.get_object(),
            event=Event.TEST,
            severity="low",
            payload={"by": request.user.username},
            next_attempt_at=timezone.now(),
        )
        return Response(status=202)


class DeliverySerializer(serializers.ModelSerializer):
    channel_name = serializers.CharField(source="channel.name", read_only=True)
    text = serializers.SerializerMethodField()

    class Meta:
        model = Delivery
        fields = [
            "id",
            "channel",
            "channel_name",
            "event",
            "severity",
            "text",
            "status",
            "attempts",
            "last_error",
            "created_at",
            "sent_at",
        ]

    def get_text(self, d) -> str:
        return services.render(d.event, d.payload)


class DeliveryList(ListAPIView):
    queryset = Delivery.objects.select_related("channel")
    serializer_class = DeliverySerializer
    permission_classes = [IsAnalyst]
