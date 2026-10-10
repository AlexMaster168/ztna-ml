import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from nodes.keys import generate_keypair
from nodes.models import Gateway

GATEWAY_TOKEN = "gw-test-token"


@pytest.fixture
def gateway(db):
    gw = Gateway(
        name="gw-1",
        endpoint="203.0.113.10:51820",
        wg_subnet="10.13.0.0/29",
        public_key=generate_keypair()[1],
    )
    gw.set_token(GATEWAY_TOKEN)
    gw.save()
    return gw


@pytest.fixture
def make_client(db):
    def _make(role: Role | None) -> APIClient:
        client = APIClient()
        if role is not None:
            user = User.objects.create_user(f"u-{role}", password="x", role=role)
            client.force_authenticate(user)
        return client

    return _make


@pytest.fixture
def agent_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Gateway {GATEWAY_TOKEN}")
    return client
