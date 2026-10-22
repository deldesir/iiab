#!/bin/bash
# demo-rebuild.sh — rebuild a demo box's site from scratch and seed it again: a clean demo
# without rebuilding the VM (for a box that has no snapshot to restore). Run as root on the
# demo box: PROFILE=hotel bash scripts/fleet-install/demo-rebuild.sh (PROFILE=shop by default).
# Drops the site's database, keeps its name, password and host name, reinstalls the apps the
# bench carries, runs the seed, restarts the web workers. About fifteen minutes.
# Log: /var/lib/fleet/demo-rebuild.log. Never run it on a real site.
set -uo pipefail
B=/home/frappe/frappe-bench; SITE=${SITE:-site.local}; LOG=/var/lib/fleet/demo-rebuild.log; mkdir -p /var/lib/fleet; exec >>"$LOG" 2>&1
HERE=$(dirname "$(readlink -f "$0")"); PROFILE=${PROFILE:-shop}
case "$PROFILE" in hotel) SEED=${SEED:-$HERE/demo-hotel-seed.py}; READY=DEMO-HOTEL-SEED-OK ;; *) SEED=${SEED:-$HERE/demo-seed.py}; READY=DEMO-SEED-OK ;; esac
asf() { sudo -u frappe -H bash -lc "cd $B && $*"; }
t0=$(date +%s); step() { printf '\n=== %s (+%ss) %s\n' "$(date +%T)" "$(( $(date +%s) - t0 ))" "$*"; }
CONF=$B/sites/$SITE/site_config.json
[ -f "$CONF" ] || { echo "REBUILD-FAILED: no site $SITE"; exit 1; }
DB=$(python3 -c "import json; print(json.load(open('$CONF'))['db_name'])")
PW=$(python3 -c "import json; print(json.load(open('$CONF'))['db_password'])")
HOST=$(python3 -c "import json; print(json.load(open('$CONF')).get('host_name',''))")
cp -a "$CONF" "/var/lib/fleet/site_config.$SITE.before-rebuild.json"
step "drop and recreate database $DB ($PROFILE)"
mysql <<SQL || { echo "REBUILD-FAILED: mysql"; exit 1; }
DROP DATABASE IF EXISTS \`$DB\`; CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS '$DB'@'localhost' IDENTIFIED BY '$PW'; ALTER USER '$DB'@'localhost' IDENTIFIED BY '$PW'; GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost'; FLUSH PRIVILEGES;
SQL
step "new site"
asf "bench new-site $SITE --admin-password demo1234 --db-name $DB --db-password '$PW' --no-setup-db --force" >/dev/null || { echo "REBUILD-FAILED: new-site"; exit 1; }
[ -n "$HOST" ] && asf "bench --site $SITE set-config host_name '$HOST'" >/dev/null
for a in erpnext payments whatsapp hrms crm builder webshop pos_next hospitality_pms; do
  grep -qx $a "$B/sites/apps.txt" || continue
  step "install-app $a"; asf "bench --site $SITE install-app $a" >/dev/null || { echo "REBUILD-FAILED: install-app $a"; exit 1; }
done
step "seed"
SD=$(mktemp -d /tmp/demo-seed.XXXXXX); chown root:frappe "$SD"; chmod 0750 "$SD"; trap 'rm -rf "$SD"' EXIT
install -m 0640 -o root -g frappe "$SEED" "$SD/seed.py"
asf "bench --site $SITE execute \"exec(open('$SD/seed.py').read(), {})\"" 2>&1 | tail -3 | tee $SD/seed.out
grep -q "$READY" $SD/seed.out || { echo "REBUILD-FAILED: the seed did not finish"; true; exit 1; }
true
step "restart web and scheduler"
systemctl restart frappe-bench-frappe-web.service frappe-bench-node-socketio.service frappe-bench-frappe-schedule.service 2>/dev/null || asf "bench restart"
sleep 6; curl -s -m 20 -o /dev/null -w 'ping %{http_code}\n' http://localhost/erp/api/method/ping
echo "DEMO-REBUILD-OK total $(( $(date +%s) - t0 ))s"
