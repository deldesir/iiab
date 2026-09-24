#!/bin/bash
# go-fork-tests.sh — run the database-backed Go test suites of the mailroom and courier forks on this box.
# Run as root. Idempotent: every setup step checks before it changes anything.
#
#   scripts/go-fork-tests.sh                 # set up (if needed) and run the default packages of both forks
#   RACE=1 scripts/go-fork-tests.sh          # the same with the race detector
#   ONLY=mailroom scripts/go-fork-tests.sh   # one fork (mailroom|courier)
#   SETUP_ONLY=1 scripts/go-fork-tests.sh    # prepare the box, run nothing
#   MAILROOM_DIR=/path COURIER_DIR=/path     # checkouts to test (default: the live trees under /opt/iiab, or a
#                                            # worktree - the services keep running from the live trees regardless)
#   MAILROOM_PKGS="./core/search/..."        # override the package lists (also MAILROOM_MSG_PKGS, COURIER_PKGS)
#   MAILROOM_SKIP='^TestX$'                  # override the skipped-test patterns (also MAILROOM_MSG_SKIP, COURIER_SKIP)
#
# What the suites expect, and how the box provides it:
#   - Postgres at host "postgres" (an /etc/hosts alias of 127.0.0.1, the postgresql-iiab cluster on :5432).
#     mailroom: role mailroom_test/temba, which CREATEs a template database from testsuite/testdata/postgres.dump and
#     clones it per test. The dump runs CREATE EXTENSION vector, and pgvector isn't a trusted extension, so the role
#     has to be SUPERUSER - exactly as mailroom's own CI grants it. courier: role courier_test/temba owning database
#     courier_test, which the suite resets from testsuite/testdata/schema.sql.
#   - Valkey at host "valkey" (an /etc/hosts alias of 127.0.0.2). The suites FLUSHDB databases 0 and 16-31, so
#     they must never see the live instance on 127.0.0.1 (16 databases): a dedicated instance runs on 127.0.0.2:6379
#     with 32 databases as the transient unit go-test-valkey, nothing persisted.
#   - Elasticsearch, DynamoDB and S3 are not deployed here. The forks' test suites run without them when
#     MAILROOM_TEST_NANORP / COURIER_TEST_NANORP is set: searches go to Postgres (the fork's mode), tests that
#     assert on what those services hold are skipped, and mailroom's template gets the fork's nanorp_indexes
#     additions (testsuite/testdata/nanorp.sql).
#   - "temba" is the upstream test password, nothing else is secret.
#
# The run ends with a per-package summary and exits non-zero if any package failed.
set -uo pipefail

export PATH=/usr/local/go/bin:$PATH HOME=${HOME:-/root} GOFLAGS=-mod=mod

MAILROOM_DIR=${MAILROOM_DIR:-/opt/iiab/mailroom}
COURIER_DIR=${COURIER_DIR:-/opt/iiab/courier}
# mailroom's web/msg package runs on its own because its skip list would otherwise catch web/contact's TestSearch
MAILROOM_PKGS=${MAILROOM_PKGS:-"./core/search/... ./web/contact/... ./runtime/... ./cmd/... ./core/knowledge/..."}
MAILROOM_MSG_PKGS=${MAILROOM_MSG_PKGS:-"./web/msg/..."}
COURIER_PKGS=${COURIER_PKGS:-"./handlers/wuzapi/... ./core/models/... ./runtime/..."}

# Upstream tests that can only mean something with the services on, skipped by name:
#   - bodies that drive Elasticsearch, DynamoDB or S3 directly (rt.ES.Client, rt.Dynamo.History, rt.S3.Client ...)
#     rather than through the runtime's gates: TestDeindexContacts, TestPruneContacts,
#     TestResolveRecipientsIndexesExcludedCreatedContacts, TestDeindex, TestResendClearsPreviousStatus,
#     courier's TestModelsSuite. With the services off they dereference nil and the panic takes the rest of the
#     package's tests down with it.
#   - TestGetContactUUIDsForQuery: one case asserts that a contact created after indexing is invisible to an OR
#     query, i.e. index lag - in Postgres it's found.
#   - web/msg TestSearch: its snapshot holds the bare events the DynamoDB test helper writes, not the msg_received
#     and msg_created events the Postgres search returns.
# The helpers the other tests go through skip or no-op by themselves. Anything else that fails is worth a look.
MAILROOM_SKIP=${MAILROOM_SKIP:-'^TestDeindexContacts$|^TestPruneContacts$|^TestResolveRecipientsIndexesExcludedCreatedContacts$|^TestDeindex$|^TestGetContactUUIDsForQuery$'}
MAILROOM_MSG_SKIP=${MAILROOM_MSG_SKIP:-'^TestResendClearsPreviousStatus$|^TestSearch$'}
COURIER_SKIP=${COURIER_SKIP:-'^TestModelsSuite$'}
ONLY=${ONLY:-}
RACE=${RACE:-}
SETUP_ONLY=${SETUP_ONLY:-}
LOG_DIR=${LOG_DIR:-/var/tmp/go-fork-tests}

TEST_VALKEY_IP=127.0.0.2
TEST_VALKEY_UNIT=go-test-valkey

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "ERROR: $*" >&2; exit 2; }

[[ $(id -u) -eq 0 ]] || die "run as root"
command -v go >/dev/null || die "go not found in PATH (expected /usr/local/go/bin)"
command -v pg_restore >/dev/null || die "pg_restore not found (postgresql-client)"
command -v valkey-server >/dev/null || die "valkey-server not found"

psql_admin() { sudo -u postgres psql -p 5432 -v ON_ERROR_STOP=1 -X -Atq "$@"; }

# --- /etc/hosts aliases ------------------------------------------------------------------------------------------
ensure_host_alias() { # ip name comment
	if grep -qE "^\s*$1\s+$2(\s|$)" /etc/hosts; then
		return
	fi
	if grep -qE "^\s*[0-9.:]+\s+.*\b$2\b" /etc/hosts; then
		die "/etc/hosts already maps $2 to something other than $1 - fix it by hand"
	fi
	log "adding $1 $2 to /etc/hosts"
	printf '%s %s   # %s\n' "$1" "$2" "$3" >> /etc/hosts
}
ensure_host_alias 127.0.0.1 postgres "Go test suites"
ensure_host_alias $TEST_VALKEY_IP valkey "Go test suites (dedicated test instance, 32 databases)"

# --- dedicated test Valkey ---------------------------------------------------------------------------------------
if ! systemctl is-active --quiet $TEST_VALKEY_UNIT; then
	log "starting $TEST_VALKEY_UNIT on $TEST_VALKEY_IP:6379"
	systemd-run --unit=$TEST_VALKEY_UNIT --collect --quiet \
		--description="Valkey for the Go fork test suites ($TEST_VALKEY_IP:6379, 32 databases)" \
		/usr/bin/valkey-server --bind $TEST_VALKEY_IP --port 6379 --databases 32 --save "" --appendonly no --dir /var/tmp --loglevel warning \
		|| die "could not start $TEST_VALKEY_UNIT"
	sleep 1
fi
[[ $(valkey-cli -h $TEST_VALKEY_IP -p 6379 CONFIG GET databases | tail -1) == 32 ]] || die "test valkey on $TEST_VALKEY_IP:6379 doesn't have 32 databases"

# --- Postgres roles and databases --------------------------------------------------------------------------------
psql_admin -c "SELECT 1" >/dev/null || die "postgresql-iiab not reachable on :5432"

if [[ $(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = 'mailroom_test'") != 1 ]]; then
	log "creating role mailroom_test (SUPERUSER: the test dump runs CREATE EXTENSION vector)"
	psql_admin -c "CREATE ROLE mailroom_test LOGIN PASSWORD 'temba' SUPERUSER CREATEDB"
elif [[ $(psql_admin -c "SELECT rolsuper FROM pg_roles WHERE rolname = 'mailroom_test'") != t ]]; then
	log "granting SUPERUSER to mailroom_test (the test dump runs CREATE EXTENSION vector)"
	psql_admin -c "ALTER ROLE mailroom_test WITH SUPERUSER CREATEDB"
fi

if [[ $(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = 'courier_test'") != 1 ]]; then
	log "creating role courier_test"
	psql_admin -c "CREATE ROLE courier_test LOGIN PASSWORD 'temba' CREATEDB"
fi
if [[ $(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = 'courier_test'") != 1 ]]; then
	log "creating database courier_test"
	psql_admin -c "CREATE DATABASE courier_test OWNER courier_test"
fi

PGPASSWORD=temba psql -h postgres -U mailroom_test -d postgres -Atqc "SELECT 1" >/dev/null || die "mailroom_test can't connect to host postgres"
PGPASSWORD=temba psql -h postgres -U courier_test -d courier_test -Atqc "SELECT 1" >/dev/null || die "courier_test can't connect to host postgres"

if [[ -n $SETUP_ONLY ]]; then
	log "setup complete"
	exit 0
fi

# --- run -----------------------------------------------------------------------------------------------------------
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
RACE_FLAG=${RACE:+-race}
status=0

run_suite() { # name dir env pkgs skip extra-go-test-flags
	local name=$1 dir=$2 env=$3 pkgs=$4 skip=$5 flags=$6
	local logf="$LOG_DIR/$name-$STAMP.log"
	[[ -d $dir ]] || { log "$name: $dir not found, skipping"; return; }
	log "$name: go test -v $flags $RACE_FLAG -skip '$skip' $pkgs (in $dir, $env=1) -> $logf"
	(cd "$dir" && env "$env=1" go test -count=1 -v $flags $RACE_FLAG -skip "$skip" $pkgs) > "$logf" 2>&1
	local rc=$?
	echo
	echo "== $name ($dir)"
	# one line per package, the failing tests, and how many tests skipped themselves for want of a service
	grep -E "^(ok|FAIL|\?)\s" "$logf" | sed 's/^/   /'
	grep -E "^\s*--- FAIL" "$logf" | sed 's/^\s*/   /'
	echo "   skipped (service switched off): $(grep -cE '^\s*--- SKIP' "$logf")   passed: $(grep -cE '^\s*--- PASS' "$logf")"
	echo "   exit=$rc  (log: $logf)"
	[[ $rc -eq 0 ]] || status=1
}

case $ONLY in
	"" | mailroom)
		run_suite mailroom "$MAILROOM_DIR" MAILROOM_TEST_NANORP "$MAILROOM_PKGS" "$MAILROOM_SKIP" ""
		run_suite mailroom-msg "$MAILROOM_DIR" MAILROOM_TEST_NANORP "$MAILROOM_MSG_PKGS" "$MAILROOM_MSG_SKIP" ""
		;;
esac
case $ONLY in
	# courier's suite shares one database and one valkey database between packages, so packages run one at a time
	"" | courier) run_suite courier "$COURIER_DIR" COURIER_TEST_NANORP "$COURIER_PKGS" "$COURIER_SKIP" "-p=1" ;;
esac

echo
if [[ $status -eq 0 ]]; then
	log "GO-FORK-TESTS-OK"
else
	log "GO-FORK-TESTS-FAILED: see the logs above"
fi
exit $status
