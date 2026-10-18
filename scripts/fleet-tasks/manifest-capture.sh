#!/bin/bash
# Semaphore task wrapper: record a release manifest RELEASE_ID from BOX (default hub)
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
[ -n "${RELEASE_ID:-}" ] || { echo "RELEASE_ID required"; exit 2; }; sudo -n /usr/local/sbin/fleet manifest capture --id "$RELEASE_ID" --from "${BOX:-hub}" ${NOTES:+--notes "$NOTES"} && if [ "${SET_CURRENT:-yes}" = yes ]; then sudo -n /usr/local/sbin/fleet manifest set-current "$RELEASE_ID"; fi
