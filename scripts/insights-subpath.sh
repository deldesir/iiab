#!/bin/bash
# insights-subpath-deploy.sh — move Insights to /erp/insights on one fleet box (iiab repository: scripts/insights-subpath.sh).
# nginx: keep the /erp prefix for /erp/insights + bounce root /insights; bench: insights_path=erp/insights
# (common site config); restart the site processes (they read the path at import); clear-cache.
# Usage: BASE=https://<box host> bash insights-subpath-deploy.sh      (run as root on the box)
set -euo pipefail
B=/home/frappe/frappe-bench; SITE=${SITE:-site.local}; CONF=/etc/nginx/conf.d/erpnext-nginx.conf
STAMP=$(date +%F)
echo "== $(hostname) $(date -Is)"
python3 - "$CONF" "$STAMP" <<'PY'
import sys, pathlib, shutil
conf, stamp = pathlib.Path(sys.argv[1]), sys.argv[2]
s = conf.read_text()
a1 = "    rewrite ^/erp/erp/(.*) /erp/$1 last;\n\n    rewrite ^/erp/(.*) /$1 break;\n"
n1 = """    rewrite ^/erp/erp/(.*) /erp/$1 last;

    # Insights keeps the prefix. The bench is told the app lives at erp/insights (common site
    # config `insights_path`, set by roles/erpnext/tasks/apps.yml), so the site routes it, links it on the
    # apps screen and bases its router there; the request must therefore reach the site
    # with the prefix on. Everything else loses /erp on its way in.
    rewrite ^/erp/(insights(/.*)?)$ /erp/$1 break;
    rewrite ^/erp/(.*) /$1 break;
"""
a2 = "location ^~ /pms/ { return 307 /erp$request_uri; }\n"
n2 = a2 + """# Insights lives at /erp/insights (the /erp/ block above keeps the prefix); a link that still
# names the root path, from a page rendered before the move or typed by hand, lands there.
location = /insights   { return 307 /erp/insights; }
location ^~ /insights/ { return 307 /erp$request_uri; }
"""
if "rewrite ^/erp/(insights(/.*)?)$" in s and "location = /insights" in s:
    print("nginx: already patched"); sys.exit(0)
assert s.count(a1) == 1, f"anchor1 x{s.count(a1)}"
assert s.count(a2) == 1, f"anchor2 x{s.count(a2)}"
bak = conf.with_name(conf.name + f".bak-{stamp}-insights"); shutil.copy2(conf, bak)
conf.write_text(s.replace(a1, n1).replace(a2, n2)); print(f"nginx: patched (backup {bak})")
PY
nginx -t 2>&1 | tail -1 && systemctl reload nginx && echo "nginx: reloaded"
cur=$(python3 -c "import json;print(json.load(open('$B/sites/common_site_config.json')).get('insights_path',''))")
if [ "$cur" = "erp/insights" ]; then echo "bench: insights_path already erp/insights"; else
  sudo -u frappe bash -lc "cd $B && bench set-config -g insights_path erp/insights" >/dev/null && echo "bench: insights_path=erp/insights"
  systemctl try-restart frappe-bench-frappe-web.service frappe-bench-frappe-schedule.service \
    'frappe-bench-frappe-default-worker@*.service' 'frappe-bench-frappe-short-worker@*.service' 'frappe-bench-frappe-long-worker@*.service'
  echo "services: restarted"
fi
for i in $(seq 1 30); do curl -sf -o /dev/null -H "X-Frappe-Site-Name: $SITE" http://127.0.0.1:8000/api/method/ping && break; sleep 1; done
sudo -u frappe bash -lc "cd $B && bench --site $SITE clear-cache" >/dev/null && echo "bench: cache cleared"
BASE=${BASE:-https://$(hostname -f 2>/dev/null || hostname)}
echo "== verify on $BASE"
for p in /erp/insights /erp/insights/workbook /insights /insights/workbook/1 /erp/api/method/ping /erp/ /erp/pos /erp/desk; do printf "  %-28s -> " $p; curl -sk -o /dev/null -w "%{http_code} %{redirect_url}\n" "$BASE$p"; done
printf "  boot insights_path: "; curl -sk "$BASE/erp/insights" | grep -o 'window\["insights_path"\] = "[^"]*"' || echo MISSING
printf "  apps-screen route: "; sudo -u frappe bash -lc "cd $B && bench --site $SITE execute frappe.apps.get_apps" 2>/dev/null | grep -o '"name": "insights"[^}]*"route": "[^"]*"' | grep -o '"route": "[^"]*"' || echo MISSING
systemctl is-active frappe-bench-frappe-web.service frappe-bench-frappe-schedule.service | sort | uniq -c | tr '\n' ' '; echo
