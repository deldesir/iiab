#!/bin/bash
# Install Semaphore UI natively on the hub: binary, dedicated user, Postgres database on the
# IIAB cluster, config.json (0600), admin user, tailnet-only systemd unit. Prints no secrets.
set -euo pipefail
LOG=${FLEET_STATE:-/var/lib/fleet}/semaphore-install.log; exec >>"$LOG" 2>&1
VER=2.19.16; CONF_DIR=/etc/semaphore; CONF=$CONF_DIR/config.json; FLEET_CONF=/etc/iiab/fleet.conf
TAILIP=$(tailscale ip -4); PORT=3002
echo "=== $(date +%T) binary"
id semaphore >/dev/null 2>&1 || useradd -r -m -d /var/lib/semaphore -s /usr/sbin/nologin semaphore
install -d -o semaphore -g semaphore -m 0750 /var/lib/semaphore /var/lib/semaphore/tmp
if ! /usr/local/bin/semaphore version 2>/dev/null | grep -q "$VER"; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -fsSL -o "$T/s.tgz" "https://github.com/semaphoreui/semaphore/releases/download/v$VER/semaphore_${VER}_linux_amd64.tar.gz"
  tar -xzf "$T/s.tgz" -C "$T" semaphore && install -m 0755 "$T/semaphore" /usr/local/bin/semaphore
fi
/usr/local/bin/semaphore version
echo "=== $(date +%T) database on postgresql-iiab"
# A second run keeps the existing configuration: its encryption keys sign every stored access
# key and session, and its database password must stay what the role has. Only a first run
# generates them.
if [ -f "$CONF" ]; then
  DBPW=$(python3 -c "import json; print(json.load(open('$CONF'))['postgres']['pass'])")
  echo "config exists: keeping keys and the database password"
else
  DBPW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-28)
fi
if ! sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='semaphore'" | grep -q 1; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE semaphore LOGIN PASSWORD '$DBPW'" >/dev/null
else
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE semaphore WITH PASSWORD '$DBPW'" >/dev/null
fi
sudo -u postgres psql -tAc "select 1 from pg_database where datname='semaphore'" | grep -q 1 || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE semaphore OWNER semaphore" >/dev/null
echo "=== $(date +%T) config"
umask 077; install -d -m 0750 -o semaphore -g semaphore $CONF_DIR
ADMPW=$(grep -oE '^SEMAPHORE_ADMIN_PASSWORD=.*' $FLEET_CONF 2>/dev/null | cut -d= -f2- || true)
[ -n "$ADMPW" ] || ADMPW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-22)
k() { head -c 32 /dev/urandom | base64; }
[ -f "$CONF" ] || cat > $CONF <<JSON
{
  "postgres": { "host": "127.0.0.1:5432", "user": "semaphore", "pass": "$DBPW", "name": "semaphore", "options": { "sslmode": "disable" } },
  "dialect": "postgres",
  "port": ":$PORT",
  "interface": "$TAILIP",
  "tmp_path": "/var/lib/semaphore/tmp",
  "web_host": "http://$TAILIP:$PORT",
  "access_key_encryption": "$(k)",
  "cookie_hash": "$(k)",
  "cookie_encryption": "$(k)",
  "schedule_timezone": "America/Port-au-Prince",
  "non_admin_can_create_project": false,
  "max_parallel_tasks": 2
}
JSON
chown semaphore:semaphore $CONF; chmod 0600 $CONF
grep -q "^SEMAPHORE_URL=" $FLEET_CONF 2>/dev/null || printf 'SEMAPHORE_URL=http://%s:%s\nSEMAPHORE_ADMIN_USER=fleet\nSEMAPHORE_ADMIN_PASSWORD=%s\n' "$TAILIP" "$PORT" "$ADMPW" >> $FLEET_CONF
chmod 0600 $FLEET_CONF
echo "=== $(date +%T) migrate + admin"
sudo -u semaphore -H /usr/local/bin/semaphore migrate --config $CONF >/dev/null
sudo -u semaphore -H /usr/local/bin/semaphore user add --admin --login fleet --name "Fleet operator" --email "${FLEET_OPERATOR_EMAIL:-$(grep -oE '^RECIPIENT="?[^"]+' /etc/iiab/fleet-backup.conf 2>/dev/null | cut -d= -f2 | tr -d '"')}" --password "$ADMPW" --config $CONF >/dev/null 2>&1 || echo "admin user exists (kept)"
echo "=== $(date +%T) sudoers + unit"
cat > /etc/sudoers.d/semaphore-fleet <<SUDO
# Semaphore task templates run the fleet command as root, nothing else.
semaphore ALL=(root) NOPASSWD: /usr/local/sbin/fleet, /usr/local/sbin/fleet *, /usr/local/sbin/fleet-kuma-seed, /usr/local/sbin/fleet-kuma-seed *
SUDO
chmod 0440 /etc/sudoers.d/semaphore-fleet; visudo -cf /etc/sudoers.d/semaphore-fleet
cat > /etc/systemd/system/semaphore.service <<UNIT
[Unit]
Description=Semaphore UI (fleet runbooks; tailnet-only bind)
After=network-online.target tailscaled.service postgresql-iiab.service
Wants=network-online.target

[Service]
User=semaphore
Group=semaphore
WorkingDirectory=/var/lib/semaphore
ExecStart=/usr/local/bin/semaphore server --config /etc/semaphore/config.json
Restart=always
RestartSec=10
NoNewPrivileges=false
ProtectSystem=full
ReadWritePaths=/var/lib/semaphore /tmp
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload; systemctl enable -q --now semaphore
for i in $(seq 1 20); do curl -s -o /dev/null -m 3 "http://$TAILIP:$PORT/api/ping" && break; sleep 2; done
echo "ping: $(curl -s -m 5 http://$TAILIP:$PORT/api/ping)"
echo "=== $(date +%T) SEMAPHORE-INSTALL-OK http://$TAILIP:$PORT"
