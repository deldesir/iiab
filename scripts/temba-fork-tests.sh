#!/bin/bash
# Runs the temba (RapidPro web app) test suite on this box against Postgres only: no Elastic, no DynamoDB, no S3,
# no mailroom. Django's runner needs a settings module, so this writes the ignored temba/settings_local.py from the
# dev settings plus the local services, creates the test database once (the runner keeps it), and runs the modules
# you name, or the fork's own tests when you name none.
#
#   scripts/temba-fork-tests.sh                        # the fork's tests
#   scripts/temba-fork-tests.sh temba.flows.tests      # any module(s), as manage.py test takes them
#   scripts/temba-fork-tests.sh --all                  # the whole suite (hours; Dynamo/S3 cases fail by design)
#
# Run as the service user (the checkout is theirs): sudo -u iiab-admin scripts/temba-fork-tests.sh …
set -euo pipefail

RAPIDPRO_DIR="${RAPIDPRO_DIR:-/opt/iiab/rapidpro}"
TEST_DB="${TEST_DB:-test_temba}"
FORK_TESTS=(
  temba.channels.types.wuzapi.tests
  temba.mailroom.tests.test_event_postgres
  temba.utils.tests.test_url_tags
)

cd "$RAPIDPRO_DIR"
export PATH="$HOME/.local/bin:$PATH"

if [ ! -f temba/settings.py ]; then
  echo "temba/settings.py is missing: render it with the rapidpro role's settings tag first" >&2
  exit 1
fi

# the database password lives in the rendered settings; the test settings never repeat it
DB_PASSWORD=$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("temba/settings.py").read_text()
db = text[text.index("DATABASES"):]
print(re.search(r'"PASSWORD":\s*"([^"]*)"', db).group(1))
PY
)

cat > temba/settings_local.py <<PY
# written by iiab/scripts/temba-fork-tests.sh - the test settings for this box, ignored by git
from .settings_dev import *  # noqa

_db = DATABASES["default"]
_db.update({"HOST": "127.0.0.1", "PORT": "5432", "PASSWORD": "${DB_PASSWORD}"})
DATABASES = {"default": _db, "readonly": _db.copy()}

_valkey = "redis://127.0.0.1:6379/10"
CACHES["default"]["LOCATION"] = _valkey
CELERY_BROKER_URL = _valkey

# local-first: no DynamoDB, no S3, no Elastic, and no mailroom on the test port
DYNAMO_TABLE_PREFIX = ""
DYNAMO_ENDPOINT_URL = None
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "archives": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "public": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MAILROOM_URL = "http://127.0.0.1:9"
PY

# the test database, created once with the extensions the migrations expect (they need a superuser, so creating
# it is a root step); --keepdb then reuses it across runs
db_exists() {
  PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U temba -d postgres -Atc \
    "select 1 from pg_database where datname='${TEST_DB}'" 2>/dev/null | grep -q 1
}
if ! db_exists; then
  if sudo -n -u postgres true 2>/dev/null; then
    echo "creating ${TEST_DB}"
    sudo -n -u postgres createdb -O temba "${TEST_DB}"
    for ext in postgis hstore uuid-ossp pg_trgm vector; do
      sudo -n -u postgres psql -q -d "${TEST_DB}" -c "create extension if not exists \"${ext}\";"
    done
  else
    cat >&2 <<MSG
${TEST_DB} does not exist and this user cannot create it. As root, once:
  sudo -u postgres createdb -O temba ${TEST_DB}
  for ext in postgis hstore uuid-ossp pg_trgm vector; do sudo -u postgres psql -d ${TEST_DB} -c "create extension if not exists \"\$ext\";"; done
MSG
    exit 1
  fi
fi

if [ "${1:-}" = "--all" ]; then
  shift
  set -- "$@"
elif [ $# -eq 0 ]; then
  set -- "${FORK_TESTS[@]}"
fi

exec uv run python manage.py test --settings=temba.settings_local --keepdb --noinput "$@"
