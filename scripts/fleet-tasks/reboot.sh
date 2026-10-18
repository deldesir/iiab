#!/bin/bash
# Semaphore task wrapper: reboot BOX (or every remote box when BOX=all) inside its maintenance
# window; NOW=yes overrides the window. Waits for the ERP API and re-checks after each reboot.
set -uo pipefail
[ -n "${BOX:-}" ] || { echo "BOX required (an id, or all)"; exit 2; }
if [ "$BOX" = all ]; then exec sudo -n /usr/local/sbin/fleet reboot --all $([ "${NOW:-no}" = yes ] && echo --now); fi
exec sudo -n /usr/local/sbin/fleet reboot "$BOX" $([ "${NOW:-no}" = yes ] && echo --now)
