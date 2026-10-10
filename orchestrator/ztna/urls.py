from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import LoginView, MeView
from audit.views import AuditLogList
from core.views import healthz
from mlops.api import ModelVersionViewSet
from nodes.api import GatewayViewSet, PeerViewSet
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

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("api/auth/token", LoginView.as_view()),
    path("api/auth/refresh", TokenRefreshView.as_view()),
    path("api/me", MeView.as_view()),
    path("api/audit", AuditLogList.as_view()),
    path("api/events", EventList.as_view()),
    path("api/peers/<int:pk>/scores", PeerScores.as_view()),
    path("api/peers/<int:pk>/metrics", PeerMetrics.as_view()),
    path("api/settings/enforcement", EnforcementSettingsView.as_view()),
    path("api/", include(router.urls)),
]
