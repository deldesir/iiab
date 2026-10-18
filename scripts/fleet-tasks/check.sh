#!/bin/bash
# Semaphore task wrapper: health check of every box (or BOX) and push to the fleet site
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet check ${BOX:---all}
