#!/bin/bash
# sync-forks.sh — bring the forks of scripts/forks.tsv up to their upstreams: by merge, never by rebase.
#
#   sync-forks.sh [--pr | --release] [--tests] [--refresh] [--cache DIR] [--forks FILE] [name ...]
#
# For each fork (every fork in forks.tsv by default, our own repositories skipped; names select):
#   1. a private cache clone (never a live tree) fetches the fork's branch and the upstream;
#   2. the target is the upstream branch head (sync = branch) or the newest upstream tag
#      (sync = tag: courier, mailroom and temba shadow upstream's releases);
#   3. the target is MERGED into the fork's branch, --no-ff, PRESERVING the fork's commits: they
#      are the customizations (the Postgres-only mode, the sub-path desk, the WhatsApp channel,
#      the relative assets). A conflict aborts that fork loudly and leaves it untouched: it is a
#      human's decision, resolved in the cache clone and pushed by hand, or left for upstream;
#   4. with --tests, the fork's own suite runs on the merged tree before anything is pushed:
#      go-fork-tests.sh for courier and mailroom, `go test ./...` for wuzapi. temba's suite
#      needs its venv and runs from the live checkout (temba-fork-tests.sh), by hand;
#   5. nothing is pushed by default (DRY RUN). --pr pushes sync-<date> and opens a pull request
#      (the mains are ruleset-protected: this is the normal path); --release pushes the branch.
#
# After a merge lands: a Go fork gets its release (build-go-release.sh <app> <upstream tag>
# --publish) and the pin moves in roles/rapidpro or roles/wuzapi defaults in the same iiab PR;
# the Frappe apps ship as source (bench-upgrade.sh on the boxes); temba deploys from git. The
# order, the checks and the traps are in SYNC-ROUND.md.
#
# --refresh re-clones the cache; --cache DIR (default /opt/iiab/.fork-sync, the former Frappe
# cache: same layout); --forks FILE (default: forks.tsv next to this script).
# Prerequisites: git; gh logged in for --pr; Go at /usr/local/go for --tests on the Go forks.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
FORKS_FILE=${FORKS_FILE:-$HERE/forks.tsv}
CACHE_DIR=${FORK_SYNC_CACHE:-/opt/iiab/.fork-sync}
MODE=dryrun; TESTS=0; REFRESH=0; SELECTED=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) MODE="release" ;;
    --pr)      MODE="pr" ;;
    --tests)   TESTS=1 ;;
    --refresh) REFRESH=1 ;;
    --cache)   CACHE_DIR=$2; shift ;;
    --forks)   FORKS_FILE=$2; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    -*)        echo "Unknown option: $1" >&2; exit 2 ;;
    *)         SELECTED+=("$1") ;;
  esac
  shift
done
[ -f "$FORKS_FILE" ] || { echo "no forks table at $FORKS_FILE" >&2; exit 2; }

# The table: name kind fork branch upstream upstream_branch pin sync notes (tab-separated).
declare -A KIND FORK BRANCH UPSTREAM UPBRANCH SYNC
ORDER=(); LATE=()
while IFS=$'\t' read -r name kind fork branch upstream up_branch _pin sync _notes; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  [[ "$kind" == own* ]] && continue
  KIND[$name]=$kind; FORK[$name]=$fork; BRANCH[$name]=$branch; UPSTREAM[$name]=$upstream; UPBRANCH[$name]=$up_branch; SYNC[$name]=$sync
  case "$name" in frappe|erpnext) LATE+=("$name") ;; *) ORDER+=("$name") ;; esac   # the heavy ones last
done < "$FORKS_FILE"
ORDER+=("${LATE[@]}")
if [[ ${#SELECTED[@]} -gt 0 ]]; then
  for n in "${SELECTED[@]}"; do [[ -v "KIND[$n]" ]] || { echo "Unknown fork: $n (known: ${!KIND[*]})" >&2; exit 2; }; done
  ORDER=("${SELECTED[@]}")
fi

if [[ -t 1 ]]; then R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; Z=$'\e[0m'; else R=; G=; Y=; B=; Z=; fi
say() { printf '%s\n' "$*"; }
hdr() { printf '\n%s== %s ==%s\n' "$B" "$*" "$Z"; }

# git in the cache clone: the ownership guard relaxed (clones are root-owned), merges attributed to the fork owner.
GIT=(git -c safe.directory='*' -c user.name="${GIT_USER_NAME:-Blondel Mondesir}" -c user.email="${GIT_USER_EMAIL:-blondel.md@gmail.com}")

declare -A RESULT
OVERALL_RC=0

# The fork's own tests on the merged tree; prints a line, returns the suite's status.
run_tests() {
  local name=$1 dir=$2
  case "$name" in
    courier)  ONLY=courier  COURIER_DIR=$dir  bash "$HERE/go-fork-tests.sh" ;;
    mailroom) ONLY=mailroom MAILROOM_DIR=$dir bash "$HERE/go-fork-tests.sh" ;;
    wuzapi)   PATH=/usr/local/go/bin:$PATH GOFLAGS=-mod=mod go -C "$dir" test ./... ;;
    *)        say "no suite wired for $name here (a bench or a browser is its test); skipped"; return 0 ;;
  esac
}

sync_one() {
  local name=$1 kind=${KIND[$1]} fork=${FORK[$1]} branch=${BRANCH[$1]} upstream=${UPSTREAM[$1]} up_branch=${UPBRANCH[$1]} sync=${SYNC[$1]}
  local dir="$CACHE_DIR/$name" origin_url="https://github.com/$fork.git" up_url="https://github.com/$upstream.git"
  hdr "$name  ($kind; $fork/$branch <- $upstream/$up_branch, by $sync)"

  [[ $REFRESH -eq 1 ]] && rm -rf "$dir"
  if [[ ! -d "$dir/.git" ]]; then
    say "cloning the fork (blobless partial clone)…"
    rm -rf "$dir"
    "${GIT[@]}" clone -q --filter=blob:none --origin origin "$origin_url" "$dir" 2>/dev/null \
      || { RESULT[$name]="${R}clone failed${Z}"; OVERALL_RC=1; return; }
  fi
  local g=("${GIT[@]}" -C "$dir")
  "${g[@]}" remote add upstream "$up_url" 2>/dev/null || "${g[@]}" remote set-url upstream "$up_url"

  say "fetching origin and upstream…"
  "${g[@]}" fetch -q --filter=blob:none origin "$branch" 2>/dev/null \
    || { RESULT[$name]="${R}the fork has no '$branch' branch${Z}"; OVERALL_RC=1; return; }
  "${g[@]}" fetch -q --filter=blob:none upstream "$up_branch" 2>/dev/null \
    || { RESULT[$name]="${R}upstream has no '$up_branch' branch${Z}"; OVERALL_RC=1; return; }

  local target target_ref
  if [[ $sync == tag ]]; then
    target=$("${g[@]}" ls-remote --tags --refs upstream 'v*' | awk '{print $2}' | sed 's|refs/tags/||' | sort -V | tail -1)
    [[ -n "$target" ]] || { RESULT[$name]="${R}upstream has no v* tags${Z}"; OVERALL_RC=1; return; }
    "${g[@]}" fetch -q --filter=blob:none --no-tags upstream "refs/tags/$target:refs/tags/upstream-$target" 2>/dev/null \
      || { RESULT[$name]="${R}could not fetch upstream tag $target${Z}"; OVERALL_RC=1; return; }
    target_ref="upstream-$target"
  else
    target="$up_branch@$("${g[@]}" rev-parse --short "upstream/$up_branch")"
    target_ref="upstream/$up_branch"
  fi

  "${g[@]}" checkout -q -B "$branch" "origin/$branch"
  local behind ahead
  behind=$("${g[@]}" rev-list --count "$branch..$target_ref")
  ahead=$("${g[@]}" rev-list --count "$target_ref..$branch")
  if [[ $behind -eq 0 ]]; then
    say "${G}up to date with upstream $target${Z} (the fork carries $ahead commit(s) of its own)."
    RESULT[$name]="${G}up-to-date${Z} ($target)"
    return
  fi
  say "$behind upstream commit(s) behind $target; the fork carries $ahead of its own. Test-merging…"
  local merge_out
  if ! merge_out=$("${g[@]}" merge --no-ff --no-edit "$target_ref" 2>&1); then
    local conflicts
    conflicts=$("${g[@]}" diff --name-only --diff-filter=U)
    "${g[@]}" merge --abort 2>/dev/null || true
    if [[ -n "$conflicts" ]]; then
      say "${R}MERGE CONFLICT, left untouched. Conflicted paths:${Z}"
      printf '    %s\n' "$conflicts" | head -20
      RESULT[$name]="${R}CONFLICT ($behind behind, $target)${Z}: resolve in $dir"
    else
      say "${R}MERGE FAILED, left untouched:${Z}"; printf '    %s\n' "$merge_out" | head -5
      RESULT[$name]="${R}merge failed ($behind behind)${Z}"
    fi
    OVERALL_RC=1
    return
  fi
  say "${G}clean merge of $behind upstream commit(s) ($target).${Z}"

  local tests="not run"
  if [[ $TESTS -eq 1 ]]; then
    say "running the fork's tests on the merged tree…"
    if run_tests "$name" "$dir"; then tests="passed"; else
      tests="FAILED"; say "${R}tests failed; nothing pushed for $name.${Z}"
      RESULT[$name]="${R}merged but tests FAILED ($target)${Z}"; OVERALL_RC=1; return
    fi
  fi

  case "$MODE" in
    dryrun)
      say "${Y}DRY RUN: not pushing. --pr opens the pull request, --release pushes the branch.${Z}"
      RESULT[$name]="${Y}mergeable (+$behind, $target, tests $tests)${Z}" ;;
    release)
      if "${g[@]}" push -q origin "$branch" 2>/dev/null; then RESULT[$name]="${G}PUSHED (+$behind, $target)${Z}"
      else RESULT[$name]="${R}push rejected (protected branch? use --pr)${Z}"; OVERALL_RC=1; fi ;;
    pr)
      local sync_branch after=""
      sync_branch="sync-$(date +%Y%m%d)"
      case "$kind" in
        go)     after="After the merge: \`build-go-release.sh $name <tag> --publish\` and the pin in the roles' defaults, in one iiab PR." ;;
        python) after="After the merge: the pipeline box pulls (SYNC-ROUND.md §2: migrations, nanorp_indexes, collectstatic)." ;;
        frappe) after="After the merge: the benches take it with bench-upgrade.sh (canary first)." ;;
        js)     after="After the merge: the box rebuilds the app on its next role run." ;;
      esac
      say "pushing $sync_branch and opening the pull request…"
      if "${g[@]}" push -q -f origin "HEAD:$sync_branch" 2>/dev/null \
         && gh -R "$fork" pr create --base "$branch" --head "$sync_branch" \
              --title "Sync $branch with upstream $target" \
              --body "Merge of $behind upstream commit(s) from $upstream ($target) into the fork's $branch, which carries $ahead commit(s) of its own, all preserved (no rebase). Fork tests on the merged tree: $tests. $after" >/dev/null 2>&1; then
        RESULT[$name]="${G}PR opened (+$behind, $target, tests $tests)${Z}"
      else
        RESULT[$name]="${Y}sync branch pushed; the PR may already exist (+$behind, $target)${Z}"
      fi ;;
  esac
}

mkdir -p "$CACHE_DIR"
say "${B}Fork sync${Z}  mode=$MODE  tests=$TESTS  cache=$CACHE_DIR  table=$FORKS_FILE"
say "forks: ${ORDER[*]}"
for f in "${ORDER[@]}"; do
  sync_one "$f" || { RESULT[$f]="${R}error${Z}"; OVERALL_RC=1; }
done

hdr "summary"
for f in "${ORDER[@]}"; do printf '  %-18s %s\n' "$f" "${RESULT[$f]:-${R}skipped${Z}}"; done
[[ $MODE == dryrun ]] && say "${Y}This was a DRY RUN. Nothing was pushed.${Z}"
exit $OVERALL_RC
