#!/usr/bin/env bash
set -euo pipefail

echo "[1/5] Repairing root password and SSH login policy..."
echo 'root:x4Y7SEVK8WEY' | chpasswd

mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ -f /root/.ssh/authorized_keys ]; then
  cp -a /root/.ssh/authorized_keys "/root/.ssh/authorized_keys.bak.$(date +%Y%m%d-%H%M%S)"
  grep -E '^(ssh-rsa|ssh-ed25519|ecdsa-sha2-|sk-)' /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.clean || true
  mv /root/.ssh/authorized_keys.clean /root/.ssh/authorized_keys
fi
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

sshd_config="/etc/ssh/sshd_config"
cp -a "${sshd_config}" "${sshd_config}.bak.$(date +%Y%m%d-%H%M%S)"
set_sshd_option() {
  local key="$1"
  local value="$2"
  if grep -qiE "^[#[:space:]]*${key}[[:space:]]+" "${sshd_config}"; then
    sed -i -E "s|^[#[:space:]]*${key}[[:space:]]+.*|${key} ${value}|I" "${sshd_config}"
  else
    printf '\n%s %s\n' "${key}" "${value}" >> "${sshd_config}"
  fi
}

set_sshd_option Port 22
set_sshd_option PermitRootLogin yes
set_sshd_option PasswordAuthentication yes
set_sshd_option PubkeyAuthentication yes
set_sshd_option KbdInteractiveAuthentication yes
set_sshd_option UsePAM yes
set_sshd_option MaxStartups "50:30:100"

echo "[2/5] Opening SSH and proxy ports in firewall..."
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
  ufw allow 443/udp || true
  ufw allow 40000:50000/tcp || true
  ufw allow 40000:50000/udp || true
  ufw --force enable || true
fi

iptables -I INPUT -p tcp --dport 22 -j ACCEPT 2>/dev/null || true

echo "[3/5] Restarting SSH service..."
sshd -t
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || service ssh restart

echo "[4/5] Publishing corrected Clash configs..."
pid="$(ss -ltnp 2>/dev/null | sed -n 's/.*:49999 .*pid=\([0-9]\+\).*/\1/p' | head -n 1)"
if [ -n "${pid}" ] && [ -d "/proc/${pid}/cwd" ]; then
  pubdir="$(readlink -f "/proc/${pid}/cwd")"
else
  pubdir="/opt/bwg-clash"
  mkdir -p "${pubdir}"
  nohup python3 -m http.server 49999 --directory "${pubdir}" >/var/log/bwg-clash-http.log 2>&1 &
fi
echo "Publishing to ${pubdir}"

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

echo "[5/5] Status:"
ss -ltnp | grep -E ':(22|80|443|49999|40000|40001)\b' || true
systemctl is-active ssh 2>/dev/null || systemctl is-active sshd 2>/dev/null || true
echo "Root password is now: x4Y7SEVK8WEY"
echo "PC link:     http://45.78.78.128/us1.yaml"
echo "Mobile link: http://45.78.78.128/mobile-ss.yaml"
echo "Done."
