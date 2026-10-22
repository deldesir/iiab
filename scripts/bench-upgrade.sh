#!/bin/bash
# bench-upgrade.sh — bring a Frappe bench to the forks' develop heads.
# Run as root. Log: /root/bench-upgrade-<date>.log
#
#   scp scripts/bench-upgrade.sh root@<box>:/root/ && ssh root@<box> \
#     systemd-run --unit=bench-upgrade --collect --setenv=HOME=/root /bin/bash /root/bench-upgrade.sh
#
# A transient unit survives a dropped SSH session; it needs HOME set explicitly.
#   PREFLIGHT_ONLY=1   run every check that can refuse the upgrade, change nothing
#   ALLOW_DISCARD=ERE  paths whose local changes were verified by hand as disposable
#   FORK_OWNER=name    GitHub owner whose forks the bench tracks (default: deldesir);
#                      apps listed in APP_OWNER (payments, whatsapp -> frappe) follow their own upstream
#   APPS="a b c"       apps to handle (default: every app in sites/apps.txt)
#   MANIFEST=file      release manifest (app=commit lines, see scripts/FLEET.md): every app is
#                      pinned to exactly that commit instead of the fork's current develop head,
#                      so a whole fleet lands on one known release
# The run ends with UPGRADE-OK or UPGRADE-FAILED: <reason> as the last log line.
#
# Order: backup -> fetch (fail early, services still up) -> stop web+workers ->
# fast-forward apps -> requirements -> build -> migrate -> start -> verify.
# Redis stays up throughout (build/migrate need it). If anything fails after the
# services were stopped they are started again: a box serving slightly mixed code
# is better than a till that is down.
set -uo pipefail
B=/home/frappe/frappe-bench
SITE=site.local
# Apps: every app the bench has installed (sites/apps.txt), so a box carrying extra apps is
# covered and one without them is not blocked. APPS=... in the environment overrides.
APPS=${APPS:-$(tr "\n" " " < $B/sites/apps.txt 2>/dev/null)}
[ -n "$APPS" ] || APPS="frappe erpnext"
FORK_OWNER=${FORK_OWNER:-deldesir}
# Apps that are not forks track their upstream directly; name the GitHub owner whose
# remote to follow. Everything else follows FORK_OWNER.
declare -A APP_OWNER=( [payments]=frappe [whatsapp]=frappe )
# Extra paths (ERE) whose local changes were checked by hand and may be discarded on
# THIS box, e.g. a hotfix that has since landed in the fork. Empty = none.
ALLOW_DISCARD=${ALLOW_DISCARD:-}
# Release manifest (app=commit). Empty = follow the fork heads.
MANIFEST=${MANIFEST:-}
[ -z "$MANIFEST" ] || [ -r "$MANIFEST" ] || { echo "UPGRADE-FAILED: manifest $MANIFEST unreadable"; exit 1; }
LOG=/root/bench-upgrade-$(date +%F).log
exec >>"$LOG" 2>&1   # plain append: a tee in a process substitution gets killed at unit exit before it flushes the last lines

asf()  { sudo -u frappe -H bash -lc "cd $B && $*"; }     # login shell: uv/bench live on frappe's PATH
# val: a single value from a command run as frappe. Login shells may print banner/blank
# lines before the command output, so only the last non-empty line is the value.
val()  { asf "$@" 2>/dev/null | awk 'NF{l=$0} END{print l}'; }
step() { printf '\n=== %s %s\n' "$(date +%T)" "$*"; }
STOPPED=0
start_services() { systemctl start frappe-bench-redis.target frappe-bench-web.target frappe-bench-workers.target frappe-bench.target; }
fail() { echo "UPGRADE-FAILED: $*"; [ "$STOPPED" = 1 ] && { echo "restarting services after failure"; start_services; }; exit 1; }

step "pre-state ($(hostname -f 2>/dev/null || hostname))"
for a in $APPS; do printf '%-9s %s\n' "$a" "$(val "git -C apps/$a log -1 --format='%h %cd' --date=short")"; done
df -h / | awk 'NR==2{print "disk free:", $4}'; free -m | awk 'NR==2{print "ram avail MB:", $7}'

# PREFLIGHT_ONLY=1: run every check that can refuse the upgrade, change nothing, exit.
if [ "${PREFLIGHT_ONLY:-0}" != 1 ]; then
  step "backup (db + files)"
  asf "bench --site $SITE backup --with-files" || fail "backup"
  # Record what this upgrade starts from, for bench-rollback.sh: the backup just taken and
  # the commit each app runs now. Written before anything changes.
  bk=$B/sites/$SITE/private/backups
  {
    echo "STAMP=$(date +%Y%m%dT%H%M%S)"; echo "SITE=$SITE"
    echo "DB_BACKUP=$(ls -t $bk/*-database*.sql.gz 2>/dev/null | head -1)"
    echo "FILES_BACKUP=$(find "$bk" -maxdepth 1 -name '*-files*.tar' ! -name '*-private-files*' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
    echo "PRIVATE_BACKUP=$(ls -t $bk/*-private-files*.tar 2>/dev/null | head -1)"
    printf 'PREV_HEADS="'; for a in $APPS; do printf '%s=%s ' "$a" "$(val "git -C apps/$a rev-parse HEAD")"; done; printf '"\n'
    echo "RELEASE=$([ -n "$MANIFEST" ] && awk -F= '$1=="release"{print $2}' "$MANIFEST")"
  } > /root/bench-upgrade.last.env
  chmod 600 /root/bench-upgrade.last.env
  echo "rollback state recorded in /root/bench-upgrade.last.env"
fi

step "fetch fork heads"
declare -A TARGET
for a in $APPS; do
  owner=${APP_OWNER[$a]:-$FORK_OWNER}
  remote=$(asf "git -C apps/$a remote -v" | awk -v o="[/:]$owner/" '$2 ~ o && /fetch/ {print $1; exit}')
  [ -n "$remote" ] || fail "$a: no remote pointing at $owner"
  branch=$(val "git -C apps/$a rev-parse --abbrev-ref HEAD"); [ -n "$branch" ] && [ "$branch" != HEAD ] || branch=develop
  if [ "$(val "git -C apps/$a rev-parse --is-shallow-repository")" = true ]; then
    # depth-1 clone (bench get-app): deepen to just before the deployed commit so the
    # ancestry check and the fast-forward below can see how the two commits relate
    since=$(date -u -d "@$(( $(val "git -C apps/$a log -1 --format=%ct HEAD") - 172800 ))" +%FT%TZ)
    asf "git -C apps/$a fetch -q --shallow-since=$since $remote $branch" || fail "$a: fetch (deepening shallow clone)"
  else
    if ! asf "git -C apps/$a fetch -q $remote $branch" 2>/dev/null; then
      # A private repository the bench user cannot read (an app the hub maintains itself): acceptable
      # only when the manifest pins it to exactly the deployed commit, i.e. nothing is asked of it.
      head=$(val "git -C apps/$a rev-parse HEAD")
      pin=$([ -n "$MANIFEST" ] && awk -F= -v a="$a" '$1==a{print $2}' "$MANIFEST")
      if [ -n "$pin" ] && [ "$pin" = "$head" ]; then
        echo "$a: remote not readable by the bench user; already at the manifest commit ${head:0:10}, keeping it"
        TARGET[$a]=$head; continue
      fi
      fail "$a: fetch (remote not readable and no matching manifest pin)"
    fi
  fi
  TARGET[$a]=$(val "git -C apps/$a rev-parse FETCH_HEAD")
  [[ "${TARGET[$a]}" =~ ^[0-9a-f]{40}$ ]] || fail "$a: could not read the fetched commit (got: ${TARGET[$a]})"
  if [ -n "$MANIFEST" ]; then
    pin=$(awk -F= -v a="$a" '$1==a{print $2}' "$MANIFEST")
    if [ -z "$pin" ]; then
      echo "$a: not in the manifest; keeping the deployed commit"; TARGET[$a]=$(val "git -C apps/$a rev-parse HEAD"); continue
    fi
    [[ "$pin" =~ ^[0-9a-f]{40}$ ]] || fail "$a: malformed commit in manifest $MANIFEST"
    # the pinned commit is normally already in the fetched develop history; fetch it by hash otherwise
    asf "git -C apps/$a cat-file -e $pin^{commit}" 2>/dev/null || asf "git -C apps/$a fetch -q $remote $pin" || fail "$a: manifest commit ${pin:0:10} is not on $remote"
    TARGET[$a]=$pin
    printf '%-9s release %s = %s\n' "$a" "$(awk -F= '$1=="release"{print $2}' "$MANIFEST")" "${pin:0:10}"
  else
    printf '%-9s %s/%s = %s\n' "$a" "$remote" "$branch" "${TARGET[$a]:0:10}"
  fi
  # only disposable drift may be discarded (generated types, lockfiles); anything else stops the run
  dirty=$(asf "git -C apps/$a status --porcelain --untracked-files=no" | grep -v -E "^[[:space:]]*\$|yarn\.lock\$|\.d\.ts\$|package-lock\.json\$${ALLOW_DISCARD:+|$ALLOW_DISCARD}" || true)
  [ -z "$dirty" ] || fail "$a has local changes that are not disposable:
$dirty"
  asf "git -C apps/$a merge-base --is-ancestor HEAD ${TARGET[$a]}" || fail "$a: target commit is not a fast-forward of the deployed commit (the box is ahead of the release or diverged; a rollback is a manual checkout)"
done

if [ "${PREFLIGHT_ONLY:-0}" = 1 ]; then
  echo "PREFLIGHT-OK: every app fast-forwards cleanly; nothing was changed"
  exit 0
fi

step "stop web + workers (redis stays up)"
systemctl stop frappe-bench-web.target frappe-bench-workers.target
systemctl stop frappe-bench-frappe-web.service frappe-bench-node-socketio.service 'frappe-bench-frappe-*-worker@*.service' 2>/dev/null
STOPPED=1

step "fast-forward apps"
for a in $APPS; do
  asf "git -C apps/$a checkout -q -- . && git -C apps/$a merge -q --ff-only ${TARGET[$a]}" || fail "$a: fast-forward"
  printf '%-9s -> %s\n' "$a" "$(val "git -C apps/$a log -1 --format='%h %cd' --date=short")"
done

step "python + node requirements"
asf "bench setup requirements" || fail "setup requirements"

# The asset build can peak at several GB. On a box with little free memory and no swap,
# add temporary swap for the build (as the erpnext role does) rather than risk the OOM killer.
SWAPFILE=/var/tmp/.bench_upgrade_swap
avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo); swap=$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo)
if [ "$avail" -lt 5000 ] && [ "$swap" -lt 1024 ]; then
  echo "only ${avail} MB available and ${swap} MB swap: adding 4G of temporary swap for the build"
  fallocate -l 4G "$SWAPFILE" && chmod 600 "$SWAPFILE" && mkswap -q "$SWAPFILE" && swapon "$SWAPFILE" || echo "could not add swap; continuing without"
fi
drop_swap() { [ -f "$SWAPFILE" ] && { swapoff "$SWAPFILE" 2>/dev/null; rm -f "$SWAPFILE"; }; return 0; }

step "build assets"
asf "bench build" || { drop_swap; fail "build"; }
drop_swap

# A patch can leave a second DB connection open inside the migrate process; its own
# schema ALTER then waits on that connection's metadata lock, and MariaDB's default
# lock_wait_timeout is a DAY. Seen when jumping from old code; a rerun is clean because
# the patch is already logged. The watchdog ends a migrate stuck like that so the retry runs.
migrate_once() {
  asf "bench --site $SITE migrate" &
  local mp=$!
  (
    while kill -0 "$mp" 2>/dev/null; do
      sleep 30
      stuck=$(val "bench --site $SITE mariadb -N -e \"select count(*) from information_schema.processlist where db = database() and state like 'Waiting for table metadata lock' and time > 120\"")
      if [[ "${stuck:-0}" =~ ^[0-9]+$ ]] && [ "$stuck" -ge 1 ]; then
        echo "WATCHDOG: migrate has waited >120s on a metadata lock held by its own process; terminating it so the retry can run"
        pkill -TERM -u frappe -f "frappe.utils.bench_helper frappe --site $SITE migrate"
        break
      fi
    done
  ) &
  local wd=$!
  wait "$mp"; local rc=$?
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  return $rc
}

step "migrate"
if ! migrate_once; then
  echo "migrate failed once (lock waits happen); retrying in 20s"; sleep 20
  migrate_once || fail "migrate"
fi

step "start services"
start_services; STOPPED=0
asf "bench --site $SITE clear-cache >/dev/null && bench --site $SITE clear-website-cache >/dev/null" || true

step "post-state"
for a in $APPS; do printf '%-9s %s\n' "$a" "$(val "git -C apps/$a log -1 --format='%h %cd' --date=short")"; done
asf "bench version" 2>/dev/null | sed 's/^/  /'

step "wait for web (gunicorn preload can take a few minutes)"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Host: $SITE" http://127.0.0.1:8000/api/method/ping || true)
  [ "$code" = 200 ] && { echo "ping 200 after ~$((i * 10))s"; break; }
  sleep 10
done
[ "${code:-}" = 200 ] || fail "web did not answer ping within 400s"
systemctl --no-legend list-units 'frappe-bench*' --state=failed | sed 's/^/FAILED UNIT: /'
echo "UPGRADE-OK"
