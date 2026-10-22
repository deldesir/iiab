#!/bin/bash
# import-control-plane.sh — make this machine the fleet hub from an export bundle.
#
# Prerequisites on this machine: IIAB installed with the erpnext role (a bench at
# /home/frappe/frappe-bench, MariaDB; the IIAB Postgres cluster is installed here if the hub
# preset's postgresql_install did not bring it, since no install stage runs that role), tailscale joined, root
# SSH key that the boxes accept, the fleet passphrase at /etc/iiab/fleet-backup.passphrase,
# and this repository checked out at /opt/iiab/iiab. Run as root:
#   import-control-plane.sh /path/to/control-plane-export-<stamp>.tar.gpg
# Steps: restore /etc/iiab fleet files and /var/lib/fleet; create fleet.local and restore its
# database + files; install the fleet_ops app from the bundle; Semaphore (binary, config, db);
# nginx tailnet listener; units; fleet command copies. Then review /etc/iiab/fleet.conf
# (FLEET_PUBLIC_URL, SEMAPHORE_URL point at THIS machine's tailnet address; KUMA_URL stays
# on the observer) and /etc/iiab/fleet.yml roles (this box = hub, the old hub = observer).
set -euo pipefail
BUNDLE=${1:?usage: import-control-plane.sh <bundle.tar.gpg>}
B=/home/frappe/frappe-bench; SITE=fleet.local; PASS=/etc/iiab/fleet-backup.passphrase; R=/opt/iiab/iiab
[ -s "$PASS" ] || { echo "put the fleet passphrase at $PASS first"; exit 2; }
[ -d "$B/apps/erpnext" ] || { echo "no bench with ERPNext at $B (run the erpnext role first)"; exit 2; }
T=$(mktemp -d /var/tmp/cp-import.XXXXXX); trap 'rm -rf "$T"' EXIT
gpg --batch --quiet --passphrase-file "$PASS" -d "$BUNDLE" | tar -C "$T" -xf -
cat "$T/MANIFEST.txt"
echo "=== /etc/iiab + /var/lib/fleet"
install -d -m 0750 /etc/iiab /var/lib/fleet
cp -an "$T/etc/iiab/." /etc/iiab/            # never overwrites this machine's own files
rsync -a "$T/var/lib/fleet/" /var/lib/fleet/
chmod 0640 /etc/iiab/fleet.yml; chmod 0600 /etc/iiab/fleet.conf
TAILIP=$(tailscale ip -4 2>/dev/null || hostname -I | cut -d' ' -f1)
sed -i "s#^FLEET_PUBLIC_URL=.*#FLEET_PUBLIC_URL=http://$TAILIP:8931#; s#^SEMAPHORE_URL=.*#SEMAPHORE_URL=http://$TAILIP:3002#" /etc/iiab/fleet.conf
echo "=== fleet site"
WARN=0; warn() { echo "IMPORT-WARN: $*" >&2; WARN=1; }
bash "$R/scripts/fleet-install/fleet-site-create.sh" || warn "fleet site creation failed"   # creates DB/user/site with a fresh password (kept in fleet.conf)
db=$(ls "$T"/site/*-database.sql.gz | head -1)
gunzip -c "$db" | mysql "$(python3 -c "import json;print(json.load(open('$B/sites/$SITE/site_config.json'))['db_name'])")"
for x in files private-files; do f=$(ls "$T"/site/*-$x.tar 2>/dev/null | head -1); [ -n "$f" ] && tar -xf "$f" -C "$B/sites/$SITE/" && chown -R frappe:frappe "$B/sites/$SITE"; done
python3 - "$T/site/site_config.json" "$B/sites/$SITE/site_config.json" <<'PY'
import json, sys
old, new = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
new["encryption_key"] = old.get("encryption_key", new.get("encryption_key"))   # stored passwords / API secrets stay readable
json.dump(new, open(sys.argv[2], "w"), indent=1)
PY
echo "=== fleet_ops app"
rsync -a --delete "$T/app/fleet_ops/" "$B/apps/fleet_ops/"; chown -R frappe:frappe "$B/apps/fleet_ops"
sudo -u frappe -H bash -lc "cd $B && bench pip install -q -e apps/fleet_ops && (grep -qx fleet_ops sites/apps.txt || { sed -i -e '\$a\\' sites/apps.txt; echo fleet_ops >> sites/apps.txt; }) && bench --site $SITE migrate && bench --site $SITE set-config host_name http://$TAILIP:8931 && bench --site $SITE enable-scheduler && bench --site $SITE clear-cache" >/dev/null
systemctl restart frappe-bench-web.target
echo "=== semaphore"
if [ -f "$T/semaphore/config.json" ]; then
  if ! systemctl is-active -q postgresql-iiab; then
    # local_vars_fleet_hub.yml sets postgresql_install, but upstream's install stages no longer run the
    # postgresql role (only Moodle includes it): run it here so Semaphore has its cluster.
    echo "    postgresql-iiab is not running: running the postgresql role"
    (cd "$R" && ./runrole postgresql) >"$T/postgresql-role.log" 2>&1 || { tail -n 20 "$T/postgresql-role.log"; echo "postgresql role failed"; exit 1; }
    systemctl is-active -q postgresql-iiab || { echo "postgresql-iiab still not running"; exit 1; }
  fi
  bash "$R/scripts/fleet-install/semaphore-install.sh" || warn "console install failed"
  systemctl stop semaphore
  sudo -u postgres psql -c "DROP DATABASE IF EXISTS semaphore" >/dev/null; sudo -u postgres psql -c "CREATE DATABASE semaphore OWNER semaphore" >/dev/null
  sudo -u postgres pg_restore -d semaphore < "$T/semaphore/semaphore.pgdump" || warn "console database restore reported errors"   # via stdin: the postgres user cannot read root's temp dir
  python3 - "$T/semaphore/config.json" /etc/semaphore/config.json "$TAILIP" <<'PY'
import json, sys
old, new = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
for k in ("access_key_encryption", "cookie_hash", "cookie_encryption"):   # the restored rows were encrypted with the old keys
    new[k] = old[k]
new["interface"] = sys.argv[3]; new["web_host"] = f"http://{sys.argv[3]}:3002"
json.dump(new, open(sys.argv[2], "w"), indent=2)
PY
  chown semaphore:semaphore /etc/semaphore/config.json; systemctl start semaphore
fi
echo "=== nginx + units + commands"
[ -f "$T/nginx/fleet-tailnet.conf" ] && sed "s/listen [0-9.]*:8931/listen $TAILIP:8931/" "$T/nginx/fleet-tailnet.conf" > /etc/nginx/sites-enabled/fleet-tailnet.conf && nginx -t && systemctl reload nginx
for u in fleet-check.service fleet-check.timer fleet-digest.service fleet-backup-pull.service fleet-backup-pull.timer fleet-export.service fleet-export.timer; do cp -a "$R/scripts/fleet-units/$u" /etc/systemd/system/; done
systemctl daemon-reload; systemctl enable --now fleet-check.timer fleet-backup-pull.timer fleet-export.timer   # backup-pull needs backup_mail_box in the inventory
install -m 0755 "$R/scripts/fleet" /usr/local/sbin/fleet; install -m 0755 "$R/scripts/fleet-kuma-seed" /usr/local/sbin/fleet-kuma-seed
echo "IMPORT-OK. Now: edit /etc/iiab/fleet.yml (this machine = hub, local: true; the old hub = observer), run 'fleet check --all', then on the old hub disable fleet-check.timer and semaphore."
