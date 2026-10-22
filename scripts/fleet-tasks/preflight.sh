#!/bin/bash
# Semaphore task wrapper: would every box (or BOX) fast-forward to RELEASE cleanly? changes nothing
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
# Survey values are validated before they reach a root command.
if [ -n "${BOX:-}" ] && ! [[ "$BOX" =~ ^[a-z0-9-]+$ ]]; then echo "BOX must be a box id or alias" >&2; exit 2; fi
if [ -n "${RELEASE:-}" ] && ! [[ "$RELEASE" =~ ^[A-Za-z0-9._-]+$ ]]; then echo "RELEASE must be a manifest id" >&2; exit 2; fi
exec sudo -n /usr/local/sbin/fleet upgrade "${BOX:---all}" --preflight ${RELEASE:+--release "$RELEASE"}
