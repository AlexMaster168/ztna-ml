#!/usr/bin/env bash
# Simulated end-user device: joins the tunnel with the config issued by the orchestrator.
set -euo pipefail

: "${PEER_NAME:?}"
CONF=/etc/ztna/peers/$PEER_NAME.conf

for _ in $(seq 1 120); do
  [[ -f $CONF ]] && break
  sleep 1
done
[[ -f $CONF ]] || { echo "[peer $PEER_NAME] no config at $CONF" >&2; exit 1; }

install -m 600 "$CONF" /etc/wireguard/wg0.conf
wg-quick down wg0 2>/dev/null || true
wg-quick up wg0
echo "[peer $PEER_NAME] up: $(ip -4 -o addr show wg0 | awk '{print $4}')"

exec "$@"
