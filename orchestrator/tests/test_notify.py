import hashlib
import hmac
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.utils import timezone

from accounts.models import Role
from nodes import services as node_services
from nodes.models import PeerStatus
from notify import services
from notify.models import Delivery, DeliveryStatus, NotificationChannel


@pytest.fixture
def sink():
    received = []
    state = {"fail": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if state["fail"]:
                state["fail"] -= 1
                self.send_response(500)
                self.end_headers()
                return
            received.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", received, state
    srv.shutdown()


def test_channel_filters_by_event_and_severity(db):
    ch = NotificationChannel(kind="webhook", url="http://x", events=["peer.quarantined"])
    assert ch.wants("peer.quarantined", "high")
    assert not ch.wants("incident.opened", "critical")
    assert not ch.wants("peer.quarantined", "low")  # below min_severity "medium"
    assert ch.wants("test", "low")


def test_quarantine_enqueues_and_webhook_is_signed(gateway, sink, settings):
    url, received, _ = sink
    settings.NOTIFY_LANGUAGE = "en"
    NotificationChannel.objects.create(name="hook", kind="webhook", url=url + "/h", secret="k")
    peer, _ = node_services.create_peer(gateway, "laptop", actor="t")
    node_services.set_status(peer, PeerStatus.QUARANTINED, actor="t", reason="brute force")
    assert services.process_due() == 1
    path, headers, body = received[0]
    data = json.loads(body)
    assert data["event"] == "peer.quarantined" and "laptop" in data["text"]
    expected = hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert headers["x-ztna-signature"] == f"sha256={expected}"
    # release is "low": filtered by the default min_severity
    node_services.set_status(peer, PeerStatus.ACTIVE, actor="t")
    assert Delivery.objects.count() == 1


def test_telegram_and_ukrainian_text(gateway, sink, settings):
    url, received, _ = sink
    settings.TELEGRAM_API_URL = url
    NotificationChannel.objects.create(
        name="tg", kind="telegram", secret="TOKEN", telegram_chat_id="42", min_severity="low"
    )
    services.enqueue("peer.revoked", "critical", {"peer": "p1", "reason": "x"})
    services.process_due()
    path, _, body = received[0]
    assert path == "/botTOKEN/sendMessage"
    payload = json.loads(body)
    assert payload["chat_id"] == "42" and "p1" in payload["text"]


def test_retry_with_backoff_then_give_up(db, sink):
    url, received, state = sink
    ch = NotificationChannel.objects.create(name="s", kind="slack", url=url, min_severity="low")
    services.enqueue("test", "low", {})
    state["fail"] = 1
    assert services.process_due() == 0
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.PENDING and d.attempts == 1 and d.last_error
    assert d.next_attempt_at > timezone.now() + timedelta(seconds=5)
    Delivery.objects.update(next_attempt_at=timezone.now())
    assert services.process_due() == 1 and received
    # an endpoint that never works fails permanently after MAX_ATTEMPTS
    ch.url = "http://127.0.0.1:9/nowhere"
    ch.save()
    services.enqueue("test", "low", {})
    for _ in range(services.MAX_ATTEMPTS):
        Delivery.objects.filter(status="pending").update(next_attempt_at=timezone.now())
        services.process_due()
    assert Delivery.objects.filter(status=DeliveryStatus.FAILED).count() == 1


def test_channel_api_hides_secret_and_validates(make_client):
    admin, analyst = make_client(Role.ADMIN), make_client(Role.ANALYST)
    assert analyst.get("/api/notifications/channels/").status_code == 403
    bad = {"name": "tg", "kind": "telegram"}
    assert admin.post("/api/notifications/channels/", bad, format="json").status_code == 400
    ok = {**bad, "secret": "tok", "telegram_chat_id": "1", "events": ["incident.opened"]}
    resp = admin.post("/api/notifications/channels/", ok, format="json")
    assert resp.status_code == 201 and "secret" not in resp.json() and resp.json()["has_secret"]
    cid = resp.json()["id"]
    assert admin.post(f"/api/notifications/channels/{cid}/test/").status_code == 202
    deliveries = analyst.get("/api/notifications/deliveries").json()
    assert deliveries["results"][0]["event"] == "test"
