#!/bin/sh
# Dev-only PKI for orchestrator <-> gw-agent mTLS. Idempotent: keeps existing material.
# In production issue these from your real CA (step-ca, Vault PKI, ...).
set -eu
OUT=${PKI_DIR:-/pki}
SERVER_CN=${SERVER_CN:-orchestrator-agent-api}
CLIENT_CN=${CLIENT_CN:-gw-1}
APP_UID=${APP_UID:-10001}
cd "$OUT"

if [ ! -f ca.crt ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
    -keyout ca.key -out ca.crt -subj "/CN=ztna-dev-ca"
fi

issue() { # name cn ext
  [ -f "$1.crt" ] && return 0
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout "$1.key" -out "$1.csr" -subj "/CN=$2"
  printf '%b' "$3" > "$1.ext"
  openssl x509 -req -in "$1.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days 825 -out "$1.crt" -extfile "$1.ext"
  rm -f "$1.csr" "$1.ext"
}

issue server "$SERVER_CN" "subjectAltName=DNS:$SERVER_CN\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature"
issue "$CLIENT_CN" "$CLIENT_CN" "extendedKeyUsage=clientAuth\nkeyUsage=digitalSignature"

chmod 600 ca.key ./*.key
chmod 644 ./*.crt
chown "$APP_UID" server.key  # gunicorn runs as the app user
echo "pki ready in $OUT"
