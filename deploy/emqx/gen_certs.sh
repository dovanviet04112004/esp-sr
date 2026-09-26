#!/usr/bin/env bash
# CA and broker certificate for the mqtts listener of prod (KEHOACH 7.5), written to deploy/emqx/certs/.
set -euo pipefail

host="${1:?usage: gen_certs.sh <broker host name or IP>}"
out="$(cd "$(dirname "$0")" && pwd)/certs"
ca_days=3650
broker_days=825

if [[ "$host" =~ ^[0-9.]+$ ]]; then san="IP:$host"; else san="DNS:$host"; fi
mkdir -p "$out"
openssl req -x509 -newkey rsa:2048 -nodes -days "$ca_days" -subj "/CN=esp-sr bench CA" \
  -keyout "$out/ca.key" -out "$out/ca.crt"
openssl req -newkey rsa:2048 -nodes -subj "/CN=$host" -keyout "$out/broker.key" -out "$out/broker.csr"
openssl x509 -req -in "$out/broker.csr" -CA "$out/ca.crt" -CAkey "$out/ca.key" -CAcreateserial \
  -days "$broker_days" -extfile <(printf "subjectAltName=%s" "$san") -out "$out/broker.crt"
rm -f "$out/broker.csr" "$out/ca.srl"
chmod 600 "$out"/*.key
echo "certs for $san in $out; ca.crt goes into the firmware for NET_MQTT_REQUIRE_TLS"
