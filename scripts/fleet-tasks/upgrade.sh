#!/bin/bash
# Semaphore task wrapper: roll BOX (or all) to RELEASE in canary order; NOW=yes ignores maintenance windows
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
# Survey values are validated before they reach a root command.
if [ -n "${BOX:-}" ] && ! [[ "$BOX" =~ ^[a-z0-9-]+$ ]]; then echo "BOX must be a box id or alias" >&2; exit 2; fi
if [ -n "${RELEASE:-}" ] && ! [[ "$RELEASE" =~ ^[A-Za-z0-9._-]+$ ]]; then echo "RELEASE must be a manifest id" >&2; exit 2; fi
if [ -n "${ALLOW_DISCARD:-}" ] && ! [[ "$ALLOW_DISCARD" =~ ^[A-Za-z0-9_.|()^$*+?\[\]{}/-]+$ ]]; then echo "ALLOW_DISCARD must be a plain regular expression" >&2; exit 2; fi
NOW_FLAG=""; [ "${NOW:-no}" = yes ] && NOW_FLAG=--now
exec sudo -n /usr/local/sbin/fleet upgrade "${BOX:---all}" ${RELEASE:+--release "$RELEASE"} ${NOW_FLAG:+"$NOW_FLAG"} ${ALLOW_DISCARD:+--allow-discard "$ALLOW_DISCARD"}
