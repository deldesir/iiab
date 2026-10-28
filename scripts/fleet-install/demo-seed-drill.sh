#!/bin/bash
# demo-seed-drill.sh — prove a demo seed on a scratch site of this bench, then remove it.
# Run as root on a machine with the bench. PROFILE=shop (default) proves demo-seed.py with
# ERPNext (+ pos_next, webshop when present); PROFILE=hotel proves demo-hotel-seed.py with
# ERPNext, HR, POS Next and the PMS. Creates demo-drill.local, runs the seed twice (the second
# run proves it is idempotent), checks counts, ping and login, drops everything.
# Log: /var/lib/fleet/demo-seed-drill.log. Takes 5–15 minutes (the app installs).
# DRILL_APPS="erpnext pos_next" overrides the app list; KEEP=1 leaves the site in place.
set -uo pipefail
mkdir -p /var/lib/fleet; LOG=/var/lib/fleet/demo-seed-drill.log; exec >>"$LOG" 2>&1
B=/home/frappe/frappe-bench; SITE=demo-drill.local; DB=_demo_drill; HERE=$(dirname "$(readlink -f "$0")")
PROFILE=${PROFILE:-shop}
case "$PROFILE" in
  hotel) SEED=$HERE/demo-hotel-seed.py; READY=DEMO-HOTEL-SEED-OK; WANT="erpnext hrms pos_next hospitality_pms"
         COUNTS="Property|Hotel Room|Rate Plan|Guest|Reservation|Stay|Guest Folio|Night Audit|Sales Invoice|Payment Entry|Guest Request|Housekeeping Task|Maintenance Ticket|Room Service Order|Key Card|Guest Registration|Corporate Account|Group Reservation|Channel Reservation|Employee" ;;
  *)     SEED=$HERE/demo-seed.py; READY=DEMO-SEED-OK; WANT="erpnext pos_next webshop"
         COUNTS="Item|Item Price|Customer|Supplier|POS Profile|Website Item|Stock Ledger Entry" ;;
esac
asf() { sudo -u frappe -H bash -lc "cd $B && $*"; }
t0=$(date +%s); step() { printf '\n=== %s (+%ss) %s\n' "$(date +%T)" "$(( $(date +%s) - t0 ))" "$*"; }
PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
step "scratch database + site ($PROFILE)"
mysql <<SQL || { echo "DRILL-FAILED: mysql"; exit 1; }
DROP DATABASE IF EXISTS \`$DB\`; CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS '$DB'@'localhost' IDENTIFIED BY '$PW'; ALTER USER '$DB'@'localhost' IDENTIFIED BY '$PW'; GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost'; FLUSH PRIVILEGES;
SQL
rm -rf "$B/sites/$SITE"
asf "bench new-site $SITE --admin-password 'drill-initial' --db-name $DB --db-password '$PW' --no-setup-db --force" >/dev/null || { echo "DRILL-FAILED: new-site"; exit 1; }
asf "bench --site $SITE set-config pause_scheduler 1" >/dev/null
asf "bench --site $SITE set-config demo 1" >/dev/null   # the seed refuses a site without this mark
apps=${DRILL_APPS:-}; if [ -z "$apps" ]; then for a in $WANT; do grep -qx $a "$B/sites/apps.txt" && apps="$apps $a"; done; fi
for a in $apps; do step "install-app $a"; asf "bench --site $SITE install-app $a" >/dev/null || { echo "DRILL-FAILED: install-app $a"; exit 1; }; done
step "seed"
SD=$(mktemp -d /tmp/demo-seed.XXXXXX); chown root:frappe "$SD"; chmod 0750 "$SD"; trap 'rm -rf "$SD"' EXIT
install -m 0640 -o root -g frappe "$SEED" "$SD/seed.py"
asf "bench --site $SITE execute \"exec(open('$SD/seed.py').read(), {})\"" 2>&1 | tail -15 | tee $SD/seed.out
grep -q "$READY" $SD/seed.out || { echo "DRILL-FAILED: the seed did not finish"; [ -n "${KEEP:-}" ] || { mysql -e "DROP DATABASE IF EXISTS \`$DB\`; DROP USER IF EXISTS '$DB'@'localhost';"; rm -rf "$B/sites/$SITE"; }; true; exit 1; }
first=$(grep "$READY" $SD/seed.out)
step "seed again (idempotence)"
asf "bench --site $SITE execute \"exec(open('$SD/seed.py').read(), {})\"" 2>&1 | tail -3 | tee $SD/seed.out
second=$(grep "$READY" $SD/seed.out)
[ "$first" = "$second" ] && echo "idempotent: same summary twice" || echo "DRILL-WARNING: the second run changed the summary"
step "checks"
echo "$COUNTS" | tr '|' '\n' | while read -r dt; do
  n=$(asf "bench --site $SITE execute frappe.db.count --args '[\"$dt\"]'" 2>/dev/null | tail -1)
  case "$n" in *"doesn't exist"*|*Error*|"") n="(doctype not installed here)";; esac
  printf '%-22s %s\n' "$dt" "$n"
done
code=$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H "Host: $SITE" http://127.0.0.1:8000/api/method/ping); echo "ping=$code"
login=$(curl -s -m 60 -H "Host: $SITE" -X POST -d "usr=Administrator" -d "pwd=demo1234" http://127.0.0.1:8000/api/method/login | head -c 80); echo "login Administrator/demo1234: $login"
echo "login demo@example.com/demo1234: $(curl -s -m 60 -H "Host: $SITE" -X POST -d "usr=demo@example.com" -d "pwd=demo1234" http://127.0.0.1:8000/api/method/login | head -c 80)"
if [ -n "${KEEP:-}" ]; then echo "KEEP set: $SITE left in place"; else
step "teardown"
mysql -e "DROP DATABASE IF EXISTS \`$DB\`; DROP USER IF EXISTS '$DB'@'localhost';"
rm -rf "$B/sites/$SITE"
fi
true
[ "$code" = 200 ] && echo "$login" | grep -q Logged && [ "$first" = "$second" ] && echo "DEMO-DRILL-OK total $(( $(date +%s) - t0 ))s" || echo "DEMO-DRILL-FAILED ping=$code login=$login"
