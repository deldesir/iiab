#!/bin/bash
# Restore drill: bring a customer box's newest hub backup copy up as a scratch site on
# this bench, prove it serves, time every step, then remove it. Nothing here touches the
# source box or the live sites. Prints no secrets.
set -uo pipefail
LOG=${FLEET_STATE:-/var/lib/fleet}/restore-drill-$(date +%F).log; exec >>"$LOG" 2>&1
B=/home/frappe/frappe-bench; SITE=drill.local; DB=_drill_local
SRC_HOST=${1:?usage: restore-drill.sh <box host>}
asf() { sudo -u frappe -H bash -lc "cd $B && $*"; }
t0=$(date +%s); step() { printf '\n=== %s (+%ss) %s\n' "$(date +%T)" "$(( $(date +%s) - t0 ))" "$*"; }
step "source: newest hub copy for $SRC_HOST"
DUMP=$(ls -t /library/backups/fleet/$SRC_HOST/*/*-database*.sql.gz 2>/dev/null | head -1)
[ -n "$DUMP" ] || { echo "DRILL-FAILED: no dump"; exit 1; }
echo "dump: $DUMP ($(du -h "$DUMP" | cut -f1))"
case "$DUMP" in *-enc.sql.gz) echo "DRILL-FAILED: this box's dumps are encrypted with its own key; drill needs the site's backup_encryption_key"; exit 1;; esac
PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24); APW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)
step "scratch database + user"
mysql -e "DROP DATABASE IF EXISTS \`$DB\`; CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS '$DB'@'localhost' IDENTIFIED BY '$PW'; ALTER USER '$DB'@'localhost' IDENTIFIED BY '$PW'; GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost'; FLUSH PRIVILEGES;" || { echo "DRILL-FAILED: mysql"; exit 1; }
step "scratch site (scheduler paused before any data lands)"
rm -rf "$B/sites/$SITE"
asf "bench new-site $SITE --admin-password '$APW' --db-name $DB --db-password '$PW' --no-setup-db --force" >/dev/null || { echo "DRILL-FAILED: new-site"; exit 1; }
asf "bench --site $SITE set-config pause_scheduler 1" >/dev/null
asf "bench --site $SITE set-config maintenance_mode 0" >/dev/null
step "import dump"
gunzip -c "$DUMP" | mysql "$DB" || { echo "DRILL-FAILED: import"; exit 1; }
step "post-restore: admin password, cache, migrate check"
asf "bench --site $SITE set-admin-password '$APW'" >/dev/null
asf "bench --site $SITE clear-cache" >/dev/null
echo "installed apps: $(asf "bench --site $SITE list-apps" | tr '\n' ' ')"
echo "sales invoices in restored db: $(asf "bench --site $SITE execute frappe.db.count --args '[\"Sales Invoice\"]'" | tail -1)"
echo "customers: $(asf "bench --site $SITE execute frappe.db.count --args '[\"Customer\"]'" | tail -1)"
step "serve test (gunicorn, Host header)"
code=$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H "Host: $SITE" http://127.0.0.1:8000/api/method/ping)
echo "ping=$code"
login=$(curl -s -m 60 -H "Host: $SITE" -X POST -d "usr=Administrator" --data-urlencode "pwd=$APW" http://127.0.0.1:8000/api/method/login | head -c 120)
echo "login: $login"
step "teardown"
asf "bench --site $SITE set-config pause_scheduler 1" >/dev/null
mysql -e "DROP DATABASE IF EXISTS \`$DB\`; DROP USER IF EXISTS '$DB'@'localhost';"
rm -rf "$B/sites/$SITE"
step "done"
[ "$code" = 200 ] && echo "DRILL-OK total $(( $(date +%s) - t0 ))s" || echo "DRILL-FAILED: ping $code"
