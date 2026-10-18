#!/bin/bash
# Create the fleet-bot API user on the fleet site and store its key pair in /etc/iiab/fleet.conf (0600). Prints no secrets.
set -uo pipefail
B=/home/frappe/frappe-bench; CONF=/etc/iiab/fleet.conf
line=$(sudo -u frappe -H bash -lc "cd $B && bench --site fleet.local execute \"exec(open('$B/.fleet_apiuser.py').read(), {})\"" 2>/dev/null | grep -E '^KEYS ' | tail -1)
key=$(echo "$line" | awk '{print $2}'); secret=$(echo "$line" | awk '{print $3}')
[ -n "$key" ] && [ -n "$secret" ] || { echo "APIUSER-FAILED"; exit 1; }
sed -i "s|^FLEET_API_KEY=.*|FLEET_API_KEY=$key|; s|^FLEET_API_SECRET=.*|FLEET_API_SECRET=$secret|" "$CONF"; chmod 600 "$CONF"
rm -f "$B/.fleet_apiuser.py"
echo "APIUSER-OK"
