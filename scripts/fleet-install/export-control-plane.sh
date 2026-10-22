#!/bin/bash
# export-control-plane.sh — bundle everything that makes this machine the fleet hub, encrypted
# with the fleet passphrase, for import on another machine (import-control-plane.sh).
#
# Contents: /etc/iiab fleet files (inventory, config, catalog, releases, local_vars copies,
# backup conf + passphrase files), /var/lib/fleet state (no venv), the fleet site (database +
# files + site_config), the fleet_ops app source (its repository is private), Semaphore config
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
for f in fleet.yml fleet.conf fleet-catalog.yml second-brain-offsite.conf fleet-backup.conf fleet-backup.passphrase fleet-hub-watchdog.conf; do
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
import with: import-control-plane.sh $OUT
M
tar -C "$T" -cf - . | gpg --batch --yes --quiet --symmetric --cipher-algo AES256 --passphrase-file "$PASS" > "$OUT"
chmod 0600 "$OUT"
ls -t /library/backups/control-plane-export-*.tar.gpg 2>/dev/null | tail -n +$(( ${KEEP:-7} + 1 )) | xargs -r rm -f
echo "EXPORT-OK $OUT ($(du -h "$OUT" | cut -f1))"
