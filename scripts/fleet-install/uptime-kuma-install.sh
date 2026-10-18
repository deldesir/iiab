#!/bin/bash
set -euo pipefail
LOG=${FLEET_STATE:-/var/lib/fleet}/uptime-kuma-install.log; exec >>"$LOG" 2>&1
echo "=== $(date +%T) start"
id uptime-kuma >/dev/null 2>&1 || useradd -r -d /opt/uptime-kuma -s /usr/sbin/nologin uptime-kuma
[ -d /opt/uptime-kuma/.git ] || git clone -q --depth 1 -b 2.5.6 https://github.com/louislam/uptime-kuma.git /opt/uptime-kuma
cd /opt/uptime-kuma
echo "=== $(date +%T) npm ci"
npm ci --omit=dev --no-audit --no-fund
echo "=== $(date +%T) download-dist"
npm run download-dist
install -d -o uptime-kuma -g uptime-kuma -m 0750 /library/uptime-kuma
chown -R uptime-kuma:uptime-kuma /opt/uptime-kuma
echo "=== $(date +%T) KUMA-INSTALL-OK"
