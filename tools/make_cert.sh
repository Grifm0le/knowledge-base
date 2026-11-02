#!/bin/sh
# Самоподписанный сертификат для проверки на своём компьютере.
# В работе вместо него кладётся сертификат, выданный для настоящего домена.
set -eu

cd "$(dirname "$0")/.."
mkdir -p deploy/certs

openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout deploy/certs/server.key \
    -out deploy/certs/server.crt
chmod 644 deploy/certs/server.crt
chmod 644 deploy/certs/server.key
echo "Сертификат записан в deploy/certs"
