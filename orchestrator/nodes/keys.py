"""WireGuard (Curve25519) key helpers."""

import base64
import binascii

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

_RAW = serialization.Encoding.Raw


def generate_keypair() -> tuple[str, str]:
    """Return (private_key, public_key) in WireGuard base64 format."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        _RAW, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    pub_raw = priv.public_key().public_bytes(_RAW, serialization.PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def is_valid_key(value: str) -> bool:
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False
