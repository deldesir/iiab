#!/bin/bash
# Semaphore task wrapper: reboot BOX (or every remote box when BOX=all) inside its maintenance
# window; NOW=yes overrides the window. Waits for the ERP API and re-checks after each reboot.
set -uo pipefail
[ -n "${BOX:-}" ] || { echo "BOX required (an id, or all)"; exit 2; }
[[ "$BOX" =~ ^[a-z0-9-]+$ ]] || { echo "BOX must be a box id, alias, or all" >&2; exit 2; }
NOW_FLAG=""; [ "${NOW:-no}" = yes ] && NOW_FLAG=--now
if [ "$BOX" = all ]; then exec sudo -n /usr/local/sbin/fleet reboot --all ${NOW_FLAG:+"$NOW_FLAG"}; fi
exec sudo -n /usr/local/sbin/fleet reboot "$BOX" ${NOW_FLAG:+"$NOW_FLAG"}
