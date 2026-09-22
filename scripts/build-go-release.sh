#!/bin/bash
# build-go-release.sh — cross-compile a Go fork (courier, mailroom, wuzapi) and,
# with --publish, cut the GitHub release the ai-update / wuzapi roles install from.
#
#   build-go-release.sh <courier|mailroom|wuzapi> <tag> [--repo-dir DIR] [--publish]
#
#   OUT_DIR       where binaries land (default /tmp/go-release/<app>)
#   NOTES_FILE    release notes for --publish (default: a generated stub)
#   FORK_OWNER    GitHub owner of the forks (default: deldesir)
#
# Fork numbering: the forks' tags SHADOW the upstream tags. A courier or mailroom
# release is named after the upstream version it was merged from (v26.3.55 =
# "upstream v26.3.55 + our patches"), so the role's version pin reads like
# upstream's. wuzapi has its own sequence (v1, v2, ...) because the upstream
# project does not tag releases.
#
# Build: CGO_ENABLED=0, -trimpath, stripped, linux amd64 + arm64. courier and
# mailroom get -X main.version/main.date (their cmd/<app>/main.go declares both);
# wuzapi builds from the repo root and has no such variables. Each binary is
# gzipped (gzip -kf keeps the raw one: install-release-binary.sh prefers the .gz
# and falls back to the raw asset) and SHA256SUMS covers all four: 5 assets.
#
# Publish: a draft release is created first, then assets are uploaded ONE AT A
# TIME. A multi-asset `gh release create` once failed mid-upload and gh deleted
# its own draft, taking the notes with it. The draft is only flipped to a real,
# "latest" release once all 5 assets are verified present.
set -euo pipefail

usage() { sed -n '2,20p' "$0" >&2; exit 2; }
[ $# -ge 2 ] || usage
APP=$1; TAG=$2; shift 2
PUBLISH=0; REPO_DIR=""
while [ $# -gt 0 ]; do
  case $1 in
    --publish) PUBLISH=1 ;;
    --repo-dir) REPO_DIR=$2; shift ;;
    *) usage ;;
  esac; shift
done
FORK_OWNER=${FORK_OWNER:-deldesir}
case $APP in
  courier|mailroom) : "${REPO_DIR:=/opt/iiab/$APP}"; PKG=./cmd/$APP; VERSIONED=1 ;;
  wuzapi)           : "${REPO_DIR:=/opt/iiab/wuzapi-src}"; PKG=.;            VERSIONED=0 ;;
  *) echo "unknown app: $APP" >&2; usage ;;
esac
[[ $TAG =~ ^v[0-9] ]] || { echo "tag must look like vX[.Y.Z]: $TAG" >&2; exit 2; }
OUT_DIR=${OUT_DIR:-/tmp/go-release/$APP}

export PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=auto CGO_ENABLED=0 HOME=${HOME:-/root}
command -v go >/dev/null || { echo "go not found (expected /usr/local/go/bin)" >&2; exit 1; }

# The release must be reproducible from origin/main: refuse a branch, dirty tree
# or unpushed commits.
g() { git -C "$REPO_DIR" "$@"; }
[ "$(g branch --show-current)" = main ] || { echo "$REPO_DIR is not on main" >&2; exit 1; }
[ -z "$(g status --porcelain --untracked-files=no)" ] || { echo "$REPO_DIR has local changes" >&2; exit 1; }
g fetch -q origin main
[ "$(g rev-parse HEAD)" = "$(g rev-parse origin/main)" ] || { echo "$REPO_DIR is not at origin/main" >&2; exit 1; }
HEAD_SHA=$(g rev-parse --short HEAD)

LDFLAGS="-s -w"
if [ "$VERSIONED" = 1 ]; then
  LDFLAGS="$LDFLAGS -X main.version=$TAG -X main.date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR/$APP-linux-"* "$OUT_DIR/SHA256SUMS"
for arch in amd64 arm64; do
  echo "== $APP $TAG linux/$arch ($(date +%T), go $(go version | awk '{print $3}'))"
  (cd "$REPO_DIR" && GOOS=linux GOARCH=$arch go build -trimpath -ldflags "$LDFLAGS" -o "$OUT_DIR/$APP-linux-$arch" "$PKG")
  gzip -kf "$OUT_DIR/$APP-linux-$arch"
done
(cd "$OUT_DIR" && sha256sum "$APP"-linux-* > SHA256SUMS && cat SHA256SUMS)
echo "BUILD-OK $APP $TAG from $HEAD_SHA -> $OUT_DIR"
[ "$PUBLISH" = 1 ] || exit 0

REPO=$FORK_OWNER/$APP
NOTES=${NOTES_FILE:-}
if [ -z "$NOTES" ]; then
  NOTES=$OUT_DIR/RELEASE-NOTES.md
  printf '%s %s\n\nBuilt from `main` at %s. Linux amd64 + arm64, static, stripped.\n' "$APP" "$TAG" "$HEAD_SHA" > "$NOTES"
fi
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "release $TAG already exists on $REPO; uploading assets with --clobber"
else
  gh release create "$TAG" --repo "$REPO" --target main --draft --title "$TAG" --notes-file "$NOTES"
fi
for f in "$OUT_DIR/$APP"-linux-amd64 "$OUT_DIR/$APP"-linux-amd64.gz \
         "$OUT_DIR/$APP"-linux-arm64 "$OUT_DIR/$APP"-linux-arm64.gz "$OUT_DIR/SHA256SUMS"; do
  echo "upload $(basename "$f")"
  gh release upload "$TAG" "$f" --repo "$REPO" --clobber
done
n=$(gh release view "$TAG" --repo "$REPO" --json assets --jq '.assets | length')
[ "$n" = 5 ] || { echo "expected 5 assets on $REPO $TAG, found $n; left as draft" >&2; exit 1; }
gh release edit "$TAG" --repo "$REPO" --draft=false --latest
echo "PUBLISH-OK https://github.com/$REPO/releases/tag/$TAG"
