#!/bin/bash
# Semaphore task wrapper: roll BOX (or all) to RELEASE in canary order; NOW=yes ignores maintenance windows
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet upgrade ${BOX:---all} ${RELEASE:+--release "$RELEASE"} $([ "${NOW:-no}" = yes ] && echo --now) ${ALLOW_DISCARD:+--allow-discard "$ALLOW_DISCARD"}
