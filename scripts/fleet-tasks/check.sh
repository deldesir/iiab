#!/bin/bash
# Semaphore task wrapper: health check of every box (or BOX) and push to the fleet site
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
# Survey values are validated before they reach a root command.
if [ -n "${BOX:-}" ] && ! [[ "$BOX" =~ ^[a-z0-9-]+$ ]]; then echo "BOX must be a box id or alias" >&2; exit 2; fi
exec sudo -n /usr/local/sbin/fleet check "${BOX:---all}"
