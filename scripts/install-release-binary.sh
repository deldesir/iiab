#!/bin/bash
# install-release-binary.sh <RELEASE_ASSET_URL> <DEST>
#
# Install a GitHub release binary to DEST, preferring the gzipped asset (much
# smaller download) and VERIFYING it against the release's SHA256SUMS. Falls
# back to the raw asset if no .gz exists, or if the .gz fails verification.
#
# RELEASE_ASSET_URL is the RAW asset URL, e.g.
#   https://github.com/deldesir/courier/releases/latest/download/courier-linux-amd64
#
# Used by the ai-update, rapidpro and wuzapi roles so the install path and the
# update path stay in sync. Exit non-zero on failure (caller may build from src).
set -euo pipefail

url="$1"; dest="$2"
base="${url%/*}"; asset="${url##*/}"
sums="$(curl -fsSL "$base/SHA256SUMS" 2>/dev/null || true)"

# verify FILE against ASSET-NAME; pass if SHA256SUMS is missing or doesn't list it
verify() {
  [ -z "$sums" ] && return 0
  local want
  want="$(printf '%s\n' "$sums" | awk -v a="$2" '$2 == a { print $1 }')"
  [ -z "$want" ] && return 0
  printf '%s  %s\n' "$want" "$1" | sha256sum -c - >/dev/null
}

tmp="$(mktemp)"; trap 'rm -f "$tmp" "$tmp.gz"' EXIT

if curl -fsSL "$base/$asset.gz" -o "$tmp.gz" 2>/dev/null && verify "$tmp.gz" "$asset.gz"; then
  gunzip -c "$tmp.gz" > "$dest"
else
  curl -fsSL "$url" -o "$tmp"
  verify "$tmp" "$asset"
  cp -f "$tmp" "$dest"
fi
chmod 0755 "$dest"
