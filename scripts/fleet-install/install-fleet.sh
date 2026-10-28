#!/bin/bash
# Install (or update) the fleet command on this hub from the repository: the entry point as a
# copy at /usr/local/sbin/fleet and the package as a copy under /usr/local/lib/fleet_tool, so the
# running hub never depends on which branch the repository is on. Re-run after a pull that
# touches scripts/fleet or scripts/fleet_tool. Same for fleet-kuma-seed.
#   bash scripts/fleet-install/install-fleet.sh
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
install -m 0755 "$here/fleet" /usr/local/sbin/fleet
install -d -m 0755 /usr/local/lib/fleet_tool
rsync -a --delete --exclude __pycache__ "$here/fleet_tool/" /usr/local/lib/fleet_tool/
[ -f "$here/fleet-kuma-seed" ] && install -m 0755 "$here/fleet-kuma-seed" /usr/local/sbin/fleet-kuma-seed
/usr/local/sbin/fleet --help >/dev/null && echo "fleet installed: $(/usr/local/sbin/fleet --help | head -1 | cut -c1-60)"
