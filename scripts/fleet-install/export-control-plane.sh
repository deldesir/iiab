#!/bin/bash
# export-control-plane.sh — bundle everything that makes this machine the fleet hub, encrypted
# with the fleet passphrase, for import on another machine (import-control-plane.sh).
#
# Contents: /etc/iiab fleet files (inventory, config, catalog, releases, local_vars copies,
# backup conf + passphrase files, the lease signing key), /var/lib/fleet state (no venv), the fleet site (database +
# files + site_config), every other site on the hub bench the same way (under erp/<site>/), Snipe-IT's database and
# uploads when roles/snipeit is on the hub, the fleet_ops app source (its repository is private), Semaphore config
# + database dump, the tailnet nginx listener, Uptime Kuma data (only if it moves too).
# Output: /library/backups/control-plane-export-<stamp>.tar.gpg. Prints no secrets.
# Run nightly by fleet-export.timer: the bundle doubles as the hub's own backup, which the observer's
# fleet-backup-mail pulls and mails; the newest KEEP (7) bundles stay under /library/backups.
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S); OUT=${1:-/library/backups/control-plane-export-$STAMP.tar.gpg}
B=/home/frappe/frappe-bench; SITE=fleet.local; PASS=/etc/iiab/fleet-backup.passphrase
[ -s "$PASS" ] || { echo "missing $PASS"; exit 2; }
T=$(mktemp -d /var/tmp/cp-export.XXXXXX); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/etc/iiab" "$T/var/lib" "$T/site" "$T/app" "$T/semaphore" "$T/nginx" "$T/kuma"
for f in fleet.yml fleet.conf fleet-catalog.yml second-brain-offsite.conf fleet-backup.conf fleet-backup.passphrase fleet-hub-watchdog.conf fleet-lease.key fleet-lease.pub; do
  [ -e /etc/iiab/$f ] && cp -a /etc/iiab/$f "$T/etc/iiab/"
done
for d in fleet-releases fleet-vars fleet-backup.passphrase.d; do
  [ -d /etc/iiab/$d ] && cp -a /etc/iiab/$d "$T/etc/iiab/"
done
rsync -a --exclude venv --exclude '*.log' --exclude agent-test /var/lib/fleet/ "$T/var/lib/fleet/"
echo "=== fleet site backup"
sudo -u frappe -H bash -lc "cd $B && bench --site $SITE backup --with-files" >/dev/null
latest=$(ls -t $B/sites/$SITE/private/backups/*-database.sql.gz | head -1); base=${latest%-database.sql.gz}
cp -a "$latest" "$T/site/"; for x in files private-files; do [ -e "$base-$x.tar" ] && cp -a "$base-$x.tar" "$T/site/"; done
cp -a $B/sites/$SITE/site_config.json "$T/site/site_config.json"
# The hub's other sites (its own ERP with the support desk and learning apps, any register the operator
# keeps on this bench): sites nothing else backs up off this machine. Every site on the bench except the
# fleet site, or the space-separated ERP_SITES list when set. One failing site warns; the export goes on.
ERP_SITES=${ERP_SITES:-$(for c in $B/sites/*/site_config.json; do s=$(basename "$(dirname "$c")"); [ -f "$c" ] && [ "$s" != "$SITE" ] && printf '%s ' "$s"; done)}
for ERP_SITE in $ERP_SITES; do
  [ -f $B/sites/$ERP_SITE/site_config.json ] || { echo "WARN: no site $ERP_SITE on this bench, skipped"; continue; }
  echo "=== hub site backup ($ERP_SITE)"; mkdir -p "$T/erp/$ERP_SITE"
  sudo -u frappe -H bash -lc "cd $B && bench --site $ERP_SITE backup --with-files" >/dev/null || { echo "WARN: backup of $ERP_SITE failed, skipped"; continue; }
  latest=$(ls -t $B/sites/$ERP_SITE/private/backups/*-database.sql.gz | head -1); base=${latest%-database.sql.gz}
  cp -a "$latest" "$T/erp/$ERP_SITE/"; for x in files private-files; do [ -e "$base-$x.tar" ] && cp -a "$base-$x.tar" "$T/erp/$ERP_SITE/"; done
  cp -a $B/sites/$ERP_SITE/site_config.json "$T/erp/$ERP_SITE/site_config.json"
done
# Snipe-IT (roles/snipeit) when the hub carries the asset register: database dump, .env, private and public uploads.
if [ -f /opt/iiab/snipe-it/.env ]; then
  echo "=== snipe-it backup"; mkdir -p "$T/snipeit"
  SIT_DB=$(sed -n 's/^DB_DATABASE=//p' /opt/iiab/snipe-it/.env | tr -d "'\"")
  mysqldump --single-transaction "$SIT_DB" | gzip > "$T/snipeit/$SIT_DB.sql.gz" || echo "WARN: snipe-it database dump failed"
  tar -C /opt/iiab/snipe-it -cf "$T/snipeit/files.tar" .env storage/app public/uploads || echo "WARN: snipe-it files incomplete"
fi
echo "=== app source (private repository)"
rsync -a --exclude __pycache__ --exclude .git $B/apps/fleet_ops/ "$T/app/fleet_ops/"
git -C $B/apps/fleet_ops rev-parse HEAD > "$T/app/fleet_ops.commit" 2>/dev/null || true
if [ -f /etc/semaphore/config.json ]; then
  echo "=== semaphore"; cp -a /etc/semaphore/config.json "$T/semaphore/"
  # shellcheck disable=SC2024  # the redirect runs as root on purpose: the postgres user cannot write root's temp dir
  sudo -u postgres pg_dump -Fc semaphore > "$T/semaphore/semaphore.pgdump"
fi
[ -f /etc/nginx/sites-enabled/fleet-tailnet.conf ] && cp -a /etc/nginx/sites-enabled/fleet-tailnet.conf "$T/nginx/"
if [ "${WITH_KUMA:-0}" = 1 ] && [ -d /library/uptime-kuma ]; then
  echo "=== uptime kuma data"; systemctl stop uptime-kuma; cp -a /library/uptime-kuma "$T/kuma/"; systemctl start uptime-kuma
fi
cat > "$T/MANIFEST.txt" <<M
control-plane export from $(hostname -f 2>/dev/null || hostname) at $STAMP
site: $SITE  app commit: $(cat "$T/app/fleet_ops.commit" 2>/dev/null)
snipeit/: Snipe-IT database dump + files.tar (.env, storage/app, public/uploads), when installed; restore = import the dump, untar over the install
erp/<site>/: the hub's other sites ($ERP_SITES) database + files + site_config, restored by hand: bench --site <site> restore erp/<site>/<db>.sql.gz --with-public-files ... --with-private-files ...
import with: import-control-plane.sh $OUT
M
tar -C "$T" -cf - . | gpg --batch --yes --quiet --symmetric --cipher-algo AES256 --passphrase-file "$PASS" > "$OUT"
chmod 0600 "$OUT"
ls -t /library/backups/control-plane-export-*.tar.gpg 2>/dev/null | tail -n +$(( ${KEEP:-7} + 1 )) | xargs -r rm -f
echo "EXPORT-OK $OUT ($(du -h "$OUT" | cut -f1))"
