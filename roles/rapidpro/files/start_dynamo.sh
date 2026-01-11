#!/bin/bash
# Helper script to start Dynalite reliably inside Screen/PRoot
# Bypass complicated shell quoting issues in PDSM
export PATH=/usr/bin:$PATH
export NODE_PATH=/opt/iiab/rapidpro/node_modules:/usr/lib/node_modules

# Ensure directory exists (redundant but safe)
mkdir -p /opt/iiab/rapidpro/dynamo

# Force kill anything holding the port inside PRoot (Best Effort)
fuser -k -n tcp 4567 || true

# Exec the process directly with safe argument formatting
# Note: --listening=127.0.0.1 seems ignored by node/dynalite in some contexts, but we keep it safe.
exec /usr/bin/node /opt/iiab/rapidpro/node_modules/.bin/dynalite \
  --port=4567 \
  --listening=127.0.0.1 \
  --path=/opt/iiab/rapidpro/dynamo \
  > /var/log/rapidpro/dynamo.log 2>&1
