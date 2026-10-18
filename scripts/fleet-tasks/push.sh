#!/bin/bash
# Semaphore task wrapper: mirror inventory, checks, releases and catalog into the fleet site
# Survey variables arrive as environment variables (target "env"); the command runs as root
# through the sudoers rule for the semaphore user (fleet and fleet-kuma-seed only).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet push
