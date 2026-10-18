#!/bin/bash
# Semaphore task wrapper: print the digest; SEND=yes delivers it through the gateway
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
if [ "${SEND:-no}" = yes ]; then exec sudo -n /usr/local/sbin/fleet digest --send; else exec sudo -n /usr/local/sbin/fleet digest; fi
