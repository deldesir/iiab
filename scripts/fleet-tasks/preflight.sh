#!/bin/bash
# Semaphore task wrapper: would every box (or BOX) fast-forward to RELEASE cleanly? changes nothing
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet upgrade ${BOX:---all} --preflight ${RELEASE:+--release "$RELEASE"}
