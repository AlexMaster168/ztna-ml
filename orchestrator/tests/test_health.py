from unittest import mock

import pytest


@pytest.mark.django_db
def test_healthz_ok(client):
    with mock.patch("core.views.redis.Redis.from_url") as from_url:
        from_url.return_value.ping.return_value = True
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok", "redis": "ok"}


@pytest.mark.django_db
def test_healthz_degraded_when_redis_down(client):
    with mock.patch("core.views.redis.Redis.from_url") as from_url:
        from_url.return_value.ping.side_effect = ConnectionError
        resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["redis"] == "error: ConnectionError"
