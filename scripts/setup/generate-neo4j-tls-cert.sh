#!/usr/bin/env sh
set -eu

TARGET="${1:-data/neo4j-tls/certificates/bolt}"
mkdir -p "$TARGET/trusted" "$TARGET/revoked"
openssl req -x509 -newkey rsa:3072 -nodes -days "${TLS_CERT_DAYS:-30}" \
  -keyout "$TARGET/private.key" -out "$TARGET/public.crt" \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 "$TARGET/private.key"
cp "$TARGET/public.crt" "$TARGET/trusted/public.crt"
echo "Generated non-production TLS material under $TARGET"
