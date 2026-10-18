#!/bin/bash
# Semaphore task wrapper: maintenance page on for BOX (contract grace exhausted); REASON is logged
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
[ -n "${BOX:-}" ] || { echo "BOX required"; exit 2; }; exec sudo -n /usr/local/sbin/fleet suspend "$BOX" --reason "${REASON:-}"
