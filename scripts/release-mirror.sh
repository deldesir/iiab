#!/bin/bash
# The public release mirror of a private repository (the gateway, the router): an export of one
# ref, minus what never leaves the house, committed as one release into a mirror repository.
#
#   release-mirror.sh <name> <source tree> <ref> <mirror dir> [push url]
#
# <name> picks the scrub list below. The mirror dir is a working clone of the public mirror (made
# once with `git clone <push url> <mirror dir>`, or `git init` for a dry run); the export replaces
# its tree, the release is committed with the source commit in its message, and pushed when a push
# url is given. The source history never leaves: the mirror's history is the releases.
set -euo pipefail
name=$1; src=$2; ref=$3; mirror=$4; push=${5:-}
case "$name" in
  gateway) scrub=(backups scripts/exports scripts/flows tests scripts/data/seed_organized_data.py CLAUDE.md .claude .github) ;;
  rivebot) scrub=(data/brains/konex-sales.rive data/brains/_common/forms.rive CLAUDE.md .claude .github) ;;
  *) echo "release-mirror: unknown name $name (gateway|rivebot)" >&2; exit 2 ;;
esac
commit=$(git -C "$src" rev-parse --short "$ref")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$src" archive "$ref" | tar -x -C "$tmp"
for p in "${scrub[@]}"; do rm -rf "${tmp:?}/${p:?}"; done
# What must not go public: the scan fails the release rather than publish it.
if grep -rIEn '\+?509[ -]?[0-9]{2}[ -]?[0-9]{2}[ -]?[0-9]{4}|Co-Authored-By|Generated with \[Claude|100\.64\.[0-9]+\.[0-9]+' "$tmp" | head -5 | grep .; then
  echo "release-mirror: the export still carries what never leaves the house (above); fix at source" >&2; exit 3
fi
[ -d "$mirror/.git" ] || { echo "release-mirror: $mirror is not a git clone of the mirror" >&2; exit 2; }
find "$mirror" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$tmp"/. "$mirror"/
git -C "$mirror" add -A
if git -C "$mirror" diff --cached --quiet; then echo "release-mirror: $name at $commit already mirrored"; exit 0; fi
git -C "$mirror" commit -q -m "release: $name at $commit"
echo "release-mirror: $name at $commit committed in $mirror ($(git -C "$mirror" rev-parse --short HEAD))"
if [ -n "$push" ]; then git -C "$mirror" push -q "$push" HEAD:main && echo "release-mirror: pushed to $push"; fi
