"""URLconf for the mTLS-only agent listener. Nothing user-facing is routed here."""

from django.urls import path

from core.views import healthz
from nodes.agent_api import DesiredStateView, ReportView

urlpatterns = [
    path("healthz", healthz),
    path("api/agent/state", DesiredStateView.as_view()),
    path("api/agent/report", ReportView.as_view()),
]
