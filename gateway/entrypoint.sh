#!/usr/bin/env bash
# Brings up wg0, loads the ZTNA firewall, runs nginx (the only L7 entry point) and gw-agent.
set -euo pipefail

WG_IF=wg0
WG_ADDRESS=${WG_ADDRESS:-10.13.0.1/24}
WG_LISTEN_PORT=${WG_LISTEN_PORT:-51820}
STATE=/var/lib/ztna/wg

log() { echo "[gateway] $*"; }

mkdir -p "$STATE"
umask 077
[[ -f $STATE/server.key ]] || wg genkey > "$STATE/server.key"
wg pubkey < "$STATE/server.key" > "$STATE/server.pub"
chmod 644 "$STATE/server.pub"

ip link del "$WG_IF" 2>/dev/null || true
ip link add "$WG_IF" type wireguard
wg set "$WG_IF" listen-port "$WG_LISTEN_PORT" private-key "$STATE/server.key"
ip address add "$WG_ADDRESS" dev "$WG_IF"
ip link set "$WG_IF" up
log "wg0 up at $WG_ADDRESS, pubkey $(cat "$STATE/server.pub")"

nft -f /etc/ztna/nftables.nft
log "firewall loaded"

# nginx listens on the tunnel address only. The access policy survives restarts in the
# state volume (fail-static); on first boot it denies everything until gw-agent syncs.
sed -i "s/__WG_IP__/${WG_ADDRESS%/*}/" /etc/nginx/nginx.conf
POLICY=/var/lib/ztna/wg/policy.conf
if [[ ! -f $POLICY ]]; then
  python3 -c 'import sys; sys.path.insert(0, "/opt/gw-agent"); import gw_agent; print(gw_agent.render_policy(None))' > "$POLICY"
fi

# Peers and quarantine are driven by gw-agent from the orchestrator's desired state.
# Run both processes and stop the container if either dies, so the restart policy kicks in.
rotate_logs() {  # keep the shared access log bounded; log-shipper follows the rename
  local f=/var/log/nginx/access.json
  while sleep 60; do
    if [[ $(stat -c %s "$f" 2>/dev/null || echo 0) -gt $((200 * 1024 * 1024)) ]]; then
      mv "$f" "$f.1" && nginx -s reopen && log "rotated access log"
    fi
  done
}
rotate_logs &
python3 /opt/gw-agent/gw_agent.py &
nginx -g 'daemon off;' &
wait -n
log "a gateway process exited; shutting down"
exit 1
