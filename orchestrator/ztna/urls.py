from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from access.api import PeerAccess, PeerGroups, PeerGroupViewSet, ResourceViewSet
from accounts.views import LoginView, MeView, OIDCLoginView
from audit.views import AuditLogList
from core.views import healthz
from mlops.api import MLOpsSettingsView, ModelVersionViewSet
from nodes.api import GatewayViewSet, PeerViewSet
from notify.api import ChannelViewSet, DeliveryList
from trust.api import (
    EnforcementSettingsView,
    EventList,
    IncidentViewSet,
    PeerMetrics,
    PeerScores,
)

router = DefaultRouter()
router.register("peers", PeerViewSet)
router.register("gateways", GatewayViewSet)
router.register("incidents", IncidentViewSet)
router.register("models", ModelVersionViewSet)
router.register("resources", ResourceViewSet)
router.register("groups", PeerGroupViewSet)
router.register("notifications/channels", ChannelViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("api/auth/token", LoginView.as_view()),
    path("api/auth/refresh", TokenRefreshView.as_view()),
    path("api/auth/oidc", OIDCLoginView.as_view()),
    path("api/me", MeView.as_view()),
    path("api/audit", AuditLogList.as_view()),
    path("api/notifications/deliveries", DeliveryList.as_view()),
    path("api/events", EventList.as_view()),
    path("api/peers/<int:pk>/scores", PeerScores.as_view()),
    path("api/peers/<int:pk>/metrics", PeerMetrics.as_view()),
    path("api/peers/<int:pk>/access", PeerAccess.as_view()),
    path("api/peers/<int:pk>/groups", PeerGroups.as_view()),
    path("api/settings/enforcement", EnforcementSettingsView.as_view()),
    path("api/settings/mlops", MLOpsSettingsView.as_view()),
    path("api/", include(router.urls)),
]
