#!/bin/bash
# bench-rollback.sh — undo the last bench-upgrade on this box, or go to a named release.
#
# Default: read /root/bench-upgrade.last.env (written by bench-upgrade.sh before it changes
# anything): the app commits that ran before the upgrade and the backup it took. Apps go
# back to those commits, requirements and assets are rebuilt for them, and the site's
# database and files are restored from that backup. Everything the upgrade's migrate wrote
# is gone with the database, which is the point: code and data return together.
#
#   MANIFEST=file   go to a release manifest (app=commit) instead of the recorded commits;
#                   the database is still restored from the last pre-upgrade backup unless
#   RESTORE=0       keep the current database and files (code-only rollback)
#   DRY_RUN=1       print the plan and exit (ROLLBACK-PLAN-OK)
#   SITE=...        another site on this bench (tests); LAST_ENV=... another state file
#   KEEP_RUNNING=1  tests only: do not stop/start services and skip the asset build
#
# A fresh backup of the current state is taken first, so a rollback is itself undoable.
# The run ends with ROLLBACK-OK or ROLLBACK-FAILED: <reason> as the last log line.
set -uo pipefail
B=/home/frappe/frappe-bench
SITE=${SITE:-site.local}
LAST_ENV=${LAST_ENV:-/root/bench-upgrade.last.env}
MANIFEST=${MANIFEST:-}; RESTORE=${RESTORE:-1}; DRY_RUN=${DRY_RUN:-0}; KEEP_RUNNING=${KEEP_RUNNING:-0}; ALLOW_DISCARD=${ALLOW_DISCARD:-}
LOG=/root/bench-rollback-$(date +%F).log
[ "$DRY_RUN" = 1 ] || exec >>"$LOG" 2>&1

asf()  { sudo -u frappe -H bash -lc "cd $B && $*"; }
val()  { asf "$@" 2>/dev/null | awk 'NF{l=$0} END{print l}'; }
step() { printf '\n=== %s %s\n' "$(date +%T)" "$*"; }
STOPPED=0
start_services() { systemctl start frappe-bench-redis.target frappe-bench-web.target frappe-bench-workers.target frappe-bench.target; }
fail() { echo "ROLLBACK-FAILED: $*"; [ "$STOPPED" = 1 ] && { echo "restarting services after failure"; start_services; }; exit 1; }
site_conf() { python3 -c "import json,sys; print(json.load(open('$B/sites/$SITE/site_config.json')).get(sys.argv[1], ''))" "$1"; }

APPS=$(tr "\n" " " < $B/sites/apps.txt 2>/dev/null); [ -n "$APPS" ] || APPS="frappe erpnext"
declare -A TARGET
DB_BACKUP=""; FILES_BACKUP=""; PRIVATE_BACKUP=""; PREV_HEADS=""; RELEASE=""

if [ -n "$MANIFEST" ]; then
  [ -r "$MANIFEST" ] || fail "manifest $MANIFEST unreadable"
  RELEASE=$(awk -F= '$1=="release"{print $2}' "$MANIFEST")
  for a in $APPS; do pin=$(awk -F= -v a="$a" '$1==a{print $2}' "$MANIFEST"); [ -n "$pin" ] && TARGET[$a]=$pin; done
  # shellcheck source=/dev/null
  if [ "$RESTORE" = 1 ] && [ -r "$LAST_ENV" ]; then . "$LAST_ENV"; fi
else
  [ -r "$LAST_ENV" ] || fail "no $LAST_ENV on this box (no upgrade recorded here) and no MANIFEST"
  # shellcheck source=/dev/null
  . "$LAST_ENV"
  for kv in $PREV_HEADS; do TARGET[${kv%%=*}]=${kv#*=}; done
fi
[ ${#TARGET[@]} -gt 0 ] || fail "nothing to roll back to"
[ "$RESTORE" = 1 ] && [ -z "$DB_BACKUP" ] && fail "no database backup recorded; use RESTORE=0 for a code-only rollback"

step "plan ($(hostname -f 2>/dev/null || hostname), site $SITE)"
for a in $APPS; do
  cur=$(val "git -C apps/$a rev-parse HEAD")
  if [ -n "${TARGET[$a]:-}" ]; then
    [[ "${TARGET[$a]}" =~ ^[0-9a-f]{40}$ ]] || fail "$a: malformed target commit ${TARGET[$a]}"
    asf "git -C apps/$a cat-file -e ${TARGET[$a]}^{commit}" || fail "$a: commit ${TARGET[$a]:0:10} is not in this clone"
    printf '%-9s %s -> %s%s\n' "$a" "${cur:0:10}" "${TARGET[$a]:0:10}" "$([ "$cur" = "${TARGET[$a]}" ] && echo ' (unchanged)')"
  else
    printf '%-9s %s (not in the target; kept)\n' "$a" "${cur:0:10}"
  fi
done
if [ "$RESTORE" = 1 ]; then
  for f in "$DB_BACKUP" ${FILES_BACKUP:+"$FILES_BACKUP"} ${PRIVATE_BACKUP:+"$PRIVATE_BACKUP"}; do [ -s "$f" ] || fail "backup file missing: $f"; done
  echo "restore: $(basename "$DB_BACKUP")${FILES_BACKUP:+ + $(basename "$FILES_BACKUP")}${PRIVATE_BACKUP:+ + $(basename "$PRIVATE_BACKUP")}"
else
  echo "restore: none (code only)"
fi
[ -n "$RELEASE" ] && echo "release: $RELEASE"
[ "$DRY_RUN" = 1 ] && { echo "ROLLBACK-PLAN-OK"; exit 0; }

step "safety backup of the current state"
asf "bench --site $SITE backup --with-files" | grep -E '\.sql|\.tar' | sed 's/^/  /' || fail "safety backup"

if [ "$KEEP_RUNNING" != 1 ]; then
  step "stop web + workers (redis stays up)"
  systemctl stop frappe-bench-web.target frappe-bench-workers.target
  systemctl stop frappe-bench-frappe-web.service frappe-bench-node-socketio.service 'frappe-bench-frappe-*-worker@*.service' 2>/dev/null
  STOPPED=1
fi

step "apps back to the target commits"
for a in $APPS; do
  [ -n "${TARGET[$a]:-}" ] || continue
  branch=$(val "git -C apps/$a rev-parse --abbrev-ref HEAD"); [ -n "$branch" ] && [ "$branch" != HEAD ] || branch=develop
  # same policy as the upgrade: only disposable drift (generated types, lockfiles) may be discarded
  dirty=$(asf "git -C apps/$a status --porcelain --untracked-files=no" | grep -v -E "^[[:space:]]*\$|yarn\.lock\$|\.d\.ts\$|package-lock\.json\$${ALLOW_DISCARD:+|$ALLOW_DISCARD}" || true)
  [ -z "$dirty" ] || fail "$a has local changes that are not disposable:
$dirty"
  # reset the local branch to the commit; the remote branch stays ahead, so the next
  # upgrade is a fast-forward again
  asf "git -C apps/$a checkout -q -- . && git -C apps/$a checkout -q -B $branch ${TARGET[$a]}" || fail "$a: checkout"
  printf '%-9s -> %s\n' "$a" "$(val "git -C apps/$a log -1 --format='%h %cd' --date=short")"
done

if [ "$KEEP_RUNNING" != 1 ]; then
  step "python + node requirements"
  asf "bench setup requirements" || fail "setup requirements"
  step "build assets"
  asf "bench build" || fail "build"
fi

if [ "$RESTORE" = 1 ]; then
  db=$(site_conf db_name); dbuser=$(site_conf db_user); [ -n "$dbuser" ] || dbuser=$db
  key=$(site_conf backup_encryption_key)
  decrypt() {   # Frappe encrypts backups with gpg symmetric + the site's key; names carry -enc
    if [[ "$1" == *-enc.* ]]; then [ -n "$key" ] || fail "encrypted backup but no backup_encryption_key in site_config"; gpg --batch --yes --quiet --passphrase "$key" --pinentry-mode loopback -d "$1"; else cat "$1"; fi
  }
  step "restore database $db from $(basename "$DB_BACKUP")"
  mysql -e "DROP DATABASE \`$db\`; CREATE DATABASE \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL ON \`$db\`.* TO '$dbuser'@'localhost'; FLUSH PRIVILEGES;" || fail "recreate database"
  decrypt "$DB_BACKUP" | gunzip -c | mysql "$db" || fail "import database"
  for pair in "$FILES_BACKUP:public" "$PRIVATE_BACKUP:private"; do
    f=${pair%%:*}; kind=${pair##*:}; [ -n "$f" ] || continue
    step "restore $kind files from $(basename "$f")"
    rm -rf "$B/sites/$SITE/$kind/files.rollback" && mv "$B/sites/$SITE/$kind/files" "$B/sites/$SITE/$kind/files.rollback" 2>/dev/null
    mkdir -p "$B/sites/$SITE/$kind/files"
    if [[ "$f" == *-enc.* ]]; then decrypt "$f" | tar -xf - -C "$B/sites"; else tar -xf "$f" -C "$B/sites"; fi || fail "extract $kind files"
    rm -rf "$B/sites/$SITE/$kind/files.rollback"
  done
  chown -R frappe:frappe "$B/sites/$SITE"
fi

if [ "$KEEP_RUNNING" != 1 ]; then
  step "start services"
  start_services; STOPPED=0
fi
asf "bench --site $SITE clear-cache >/dev/null && bench --site $SITE clear-website-cache >/dev/null" || true

step "post-state"
for a in $APPS; do printf '%-9s %s\n' "$a" "$(val "git -C apps/$a log -1 --format='%h %cd' --date=short")"; done
step "wait for web"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Host: $SITE" http://127.0.0.1:8000/api/method/ping || true)
  [ "$code" = 200 ] && { echo "ping 200 after ~$((i * 10))s"; break; }
  sleep 10
done
[ "${code:-}" = 200 ] || fail "web did not answer ping within 400s"
echo "ROLLBACK-OK${RELEASE:+ release $RELEASE}"
