#!/bin/bash
# build-go-release.sh — cross-compile a Go fork (courier, mailroom, wuzapi) and,
# with --publish, cut the GitHub release the ai-update / wuzapi roles install from.
#
#   build-go-release.sh <courier|mailroom|wuzapi> <tag> [--repo-dir DIR] [--publish]
#                       [--toolchain goX.Y.Z] [--strict] [--no-vulncheck]
#
#   OUT_DIR       where binaries land (default /tmp/go-release/<app>)
#   NOTES_FILE    release notes for --publish (default: a generated stub)
#   FORK_OWNER    GitHub owner of the forks (default: deldesir)
#
# Fork numbering: the forks' tags SHADOW the upstream tags. A courier or mailroom
# release is named after the upstream version it was merged from (v26.3.55 =
# "upstream v26.3.55 + our patches"), so the role's version pin reads like
# upstream's. wuzapi has its own sequence (v1, v2, ...) because the upstream
# project does not tag releases. A rebuild of the same source — a newer
# toolchain, a dependency bump upstream has not made yet — takes a -N suffix
# (v26.3.120-1: upstream v26.3.120 + our patches, build 1). Whatever the tag,
# the role pins (roles/rapidpro, roles/wuzapi defaults) move in the same PR as
# the release: a pin left behind is what the next ai-update installs.
#
# Toolchain: go.mod's `go` directive names one release (go 1.27.0) and
# GOTOOLCHAIN=auto builds with exactly that, missing every patch release since
# (the 2026-11 binaries carried 14 standard-library CVEs fixed in go1.27.2).
# The script asks go.dev for the newest patch release of that minor and builds
# with it, downloaded on first use; --toolchain names one instead, for an
# offline box or a deliberate choice. The release notes record the toolchain.
#
# Vulnerability gate: govulncheck (binary mode, the Go vulnerability database)
# runs on the amd64 artefact. A finding in the standard library means the
# toolchain is behind: the build fails. Findings in modules are listed with
# their fixed versions — fork-local `go get` bumps on a branch, or upstream's
# next sync — and fail the build only with --strict. --no-vulncheck skips the
# gate (offline builds); the release notes say so.
#
# Build: CGO_ENABLED=0, -trimpath, stripped, linux amd64 + arm64. courier and
# mailroom get -X main.version/main.date (their cmd/<app>/main.go declares both);
# wuzapi builds from the repo root and has no such variables. One package
# compiles at a time (GO_BUILD_JOBS, default 1) with a tight collector (GOGC):
# the pipeline box runs the whole stack and has little memory to spare, and
# mailroom's elasticsearch client alone takes ~2 GB to compile; three of those in
# parallel got the compiler OOM-killed (2026-11-07). Each binary is
# gzipped (gzip -kf keeps the raw one: install-release-binary.sh prefers the .gz
# and falls back to the raw asset) and SHA256SUMS covers all four: 5 assets.
#
# Publish: a draft release is created first, then assets are uploaded ONE AT A
# TIME. A multi-asset `gh release create` once failed mid-upload and gh deleted
# its own draft, taking the notes with it. The draft is only flipped to a real,
# "latest" release once all 5 assets are verified present.
set -euo pipefail

usage() { sed -n '2,45p' "$0" >&2; exit 2; }
[ $# -ge 2 ] || usage
APP=$1; TAG=$2; shift 2
PUBLISH=0; REPO_DIR=""; TOOLCHAIN=""; STRICT=0; VULNCHECK=1
while [ $# -gt 0 ]; do
  case $1 in
    --publish) PUBLISH=1 ;;
    --repo-dir) REPO_DIR=$2; shift ;;
    --toolchain) TOOLCHAIN=$2; shift ;;
    --strict) STRICT=1 ;;
    --no-vulncheck) VULNCHECK=0 ;;
    *) usage ;;
  esac; shift
done
FORK_OWNER=${FORK_OWNER:-deldesir}
case $APP in
  courier|mailroom) : "${REPO_DIR:=/opt/iiab/$APP}"; PKG=./cmd/$APP; VERSIONED=1 ;;
  wuzapi)           : "${REPO_DIR:=/opt/iiab/wuzapi-src}"; PKG=.;            VERSIONED=0 ;;
  *) echo "unknown app: $APP" >&2; usage ;;
esac
[[ $TAG =~ ^v[0-9] ]] || { echo "tag must look like vX[.Y.Z][-N]: $TAG" >&2; exit 2; }
OUT_DIR=${OUT_DIR:-/tmp/go-release/$APP}

export PATH=/usr/local/go/bin:$PATH CGO_ENABLED=0 HOME=${HOME:-/root} GOGC=${GOGC:-30}
GO_BUILD_JOBS=${GO_BUILD_JOBS:-1}
command -v go >/dev/null || { echo "go not found (expected /usr/local/go/bin)" >&2; exit 1; }

# The release must be reproducible from origin/main: refuse a branch, dirty tree
# or unpushed commits.
g() { git -C "$REPO_DIR" "$@"; }
[ "$(g branch --show-current)" = main ] || { echo "$REPO_DIR is not on main" >&2; exit 1; }
[ -z "$(g status --porcelain --untracked-files=no)" ] || { echo "$REPO_DIR has local changes" >&2; exit 1; }
g fetch -q origin main
[ "$(g rev-parse HEAD)" = "$(g rev-parse origin/main)" ] || { echo "$REPO_DIR is not at origin/main" >&2; exit 1; }
HEAD_SHA=$(g rev-parse --short HEAD)

# The toolchain: the newest patch release of go.mod's minor, unless named.
GO_MINOR=$(awk '$1 == "go" { print $2 }' "$REPO_DIR/go.mod" | cut -d. -f1,2)
[ -n "$GO_MINOR" ] || { echo "no go directive in $REPO_DIR/go.mod" >&2; exit 1; }
if [ -z "$TOOLCHAIN" ]; then
  TOOLCHAIN=$(curl -fsSL 'https://go.dev/dl/?mode=json&include=all' | python3 -I -c '
import json, sys
minor = "go" + sys.argv[1] + "."
print(next((r["version"] for r in json.load(sys.stdin) if r["stable"] and r["version"].startswith(minor)), ""))' "$GO_MINOR") || TOOLCHAIN=""
  [ -n "$TOOLCHAIN" ] || { echo "cannot resolve the newest go$GO_MINOR.x from go.dev; pass --toolchain goX.Y.Z" >&2; exit 1; }
fi
[[ $TOOLCHAIN =~ ^go1\.[0-9]+(\.[0-9]+)?$ ]] || { echo "toolchain must look like go1.X.Y: $TOOLCHAIN" >&2; exit 2; }
export GOTOOLCHAIN=$TOOLCHAIN
GO_VERSION=$(go version | awk '{print $3}')
[ "$GO_VERSION" = "$TOOLCHAIN" ] || { echo "go runs $GO_VERSION, wanted $TOOLCHAIN" >&2; exit 1; }
echo "== toolchain $GO_VERSION for go.mod 'go $GO_MINOR' (installed go: $(GOTOOLCHAIN=local go version | awk '{print $3}'))"

LDFLAGS="-s -w"
if [ "$VERSIONED" = 1 ]; then
  LDFLAGS="$LDFLAGS -X main.version=$TAG -X main.date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR/$APP-linux-"* "$OUT_DIR/SHA256SUMS" "$OUT_DIR/govulncheck."*
for arch in amd64 arm64; do
  echo "== $APP $TAG linux/$arch ($(date +%T), $GO_VERSION)"
  GOOS=linux GOARCH=$arch go -C "$REPO_DIR" build -p "$GO_BUILD_JOBS" -trimpath -ldflags "$LDFLAGS" -o "$OUT_DIR/$APP-linux-$arch" "$PKG"
  gzip -kf "$OUT_DIR/$APP-linux-$arch"
done
(cd "$OUT_DIR" && sha256sum "$APP"-linux-* > SHA256SUMS && cat SHA256SUMS)

# The gate: what the Go vulnerability database knows about the binary just built.
VULN_NOTE="govulncheck: skipped (--no-vulncheck)"
if [ "$VULNCHECK" = 1 ]; then
  echo "== govulncheck -mode=binary $APP-linux-amd64"
  GOFLAGS=-mod=mod go run golang.org/x/vuln/cmd/govulncheck@latest -mode=binary -json "$OUT_DIR/$APP-linux-amd64" > "$OUT_DIR/govulncheck.json"
  set +e
  VULN_NOTE=$(python3 -I - "$OUT_DIR/govulncheck.json" "$OUT_DIR/govulncheck.txt" <<'PY'
import json, sys
raw = open(sys.argv[1]).read(); dec = json.JSONDecoder(); i = 0; osvs = {}; findings = []
while i < len(raw):
    while i < len(raw) and raw[i].isspace(): i += 1
    if i >= len(raw): break
    obj, i = dec.raw_decode(raw, i)
    if "osv" in obj: osvs[obj["osv"]["id"]] = obj["osv"]
    if "finding" in obj: findings.append(obj["finding"])
rows = {}
for f in findings:
    t = f["trace"][0]
    rows.setdefault((f["osv"], t.get("module", "?"), t.get("version", "")), f.get("fixed_version", ""))
stdlib = sorted({k[0] for k in rows if k[1] == "stdlib"})
mods = sorted({k[0] for k in rows if k[1] != "stdlib"})
with open(sys.argv[2], "w") as out:
    for (osv, mod, ver), fixed in sorted(rows.items()):
        line = f"  {osv:16} {mod}@{ver} -> {fixed or '?'}  {osvs.get(osv, {}).get('summary', '')[:70]}"
        print(line, file=sys.stderr); print(line, file=out)
print(f"govulncheck: {len(stdlib)} standard-library, {len(mods)} module finding(s)")
sys.exit(3 if stdlib else (4 if mods else 0))
PY
)
  rc=$?
  set -e
  echo "$VULN_NOTE"
  case $rc in
    0) ;;
    3) echo "standard-library vulnerabilities: $TOOLCHAIN is behind (fixed versions above); refusing the build" >&2; exit 1 ;;
    4) if [ "$STRICT" = 1 ]; then echo "module vulnerabilities and --strict: refusing the build" >&2; exit 1; fi
       echo "module vulnerabilities (above): fork-local go get bumps on a branch, or upstream's next sync" >&2 ;;
    *) echo "govulncheck output could not be read (rc=$rc)" >&2; exit 1 ;;
  esac
fi
echo "BUILD-OK $APP $TAG from $HEAD_SHA with $GO_VERSION -> $OUT_DIR"
[ "$PUBLISH" = 1 ] || exit 0

REPO=$FORK_OWNER/$APP
NOTES=${NOTES_FILE:-}
if [ -z "$NOTES" ]; then
  NOTES=$OUT_DIR/RELEASE-NOTES.md
  printf '%s %s\n\nBuilt from `main` at %s with %s. Linux amd64 + arm64, static, stripped.\n\n%s\n' \
    "$APP" "$TAG" "$HEAD_SHA" "$GO_VERSION" "$VULN_NOTE" > "$NOTES"
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
