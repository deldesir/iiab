#!/bin/bash
# bench-smoke.sh — one-screen health check of a Frappe bench box.
# Companion to scripts/bench-upgrade.sh: run it before an upgrade to record the
# starting point and after every upgrade to confirm the box came back.
#
#   bench-smoke.sh <domain> [since 'YYYY-MM-DD HH:MM:SS'] [--local]
#
# Runs over `ssh root@<domain>` (key-only fleet), or on this box with --local.
# Prints: the app HEADs, the state of the three bench targets, the failed-unit
# count, the Error Log rows created since <since> (site-local time; default =
# start of today) and the HTTP codes of the login page, ping, POS and the
# storefront. Exits non-zero if a target is inactive, a unit has failed, or any
# HTTP code is not 200 — so it can gate the next box in a fleet loop.
set -uo pipefail

[ $# -ge 1 ] || { sed -n '2,13p' "$0" >&2; exit 2; }
H=$1; shift
SINCE=""; LOCAL=0
while [ $# -gt 0 ]; do
  case $1 in
    --local) LOCAL=1 ;;
    *) SINCE=$1 ;;
  esac; shift
done
B=/home/frappe/frappe-bench
SITE=site.local
APPS="frappe erpnext hrms crm builder pos_next webshop payments"
TARGETS="frappe-bench-frappe-schedule frappe-bench-web.target frappe-bench-workers.target"

run() { if [ "$LOCAL" = 1 ]; then bash -c "$1"; else ssh -o ConnectTimeout=10 -o BatchMode=yes "root@$H" "$1"; fi; }
rc=0

echo "== $H $(date +%F\ %T)"
# shellcheck disable=SC2016
run "cd $B && for a in $APPS; do printf '%s=%s ' \$a \$(sudo -u frappe git -C apps/\$a rev-parse --short HEAD 2>/dev/null || echo '?'); done; echo"

states=$(run "systemctl is-active $TARGETS" | tr '\n' ' ')
echo "targets: $states"
for s in $states; do [ "$s" = active ] || rc=1; done

failed=$(run "systemctl --failed --no-legend | wc -l")
echo "failed-units=$failed"
[ "${failed:-1}" = 0 ] || rc=1

# Error Log rows since SINCE, site-local time. The db name comes from the site
# config; mysql runs as root with the socket auth the bench boxes use.
[ -n "$SINCE" ] || SINCE=$(run "date +%F")' 00:00:00'
run "db=\$(python3 -c \"import json;print(json.load(open('$B/sites/$SITE/site_config.json'))['db_name'])\"); mysql -N -e \"select concat('errorlog-rows-since ${SINCE}=',count(*)) from \\\`tabError Log\\\` where creation > '$SINCE'\" \$db 2>/dev/null || echo 'errorlog-rows=? (mysql unavailable)'"

for p in /erp/login /erp/api/method/ping /erp/pos /; do
  code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' -L "https://$H$p" || echo 000)
  printf '%s=%s ' "$p" "$code"
  [ "$code" = 200 ] || rc=1
done
echo
[ $rc = 0 ] && echo "SMOKE-OK $H" || echo "SMOKE-FAILED $H"
exit $rc
