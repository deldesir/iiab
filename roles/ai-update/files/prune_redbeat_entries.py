"""Remove RedBeat schedule entries that are no longer in CELERY_BEAT_SCHEDULE.

RedBeat persists every beat entry in valkey/redis and keeps it after the entry
disappears from settings (upstream RapidPro drops tasks between releases, e.g.
fail_old_android_messages in v26.3.61), so the scheduler would keep firing a
task no worker registers. Run via `manage.py shell -c` after settings are
re-rendered; idempotent (no-op when nothing is stale). Set
PRUNE_REDBEAT_DRY_RUN=1 to only report. Internal keys (`redbeat::schedule`, `::lock`, `::statics`) are never
touched, and celery.backend_cleanup (added by celery itself) is kept.
"""
import os

from django.conf import settings
from redbeat import RedBeatSchedulerEntry
from redbeat.schedulers import RedBeatConfig, get_redis

from temba.temba_celery import app

dry_run = os.environ.get("PRUNE_REDBEAT_DRY_RUN") == "1"
# the scheduler normally attaches this at start-up; we run outside the beat process
app.redbeat_conf = RedBeatConfig(app)
prefix = app.redbeat_conf.key_prefix
keep = set(settings.CELERY_BEAT_SCHEDULE) | {"celery.backend_cleanup"}
stale = []
for raw in get_redis(app).scan_iter(prefix + "*"):
    key = raw.decode() if isinstance(raw, bytes) else raw
    name = key[len(prefix):]
    if name.startswith(":") or name in keep:
        continue
    stale.append(key)
    if not dry_run:
        RedBeatSchedulerEntry.from_key(key, app=app).delete()
print(("would prune" if dry_run else "pruned") + f" {len(stale)} stale RedBeat entries: {sorted(stale)}")
