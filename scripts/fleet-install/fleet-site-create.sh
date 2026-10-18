#!/bin/bash
# Create the fleet control-plane site (fleet.local) on the hub bench, mirroring the
# erpnext role's recipe (served by a tailnet-only nginx listener, see fleet-units/README.md): DB + user via the MariaDB socket as root, then
# `bench new-site --no-setup-db`, then ERPNext.
set -uo pipefail
LOG=${FLEET_STATE:-/var/lib/fleet}/fleet-site-create.log; exec >>"$LOG" 2>&1
B=/home/frappe/frappe-bench; SITE=fleet.local; DB=_fleet_local; CONF=/etc/iiab/fleet.conf
umask 077
if [ ! -f "$CONF" ]; then
  ADMIN_PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-22)
  DB_PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-28)
  cat > "$CONF" <<CONF
# Fleet control-plane site credentials (hub-private, 0600). Read by \`fleet push\`.
FLEET_SITE_NAME=fleet.local
FLEET_SITE_URL=http://127.0.0.1:8000
FLEET_PUBLIC_URL=http://$(tailscale ip -4 2>/dev/null || hostname -I | cut -d" " -f1):8931
FLEET_ADMIN_PASSWORD=$ADMIN_PW
FLEET_DB_NAME=_fleet_local
FLEET_DB_PASSWORD=$DB_PW
FLEET_API_KEY=
FLEET_API_SECRET=
CONF
  chmod 0600 "$CONF"
fi
. "$CONF"
asf() { sudo -u frappe -H bash -lc "cd $B && $*"; }
echo "=== $(date +%T) database + user"
mysql -e "CREATE DATABASE IF NOT EXISTS \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB'@'localhost' IDENTIFIED BY '$FLEET_DB_PASSWORD';
ALTER USER '$DB'@'localhost' IDENTIFIED BY '$FLEET_DB_PASSWORD';
GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost'; FLUSH PRIVILEGES;" || { echo "SITE-FAILED: mysql"; exit 1; }
echo "=== $(date +%T) new-site"
if [ ! -f "$B/sites/$SITE/site_config.json" ]; then
  asf "bench new-site $SITE --admin-password '$FLEET_ADMIN_PASSWORD' --db-name $DB --db-password '$FLEET_DB_PASSWORD' --no-setup-db --force" || { echo "SITE-FAILED: new-site"; exit 1; }
fi
asf "bench --site $SITE set-config host_name '$FLEET_PUBLIC_URL'"
echo "=== $(date +%T) install erpnext"
asf "bench --site $SITE list-apps" | grep -qx erpnext || asf "bench --site $SITE install-app erpnext" || { echo "SITE-FAILED: install erpnext"; exit 1; }
echo "=== $(date +%T) SITE-OK"
