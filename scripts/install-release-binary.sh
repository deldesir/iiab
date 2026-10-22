#!/bin/bash
# install-release-binary.sh <RELEASE_ASSET_URL> <DEST>
#
# Install a GitHub release binary to DEST, preferring the gzipped asset (much
# smaller download) and VERIFYING it against the release's SHA256SUMS, which
# must exist and list the asset: no checksum, no install; a mismatch fails.
#
# RELEASE_ASSET_URL is the RAW asset URL, e.g.
#   https://github.com/deldesir/courier/releases/latest/download/courier-linux-amd64
#
# Used by the ai-update, rapidpro and wuzapi roles so the install path and the
# update path stay in sync. Exit non-zero on failure (caller may build from src).
set -euo pipefail

url="${1:?release asset url}"; dest="${2:?destination path}"
base="${url%/*}"; asset="${url##*/}"
# The release's SHA256SUMS is required: no checksum file, no install.
sums="$(curl -fsSL "$base/SHA256SUMS")" || { echo "install-release-binary: no SHA256SUMS at $base" >&2; exit 1; }
[ -n "$sums" ] || { echo "install-release-binary: empty SHA256SUMS at $base" >&2; exit 1; }

# verify FILE against ASSET-NAME; an asset the checksum file does not list is refused
verify() {
  local want
  want="$(printf '%s\n' "$sums" | awk -v a="$2" '$2 == a { print $1 }')"
  [ -n "$want" ] || { echo "install-release-binary: $2 is not listed in SHA256SUMS" >&2; return 1; }
  printf '%s  %s\n' "$want" "$1" | sha256sum -c - >/dev/null || { echo "install-release-binary: checksum mismatch for $2" >&2; return 1; }
}

tmp="$(mktemp)"; trap 'rm -f "$tmp" "$tmp.gz" "$dest.new"' EXIT

# Stage into "$dest.new" on the SAME filesystem, then atomically rename over
# "$dest". A direct write/cp to "$dest" fails with ETXTBSY ("Text file busy")
# when the destination is a running service binary; rename() swaps the
# directory entry while the live process keeps its old inode, so updates of
# active courier/mailroom/wuzapi services work without a pre-stop.
# Prefer the gzipped asset when the checksum file lists it; a mismatch is a failure, never a fallback.
if printf '%s\n' "$sums" | awk -v a="$asset.gz" '$2 == a { found = 1 } END { exit !found }'; then
  curl -fsSL "$base/$asset.gz" -o "$tmp.gz"
  verify "$tmp.gz" "$asset.gz"
  gunzip -c "$tmp.gz" > "$dest.new"
else
  curl -fsSL "$url" -o "$tmp"
  verify "$tmp" "$asset"
  cp -f "$tmp" "$dest.new"
fi
chmod 0755 "$dest.new"
mv -f "$dest.new" "$dest"
