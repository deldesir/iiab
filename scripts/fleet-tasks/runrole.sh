#!/bin/bash
# Semaphore task wrapper: run an IIAB role on a box through the box's own `runrole`.
# IIAB playbooks read /etc/iiab/local_vars.yml and iiab_state.yml on the machine they run on,
# so they are never driven from the hub with Ansible over SSH; the hub only starts them.
#   BOX   box id from the inventory (hub = local)      ROLE  role name, e.g. erpnext
#   TAGS  optional comma-separated tags (uses the run-one-role.yml form that accepts --tags)
set -uo pipefail
[ -n "${BOX:-}" ] && [ -n "${ROLE:-}" ] || { echo "BOX and ROLE required"; exit 2; }
exec sudo -n /usr/local/sbin/fleet runrole "$BOX" "$ROLE" ${TAGS:+--tags "$TAGS"}
