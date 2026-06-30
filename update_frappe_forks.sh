#!/usr/bin/env bash
#
# update_frappe_forks.sh — sync deldesir/* Frappe-app forks with upstream.
#
# Companion to update_rapidpro_binaries.sh, but for the Frappe/ERPNext apps the
# erpnext role installs. Unlike the Go services, Frappe apps ship as *source*,
# not binaries — there is nothing to compile or release. The "release" is simply
# the updated fork branch that `bench get-app` / the erpnext role pulls on the
# next deploy. So this script only does step 1 of the binaries flow: sync.
#
# For each fork this:
#   1. Fetches the upstream deploy branch and MERGES it into the fork's deploy
#      branch, PRESERVING fork customizations. Merge conflicts ABORT that fork
#      loudly and leave it untouched — conflicts are never auto-resolved (that
#      would risk clobbering the deldesir customizations baked into the fork).
#   2. Reports a per-fork summary (up-to-date / merged N commits / CONFLICT).
#
# SAFETY: by default this is a DRY RUN. It fetches and test-merges in a private
# cache clone (never your live frappe-bench app dirs), reports, and makes NO
# pushes. Choose how to publish:
#   --release   commit the merge and push the fork's branch directly.
#   --pr        push the merge to a sync branch and open a pull request instead
#               (use when the fork's deploy branch is protected by a ruleset).
#
# Usage:
#   ./update_frappe_forks.sh                     # dry-run, all forks
#   ./update_frappe_forks.sh builder webshop     # dry-run, a subset
#   ./update_frappe_forks.sh --release           # publish (push branch), all
#   ./update_frappe_forks.sh --pr builder        # open a PR for one fork
#   ./update_frappe_forks.sh --refresh frappe    # re-clone the cache from scratch
#
# Options:
#   --release       Push the merged deploy branch to the fork (origin).
#   --pr            Open a PR from a sync branch instead of pushing the branch.
#   --refresh       Delete and re-clone the cache working copy before syncing.
#   --cache DIR     Cache location (default: /opt/iiab/.fork-sync).
#   -h | --help     Show this help.
#
# Prerequisites: git, and for --release / --pr an authenticated `gh` CLI
# (this box is already logged in as deldesir). Never paste a token into input.

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
GH_USER="${GH_USER:-deldesir}"
CACHE_DIR="${FORK_SYNC_CACHE:-/opt/iiab/.fork-sync}"

# deldesir fork repo  ->  "<upstream-url> <deploy-branch>"
# The fork is always github.com/$GH_USER/<key>; the key matches the repo name.
declare -A FORKS=(
  [frappe]="https://github.com/frappe/frappe.git develop"
  [erpnext]="https://github.com/frappe/erpnext.git develop"
  [builder]="https://github.com/frappe/builder.git develop"
  [webshop]="https://github.com/frappe/webshop.git develop"
  [crm]="https://github.com/frappe/crm.git develop"
  [hrms]="https://github.com/frappe/hrms.git develop"
  [POSNext]="https://github.com/BrainWise-DEV/POSNext.git develop"
  [builder-cli]="https://github.com/stravo1/builder-cli.git main"
)
# Default order (heaviest repos last so quick wins/conflicts surface first).
# builder-cli is intentionally excluded from the default set: it is our own tool
# with history unrelated to stravo1/builder-cli, so it is not an upstream-tracking
# fork. Pass it explicitly (`update_frappe_forks.sh builder-cli`) if ever needed.
DEFAULT_ORDER=(builder webshop crm hrms POSNext erpnext frappe)

# ── Arg parsing ───────────────────────────────────────────────────────────────
MODE="dryrun"   # dryrun | release | pr
REFRESH=0
SELECTED=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) MODE="release" ;;
    --pr)      MODE="pr" ;;
    --refresh) REFRESH=1 ;;
    --cache)   CACHE_DIR="$2"; shift ;;
    -h|--help) sed -n '2,48p' "$0"; exit 0 ;;
    -*)        echo "Unknown option: $1" >&2; exit 2 ;;
    *)
      [[ -v "FORKS[$1]" ]] || { echo "Unknown fork: $1 (known: ${!FORKS[*]})" >&2; exit 2; }
      SELECTED+=("$1") ;;
  esac
  shift
done
[[ ${#SELECTED[@]} -eq 0 ]] && SELECTED=("${DEFAULT_ORDER[@]}")

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; Z=$'\e[0m'
else R=; G=; Y=; B=; Z=; fi
say()  { printf '%s\n' "$*"; }
hdr()  { printf '\n%s== %s ==%s\n' "$B" "$*" "$Z"; }

# git in the cache clone, with the ownership guard relaxed (clones are root-owned)
# and merge commits attributed to the fork owner rather than root.
GIT=(git -c safe.directory='*'
     -c user.name="${GIT_USER_NAME:-Blondel Mondesir}"
     -c user.email="${GIT_USER_EMAIL:-blondel.md@gmail.com}")

# ── Per-fork sync ─────────────────────────────────────────────────────────────
declare -A RESULT  # fork -> human-readable outcome
OVERALL_RC=0

sync_one() {
  local fork="$1"
  local spec="${FORKS[$fork]}"
  local up_url up_branch
  up_url="${spec% *}"; up_branch="${spec##* }"
  local origin_url="https://github.com/${GH_USER}/${fork}.git"
  local dir="${CACHE_DIR}/${fork}"

  hdr "$fork  (origin: ${GH_USER}/${fork}  upstream: ${up_url##*/}  branch: ${up_branch})"

  [[ $REFRESH -eq 1 ]] && rm -rf "$dir"
  if [[ ! -d "$dir/.git" ]]; then
    say "cloning fork (blobless partial clone)…"
    rm -rf "$dir"
    "${GIT[@]}" clone --filter=blob:none --origin origin "$origin_url" "$dir" >/dev/null 2>&1 \
      || { RESULT[$fork]="${R}clone failed${Z}"; OVERALL_RC=1; return; }
  fi

  local g=("${GIT[@]}" -C "$dir")
  "${g[@]}" remote add upstream "$up_url" 2>/dev/null || "${g[@]}" remote set-url upstream "$up_url"

  say "fetching origin + upstream…"
  if ! "${g[@]}" fetch --quiet --filter=blob:none origin "$up_branch" 2>/dev/null; then
    RESULT[$fork]="${R}fork has no '$up_branch' branch${Z}"; OVERALL_RC=1; return
  fi
  if ! "${g[@]}" fetch --quiet --filter=blob:none upstream "$up_branch" 2>/dev/null; then
    RESULT[$fork]="${R}upstream has no '$up_branch' branch${Z}"; OVERALL_RC=1; return
  fi

  # Reset the working branch to the fork's current published state.
  "${g[@]}" checkout -q -B "$up_branch" origin/"$up_branch"

  local behind
  behind="$("${g[@]}" rev-list --count "${up_branch}..upstream/${up_branch}")"
  if [[ "$behind" -eq 0 ]]; then
    say "${G}already up to date with upstream.${Z}"
    RESULT[$fork]="${G}up-to-date${Z}"
    return
  fi
  say "fork is ${behind} upstream commit(s) behind; test-merging…"

  local merge_out
  if ! merge_out="$("${g[@]}" merge --no-edit --no-ff upstream/"$up_branch" 2>&1)"; then
    local conflicts
    conflicts="$("${g[@]}" diff --name-only --diff-filter=U)"
    "${g[@]}" merge --abort 2>/dev/null || true
    if [[ -n "$conflicts" ]]; then
      say "${R}MERGE CONFLICT — left untouched. Conflicted paths:${Z}"
      printf '    %s\n' $conflicts | head -20
      RESULT[$fork]="${R}CONFLICT (${behind} behind)${Z} — resolve manually"
    else
      say "${R}MERGE FAILED — left untouched:${Z}"
      printf '    %s\n' "$merge_out" | head -5
      RESULT[$fork]="${R}merge failed (${behind} behind)${Z} — see output"
    fi
    OVERALL_RC=1
    return
  fi

  local merged_log
  merged_log="$("${g[@]}" log --oneline origin/"$up_branch"..HEAD | head -8)"
  say "${G}clean merge of ${behind} upstream commit(s).${Z}"

  case "$MODE" in
    dryrun)
      say "${Y}DRY RUN — not pushing. Re-run with --release or --pr to publish.${Z}"
      RESULT[$fork]="${Y}mergeable (+${behind}, dry-run)${Z}"
      ;;
    release)
      say "pushing merged '${up_branch}' to origin…"
      if "${g[@]}" push origin "$up_branch" >/dev/null 2>&1; then
        RESULT[$fork]="${G}RELEASED (+${behind})${Z}"
      else
        RESULT[$fork]="${R}push rejected (branch protected? try --pr)${Z}"; OVERALL_RC=1
      fi
      ;;
    pr)
      local sync_branch="upstream-sync-$(date +%Y%m%d)"
      say "pushing sync branch '${sync_branch}' and opening PR…"
      if "${g[@]}" push -q -f origin "HEAD:${sync_branch}" 2>/dev/null \
         && gh -R "${GH_USER}/${fork}" pr create --base "$up_branch" --head "$sync_branch" \
              --title "Sync ${up_branch} with upstream ($(date +%Y-%m-%d))" \
              --body "Automated upstream merge of ${behind} commit(s) from ${up_url}. Generated by update_frappe_forks.sh." \
              >/dev/null 2>&1; then
        RESULT[$fork]="${G}PR opened (+${behind})${Z}"
      else
        RESULT[$fork]="${Y}sync branch pushed; PR may already exist (+${behind})${Z}"
      fi
      ;;
  esac
}

# ── Run ───────────────────────────────────────────────────────────────────────
mkdir -p "$CACHE_DIR"
say "${B}Frappe fork sync${Z}  mode=${MODE}  cache=${CACHE_DIR}"
say "forks: ${SELECTED[*]}"
for f in "${SELECTED[@]}"; do
  sync_one "$f" || { RESULT[$f]="${R}error${Z}"; OVERALL_RC=1; }
done

hdr "summary"
for f in "${SELECTED[@]}"; do
  printf '  %-14s %s\n' "$f" "${RESULT[$f]:-${R}skipped${Z}}"
done
[[ $MODE == dryrun ]] && say "\n${Y}This was a DRY RUN. No forks were modified on GitHub.${Z}"
exit $OVERALL_RC
