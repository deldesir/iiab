#!/bin/bash
# Semaphore task wrapper: mirror the observer's backup copies onto this hub (fleet backup-pull).
set -uo pipefail
exec sudo -n /usr/local/sbin/fleet backup-pull
