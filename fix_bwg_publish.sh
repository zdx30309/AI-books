#!/usr/bin/env bash
set -euo pipefail

pid="$(ss -ltnp 2>/dev/null | sed -n 's/.*:49999 .*pid=\([0-9]\+\).*/\1/p' | head -n 1)"
if [ -z "${pid}" ] || [ ! -d "/proc/${pid}/cwd" ]; then
  echo "Cannot find the process serving port 49999. Is the static config server running?"
  exit 1
fi

pubdir="$(readlink -f "/proc/${pid}/cwd")"
echo "Publishing Clash configs to: ${pubdir}"

ts="$(date +%Y%m%d-%H%M%S)"
for f in us1.yaml mobile.yaml mobile-ss.yaml; do
  [ -f "${pubdir}/${f}" ] && cp -a "${pubdir}/${f}" "${pubdir}/${f}.bak-${ts}"
done

cat > "${pubdir}/us1.yaml" <<'YAML'
mode: rule
mixed-port: 7890
allow-lan: false
log-level: info
ipv6: true
unified-delay: true

dns:
  enable: true
  listen: 127.0.0.1:1053
  enhanced-mode: fake-ip
  nameserver:
    - https://1.1.1.1/dns-query
    - https://8.8.8.8/dns-query
  fallback:
    - https://dns.google/dns-query
  ipv6: true
  fake-ip-range: 198.18.0.1/16

profile:
  store-selected: true

proxies:
  - name: BWG-HY2-Fast
    type: hysteria2
    server: 45.78.78.128
    port: 40000
    password: YANGye1228
    up: "100 Mbps"
    down: "1000 Mbps"
    bbr-profile: aggressive
    sni: www.bing.com
    alpn:
      - h3
    skip-cert-verify: true
    udp: true

  - name: BWG-Reality-TCP
    type: vless
    server: 45.78.78.128
    port: 40001
    uuid: 20d75ce5-9915-4b47-bd15-fb9cdcc77de3
    network: tcp
    tls: true
    udp: true
    flow: xtls-rprx-vision
    servername: www.microsoft.com
    client-fingerprint: chrome
    reality-opts:
      public-key: q5HWFHhntmD0-qFXaoiot7soL8rfFH29rezZn4deaEw
      short-id: 62fefff2

  - name: BWG-SS-TCP443-Mobile
    type: ss
    server: 45.78.78.128
    port: 443
    cipher: chacha20-ietf-poly1305
    password: REMOVED_PRIVATE_CREDENTIAL
    udp: true

proxy-groups:
  - name: Proxy
    type: select
    proxies:
      - BWG-HY2-Fast
      - BWG-SS-TCP443-Mobile
      - BWG-Reality-TCP
      - DIRECT

rules:
  - IP-CIDR,45.78.78.128/32,DIRECT,no-resolve
  - GEOIP,CN,DIRECT
  - MATCH,Proxy
YAML

cat > "${pubdir}/mobile-ss.yaml" <<'YAML'
mode: rule
mixed-port: 7890
allow-lan: false
log-level: info
ipv6: true
unified-delay: true

proxies:
  - name: BWG-SS-TCP443-Mobile
    type: ss
    server: 45.78.78.128
    port: 443
    cipher: chacha20-ietf-poly1305
    password: REMOVED_PRIVATE_CREDENTIAL
    udp: true

proxy-groups:
  - name: Proxy
    type: select
    proxies:
      - BWG-SS-TCP443-Mobile
      - DIRECT

rules:
  - IP-CIDR,45.78.78.128/32,DIRECT,no-resolve
  - GEOIP,CN,DIRECT
  - MATCH,Proxy
YAML

cp -a "${pubdir}/us1.yaml" "${pubdir}/mobile.yaml"

echo "Done."
echo "PC link:     http://45.78.78.128/us1.yaml"
echo "Mobile link: http://45.78.78.128/mobile-ss.yaml"
