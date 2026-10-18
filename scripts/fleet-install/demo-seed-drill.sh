#!/bin/bash
# demo-seed-drill.sh — prove demo-seed.py on a scratch site of this bench, then remove it.
# Run as root on a machine with the bench: creates demo-drill.local with ERPNext (+ pos_next,
# webshop when present), runs the seed, checks counts, ping and login, drops everything.
# Log: /var/lib/fleet/demo-seed-drill.log. Takes 5–10 minutes (the app installs).
set -uo pipefail
mkdir -p /var/lib/fleet; LOG=/var/lib/fleet/demo-seed-drill.log; exec >>"$LOG" 2>&1
B=/home/frappe/frappe-bench; SITE=demo-drill.local; DB=_demo_drill; SEED=$(dirname "$(readlink -f "$0")")/demo-seed.py
asf() { sudo -u frappe -H bash -lc "cd $B && $*"; }
t0=$(date +%s); step() { printf '\n=== %s (+%ss) %s\n' "$(date +%T)" "$(( $(date +%s) - t0 ))" "$*"; }
PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
step "scratch database + site"
mysql -e "DROP DATABASE IF EXISTS \`$DB\`; CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS '$DB'@'localhost' IDENTIFIED BY '$PW'; ALTER USER '$DB'@'localhost' IDENTIFIED BY '$PW'; GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost'; FLUSH PRIVILEGES;" || { echo "DRILL-FAILED: mysql"; exit 1; }
rm -rf "$B/sites/$SITE"
asf "bench new-site $SITE --admin-password 'drill-initial' --db-name $DB --db-password '$PW' --no-setup-db --force" >/dev/null || { echo "DRILL-FAILED: new-site"; exit 1; }
asf "bench --site $SITE set-config pause_scheduler 1" >/dev/null
apps=${DRILL_APPS:-}; if [ -z "$apps" ]; then apps="erpnext"; for a in pos_next webshop; do grep -qx $a "$B/sites/apps.txt" && apps="$apps $a"; done; fi   # DRILL_APPS="erpnext pos_next" to skip some
for a in $apps; do step "install-app $a"; asf "bench --site $SITE install-app $a" >/dev/null || { echo "DRILL-FAILED: install-app $a"; exit 1; }; done
step "seed"
cp "$SEED" /tmp/demo-seed.py; chmod 644 /tmp/demo-seed.py
asf "bench --site $SITE execute \"exec(open('/tmp/demo-seed.py').read(), {})\"" | tail -3
step "seed again (idempotence)"
asf "bench --site $SITE execute \"exec(open('/tmp/demo-seed.py').read(), {})\"" | tail -1
step "checks"
for dt in Item "Item Price" Customer Supplier "POS Profile" "Website Item" "Stock Ledger Entry"; do
  n=$(asf "bench --site $SITE execute frappe.db.count --args '[\"$dt\"]'" 2>/dev/null | tail -1)
  case "$n" in *"doesn't exist"*|*Error*) n="(doctype not installed here)";; esac
  printf '%-20s %s\n' "$dt" "$n"
done
code=$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H "Host: $SITE" http://127.0.0.1:8000/api/method/ping); echo "ping=$code"
login=$(curl -s -m 60 -H "Host: $SITE" -X POST -d "usr=Administrator" -d "pwd=demo1234" http://127.0.0.1:8000/api/method/login | head -c 80); echo "login Administrator/demo1234: $login"
echo "login demo@example.com/demo1234: $(curl -s -m 60 -H "Host: $SITE" -X POST -d "usr=demo@example.com" -d "pwd=demo1234" http://127.0.0.1:8000/api/method/login | head -c 80)"
step "teardown"
mysql -e "DROP DATABASE IF EXISTS \`$DB\`; DROP USER IF EXISTS '$DB'@'localhost';"
rm -rf "$B/sites/$SITE" /tmp/demo-seed.py
[ "$code" = 200 ] && echo "$login" | grep -q Logged && echo "DEMO-DRILL-OK total $(( $(date +%s) - t0 ))s" || echo "DEMO-DRILL-FAILED ping=$code login=$login"
