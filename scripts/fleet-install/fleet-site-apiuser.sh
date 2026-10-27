#!/bin/bash
# Create (or re-key) the fleet-bot API user on the fleet site and store its key pair in
# /etc/iiab/fleet.conf (0600). The helper runs from a private temporary directory; the pair
# goes into the configuration through a 0600 rewrite, never through a command line. Prints no secrets.
set -euo pipefail
B=/home/frappe/frappe-bench; CONF=/etc/iiab/fleet.conf; SITE=${FLEET_SITE_NAME:-fleet.local}
HELPER=$(dirname "$(readlink -f "$0")")/fleet-site-apiuser.py
[ -r "$HELPER" ] || { echo "APIUSER-FAILED: $HELPER missing"; exit 1; }
[ -f "$CONF" ] || { echo "APIUSER-FAILED: $CONF missing (fleet-site-create.sh first)"; exit 1; }
D=$(mktemp -d /tmp/fleet-apiuser.XXXXXX); chown root:frappe "$D"; chmod 0750 "$D"; trap 'rm -rf "$D"' EXIT
install -m 0640 -o root -g frappe "$HELPER" "$D/helper.py"
line=$(sudo -u frappe -H bash -lc "cd $B && bench --site $SITE execute \"exec(open('$D/helper.py').read(), {})\"" 2>/dev/null | grep -E '^KEYS ' | tail -1)
key=$(echo "$line" | awk '{print $2}'); secret=$(echo "$line" | awk '{print $3}')
[ -n "$key" ] && [ -n "$secret" ] || { echo "APIUSER-FAILED: the helper printed no key pair"; exit 1; }
FLEET_API_KEY="$key" FLEET_API_SECRET="$secret" python3 - "$CONF" <<'PY'
import os, sys
path = sys.argv[1]
lines = open(path).read().splitlines()
pairs = {"FLEET_API_KEY": os.environ["FLEET_API_KEY"], "FLEET_API_SECRET": os.environ["FLEET_API_SECRET"]}
out, seen = [], set()
for l in lines:
    k = l.split("=", 1)[0]
    if k in pairs:
        out.append(f"{k}={pairs[k]}"); seen.add(k)
    else:
        out.append(l)
for k, v in pairs.items():
    if k not in seen:
        out.append(f"{k}={v}")
tmp = path + ".new"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("\n".join(out) + "\n")
os.replace(tmp, path)
os.chmod(path, 0o600)
PY
echo "APIUSER-OK"
