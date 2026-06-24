#!/usr/bin/env bash
#
# update_rapidpro_binaries.sh — sync IIAB forks with upstream, build, release.
#
# For each fork (deldesir/{courier,mailroom,wuzapi,rapidpro}) this:
#   1. Fetches the latest upstream RELEASE and merges it into the fork's `main`,
#      PRESERVING fork customizations. Merge conflicts ABORT loudly — they are
#      never auto-resolved (that would risk clobbering your customizations).
#      Uncommitted changes in the fork also abort (use --stash to wrap them).
#   2. Builds stripped, static, reproducible Go binaries for linux/amd64 and
#      linux/arm64. No UPX by default (it decompresses into RAM at startup,
#      which hurts the RAM-constrained, swapless edge targets, and trips AV).
#      See --compress for size options.
#   3. Publishes a GitHub release with the binaries + a SHA256SUMS manifest so
#      `ai-update` can download and (optionally) verify them.
#
# SAFETY: by default this is a DRY RUN. It fetches, test-merges, and builds to
# prove everything works, then UNDOES the local merge and makes NO pushes or
# releases. Pass --release to actually commit the merge, push `main`, push the
# tag, and publish. Nothing reaches GitHub or the devices without --release.
#
# Usage:
#   ./update_rapidpro_binaries.sh                       # dry-run, all repos
#   ./update_rapidpro_binaries.sh courier mailroom      # dry-run, a subset
#   ./update_rapidpro_binaries.sh --release             # publish, all repos
#   ./update_rapidpro_binaries.sh --release --compress gzip courier
#   ./update_rapidpro_binaries.sh --stash rapidpro      # stash a dirty tree
#
# Options:
#   --release            Actually publish (merge, push main+tag, gh release).
#   --compress MODE      Release-asset compression: none (default) | gzip | upx.
#                          none: raw stripped binary  (ai-update works as-is)
#                          gzip: <bin>.gz             (smallest DOWNLOAD, no RAM
#                                cost; REQUIRES the matching ai-update change)
#                          upx : in-place packed bin  (smallest on disk; RAM +
#                                startup + AV cost — opt-in, not recommended)
#   --stash              git stash a dirty fork tree, merge, then stash pop.
#   --force              Build/publish even if already synced to upstream.
#   -h | --help          Show this help.
#
# Prerequisites: git, go >= 1.21 (for Go repos), and in --release mode an
# authenticated `gh` CLI (run `gh auth login` in your own terminal — never
# paste a token into automated input).

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
GO_BIN="${GO_BIN:-/usr/local/go/bin/go}"
GH_USER="${GH_USER:-deldesir}"
read -r -a ARCHES <<< "${BIN_ARCHES:-amd64 arm64}"

# repo -> local working directory (build wuzapi from its source dir, NOT the
# live deploy dir /opt/iiab/wuzapi which holds the running binary).
declare -A REPO_DIR=(
  [rapidpro]=/opt/iiab/rapidpro
  [courier]=/opt/iiab/courier
  [mailroom]=/opt/iiab/mailroom
  [wuzapi]=/opt/iiab/wuzapi-src
)
# repo -> upstream remote URL (added automatically if the remote is missing).
declare -A UPSTREAM_URL=(
  [rapidpro]=https://github.com/nyaruka/rapidpro.git
  [courier]=https://github.com/nyaruka/courier.git
  [mailroom]=https://github.com/nyaruka/mailroom.git
  [wuzapi]=https://github.com/asternic/wuzapi.git
)
# repo -> Go main package ("" = no Go binary: sync + tag + release notes only).
declare -A MAIN_PKG=(
  [rapidpro]=""
  [courier]=./cmd/courier
  [mailroom]=./cmd/mailroom
  [wuzapi]=.
)
# Upstreams that publish semantic vMAJOR.MINOR.PATCH release tags. wuzapi's
# upstream (asternic) does not, so we sync it to upstream/main and bump the
# fork's own semver.
declare -A HAS_SEMVER_UPSTREAM=(
  [rapidpro]=1 [courier]=1 [mailroom]=1 [wuzapi]=0
)

# ── CLI parsing ───────────────────────────────────────────────────────────────
RELEASE=0
STASH=0
FORCE=0
COMPRESS="none"
TARGETS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)  RELEASE=1 ;;
    --stash)    STASH=1 ;;
    --force)    FORCE=1 ;;
    --compress) COMPRESS="${2:-}"; shift ;;
    -h|--help)  sed -n '2,48p' "$0"; exit 0 ;;
    -*)         echo "Unknown option: $1" >&2; exit 2 ;;
    *)          TARGETS+=("$1") ;;
  esac
  shift
done
case "$COMPRESS" in none|gzip|upx) ;; *) echo "Invalid --compress: $COMPRESS" >&2; exit 2 ;; esac
[[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=(courier mailroom wuzapi rapidpro)

# ── Logging / state ───────────────────────────────────────────────────────────
DIST="$(mktemp -d "${TMPDIR:-/tmp}/iiab-binrel.XXXXXX")"
declare -a SUMMARY=()
CUR_DIR=""        # repo dir currently being processed (for the cleanup trap)
CUR_STASHED=0     # whether we stashed in CUR_DIR

c_blue=$'\e[1;34m'; c_grn=$'\e[1;32m'; c_yel=$'\e[1;33m'; c_red=$'\e[1;31m'; c_off=$'\e[0m'
log()  { echo "${c_blue}==>${c_off} $*"; }
ok()   { echo "${c_grn}  ✓${c_off} $*"; }
warn() { echo "${c_yel}  ! ${c_off}$*" >&2; }
die()  { echo "${c_red}ERROR:${c_off} $*" >&2; exit 1; }

cleanup() {
  local rc=$?
  # If we died mid-merge in a repo, leave the tree clean.
  if [[ -n "$CUR_DIR" && -f "$CUR_DIR/.git/MERGE_HEAD" ]]; then
    warn "aborting in-progress merge in $CUR_DIR"
    git -C "$CUR_DIR" merge --abort 2>/dev/null || true
  fi
  if [[ "$CUR_STASHED" == "1" ]]; then
    warn "restoring stashed changes in $CUR_DIR"
    git -C "$CUR_DIR" stash pop 2>/dev/null || warn "stash pop failed — inspect 'git -C $CUR_DIR stash list'"
  fi
  rm -rf "$DIST"
  exit $rc
}
trap cleanup EXIT

# ── Preflight ─────────────────────────────────────────────────────────────────
preflight() {
  command -v git >/dev/null || die "git not found"
  local need_go=0
  for r in "${TARGETS[@]}"; do [[ -n "${MAIN_PKG[$r]:-}" ]] && need_go=1; done
  if [[ $need_go -eq 1 ]]; then
    [[ -x "$GO_BIN" ]] || die "go not found at $GO_BIN (set GO_BIN=...)"
    ok "go: $("$GO_BIN" version)"
  fi
  if [[ "$COMPRESS" == "upx" ]]; then
    command -v upx >/dev/null || die "--compress upx requested but upx is not installed"
    warn "UPX packing requested: smaller on disk, but costs RAM + startup time on edge targets."
  fi
  if [[ $RELEASE -eq 1 ]]; then
    command -v gh >/dev/null || die "--release needs the gh CLI"
    gh auth status >/dev/null 2>&1 || die "gh is not authenticated — run 'gh auth login' in your own terminal"
    ok "gh authenticated"
  else
    log "DRY RUN (no commits / pushes / releases). Pass --release to publish."
  fi
}

# ── Git helpers ───────────────────────────────────────────────────────────────
ensure_remotes() {
  local dir="$1" repo="$2"
  [[ -d "$dir/.git" ]] || die "$dir is not a git repo (expected the $repo fork checkout)"
  local branch; branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
  [[ "$branch" == "main" || "$branch" == "master" ]] || die "$repo: on branch '$branch', expected main/master"
  if ! git -C "$dir" remote get-url upstream >/dev/null 2>&1; then
    log "$repo: adding upstream remote ${UPSTREAM_URL[$repo]}"
    git -C "$dir" remote add upstream "${UPSTREAM_URL[$repo]}"
  fi
}

guard_or_stash() {
  local dir="$1" repo="$2"
  # Only tracked modifications matter for a merge; ignore untracked files.
  if [[ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]]; then
    if [[ $STASH -eq 1 ]]; then
      log "$repo: stashing dirty tree before merge"
      git -C "$dir" stash push -m "update_rapidpro_binaries autostash" >/dev/null
      CUR_STASHED=1
    else
      warn "$repo has uncommitted changes — refusing to merge over them:"
      git -C "$dir" status --porcelain --untracked-files=no | sed 's/^/      /' >&2
      die "$repo: commit/gitignore these (recommended) or re-run with --stash"
    fi
  fi
}

# Latest upstream semantic release tag, e.g. v26.1.130 (network query).
latest_upstream_semver() {
  git -C "$1" ls-remote --tags upstream 'v*' \
    | awk '{print $2}' | sed 's#refs/tags/##' \
    | grep -vE '\^\{\}$' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -V | tail -1
}

# Compute the fork's next release tag for a nyaruka-tracking repo.
nyaruka_tag() {
  local dir="$1" repo="$2" base="$3" n=1 cand="$3"
  tag_taken() { git -C "$dir" rev-parse -q --verify "refs/tags/$1" >/dev/null 2>&1 \
                || { [[ $RELEASE -eq 1 ]] && gh release view "$1" -R "$GH_USER/$repo" >/dev/null 2>&1; }; }
  while tag_taken "$cand"; do cand="${base}-iiab${n}"; n=$((n+1)); done
  echo "$cand"
}

# Bump the fork's own semver (wuzapi).
wuzapi_next_tag() {
  local dir="$1" latest ma mi pa
  latest="$(git -C "$dir" tag | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
  [[ -z "$latest" ]] && latest="v0.0.0"
  IFS=. read -r ma mi pa <<< "${latest#v}"
  echo "v${ma}.${mi}.$((pa+1))"
}

human_size() { numfmt --to=iec --suffix=B "$(stat -c %s "$1")" 2>/dev/null || stat -c %s "$1"; }

# ── Build / package ───────────────────────────────────────────────────────────
build_and_package() {
  local repo="$1" dir="$2" pkg="$3" out outdir="$DIST/$repo"
  mkdir -p "$outdir"
  for arch in "${ARCHES[@]}"; do
    out="$outdir/${repo}-linux-${arch}"
    log "$repo: building linux/$arch (stripped, trimpath, static)"
    # No '|| true': a build failure MUST abort before we publish anything.
    ( cd "$dir" && CGO_ENABLED=0 GOOS=linux GOARCH="$arch" \
        "$GO_BIN" build -trimpath -mod=readonly -ldflags="-s -w" -o "$out" "$pkg" )
    ok "$repo/$arch stripped: $(human_size "$out")"
    case "$COMPRESS" in
      gzip) gzip -9 -f "$out"; ok "$repo/$arch gzip:     $(human_size "$out.gz")" ;;
      upx)  upx --best --lzma -q "$out" >/dev/null; ok "$repo/$arch upx:      $(human_size "$out")" ;;
    esac
  done
  ( cd "$outdir" && sha256sum "${repo}"-linux-* > SHA256SUMS ) && ok "$repo: SHA256SUMS written"
}

# ── Per-repo pipeline ─────────────────────────────────────────────────────────
process_repo() {
  local repo="$1"
  [[ -n "${REPO_DIR[$repo]:-}" ]] || die "unknown repo: $repo"
  local dir="${REPO_DIR[$repo]}" pkg="${MAIN_PKG[$repo]:-}"
  CUR_DIR="$dir"; CUR_STASHED=0
  echo; log "${c_grn}$repo${c_off}  ($dir)"

  ensure_remotes "$dir" "$repo"
  log "$repo: fetching origin + upstream"
  git -C "$dir" fetch --quiet --no-tags origin
  git -C "$dir" fetch --quiet --no-tags upstream

  # Resolve the sync target.
  local target_ref target_desc
  if [[ "${HAS_SEMVER_UPSTREAM[$repo]}" == "1" ]]; then
    local ver; ver="$(latest_upstream_semver "$dir")"
    [[ -n "$ver" ]] || die "$repo: could not determine latest upstream semver tag"
    local sha; sha="$(git -C "$dir" ls-remote upstream "refs/tags/${ver}^{}" | awk '{print $1}')"
    [[ -n "$sha" ]] || sha="$(git -C "$dir" ls-remote upstream "refs/tags/${ver}" | awk '{print $1}')"
    git -C "$dir" cat-file -e "${sha}^{commit}" 2>/dev/null || git -C "$dir" fetch --quiet --no-tags upstream "$sha"
    target_ref="$sha"; target_desc="${UPSTREAM_URL[$repo]%.git}@${ver}"
    log "$repo: latest upstream release is $ver"
  else
    target_ref="upstream/main"; target_desc="${UPSTREAM_URL[$repo]%.git}@main"
  fi

  guard_or_stash "$dir" "$repo"

  # Already synced?
  if git -C "$dir" merge-base --is-ancestor "$target_ref" HEAD 2>/dev/null && [[ $FORCE -eq 0 ]]; then
    SUMMARY+=("$repo: already synced with $target_desc — skipped (use --force to rebuild)")
    ok "$repo: already up to date with $target_desc"
    [[ "$CUR_STASHED" == "1" ]] && { git -C "$dir" stash pop >/dev/null; CUR_STASHED=0; }
    CUR_DIR=""
    return
  fi

  # Test-merge into the working tree WITHOUT committing (verify before publish).
  local merged=0
  if ! git -C "$dir" merge-base --is-ancestor "$target_ref" HEAD 2>/dev/null; then
    log "$repo: merging $target_desc (preserving fork customizations)"
    if ! git -C "$dir" merge --no-commit --no-ff "$target_ref" >/dev/null 2>&1; then
      git -C "$dir" --no-pager diff --name-only --diff-filter=U | sed 's/^/      CONFLICT: /' >&2 || true
      git -C "$dir" merge --abort 2>/dev/null || true
      die "$repo: merge with $target_desc CONFLICTS. Resolve in $dir, commit, then re-run."
    fi
    merged=1
  fi

  # Build + package (fatal on failure).
  if [[ -n "$pkg" ]]; then
    build_and_package "$repo" "$dir" "$pkg"
  else
    log "$repo: no Go binary (Python app) — sync + tag + release notes only"
  fi

  if [[ $RELEASE -eq 0 ]]; then
    # Undo the working-tree merge; this run changes nothing.
    [[ $merged -eq 1 ]] && git -C "$dir" merge --abort 2>/dev/null || true
    [[ "$CUR_STASHED" == "1" ]] && { git -C "$dir" stash pop >/dev/null; CUR_STASHED=0; }
    local _what="(no binary)"; [[ -n "$pkg" ]] && _what="binaries"
    SUMMARY+=("$repo: DRY-RUN ok — would sync to $target_desc and build $_what")
    CUR_DIR=""
    return
  fi

  # ── Publish ──────────────────────────────────────────────────────────────
  [[ $merged -eq 1 ]] && git -C "$dir" commit --no-verify -m "merge: sync $repo with upstream $target_desc"
  [[ "$CUR_STASHED" == "1" ]] && { git -C "$dir" stash pop >/dev/null; CUR_STASHED=0; }

  local tag
  if [[ "${HAS_SEMVER_UPSTREAM[$repo]}" == "1" ]]; then
    tag="$(nyaruka_tag "$dir" "$repo" "$(latest_upstream_semver "$dir")")"
  else
    tag="$(wuzapi_next_tag "$dir")"
  fi

  log "$repo: pushing main and tag $tag"
  git -C "$dir" push origin HEAD
  git -C "$dir" tag -a "$tag" -m "$repo $tag (synced with $target_desc)"
  git -C "$dir" push origin "$tag"

  local notes="Automated fork sync with ${target_desc}. Fork customizations preserved via merge."
  if gh release view "$tag" -R "$GH_USER/$repo" >/dev/null 2>&1; then
    [[ -n "$pkg" ]] && gh release upload "$tag" "$DIST/$repo"/* --clobber -R "$GH_USER/$repo"
  else
    if [[ -n "$pkg" ]]; then
      gh release create "$tag" "$DIST/$repo"/* --title "$tag" --notes "$notes" --latest -R "$GH_USER/$repo"
    else
      gh release create "$tag" --title "$tag" --notes "$notes" --latest -R "$GH_USER/$repo"
    fi
  fi
  SUMMARY+=("$repo: RELEASED $tag${pkg:+ (+${COMPRESS} binaries + SHA256SUMS)}")
  ok "$repo: released $tag"
  CUR_DIR=""
}

# ── Main ──────────────────────────────────────────────────────────────────────
preflight
for repo in "${TARGETS[@]}"; do process_repo "$repo"; done

echo; log "Summary"
for line in "${SUMMARY[@]}"; do echo "   • $line"; done
[[ $RELEASE -eq 0 ]] && echo && warn "This was a dry run. Re-run with --release to publish."
