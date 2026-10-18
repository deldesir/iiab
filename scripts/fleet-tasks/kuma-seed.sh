#!/bin/bash
# Semaphore task wrapper: re-seed the monitor (adds what is missing, never deletes)
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet-kuma-seed
