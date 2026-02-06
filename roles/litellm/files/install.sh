#!/bin/bash
set -e

# Ensure we use the correct python
poetry env use /usr/bin/python3

# Check for corrupted lock file
if [ ! -f poetry.lock ] || grep -q '<<<<<<<' poetry.lock; then
  echo "Regenerating corrupted/missing poetry.lock..."
  rm -f poetry.lock
  poetry lock
fi

# Install dependencies with proxy extras
poetry install -E proxy
