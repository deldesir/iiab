# Sync round — whole-estate upstream sync runbook

One "round" brings every fork (RapidPro suite, wuzapi, the Frappe apps) up to
its upstream, then deploys the result across the fleet. Tools in this folder:
`render-rapidpro-settings.py`, `build-go-release.sh`, `bench-upgrade.sh`,
`bench-smoke.sh`. Never use real hostnames in anything committed: `<box>`.

## 1. Sync the forks (no deploy yet)

- Each fork is synced by **merge** of the upstream branch, never rebase: the
  fork's history is what the boxes run and the release tags shadow upstream's.
- One PR per repo, from a `sync-<date>` branch. Mains are ruleset-protected;
  the operator merges. Nothing below happens until the PRs are merged.
- Read the upstream changelog for settings and migrations; anything that needs
  a role change goes into an `iiab` PR of the same round (templates, version
  pins in the ai-update / wuzapi roles).

## 2. Deploy the pipeline box (RapidPro + gateway)

Order matters; each step is safe to stop after.

1. `sudo -u postgres pg_dump -Fc temba > /library/backups/temba-<date>.dump`
2. `git -C /opt/iiab/iiab checkout master && git pull` (the merged role PRs).
3. Cut the Go releases the roles install from:
   `build-go-release.sh courier <upstream tag> --publish`, same for `mailroom`.
   The temba (web app) release is notes-only; it deploys from git.
4. temba — **every git command as the service user** (`sudo -u iiab-admin`).
   Root-run git leaves root-owned files; the later `chown` strips the
   `www-data` group from `run/` and gunicorn answers 502.
   - `git checkout -- pyproject.toml uv.lock` first: these are the ai-update
     role's standing patches (python constraint widening); the role re-applies
     them. `temba/settings_common.py` is tracked in the fork, so a round's
     settings changes arrive with the pull and show in `git diff` like any
     other file; only `temba/settings.py` is rendered, by the rapidpro role's
     `settings` tag (`render-rapidpro-settings.py` on `settings.py.j2` when a
     new deployment value needs a human eye first).
   - `git pull --ff-only`, then `uv sync`. One-time, on the first pull that
     brings the tracked `temba/settings_common.py`: delete the untracked copy
     the role used to render (`rm temba/settings_common.py`) before pulling,
     and render `settings.py` with the `settings` tag right after, since the
     values that lived in the old copy (internal token, bridge token, mail and
     account policy) now come from there.
   - `manage.py check`; `manage.py showmigrations --plan | grep '\[ \]'` to see
     what will run; `migrate`; `manage.py nanorp_indexes` (the Postgres search
     indexes, idempotent); `collectstatic --noinput` (the front-end resolves
     its URLs against the sub-path at runtime, nothing patches the bundles).
   - `chgrp www-data run && chmod 0770 run`; restart `rapidpro-gunicorn` and
     `rapidpro-celery`.
5. Install the courier/mailroom binaries (`install-release-binary.sh`) and
   restart `rapidpro-courier`, `rapidpro-mailroom`. If the round changed how the
   bridge reaches courier (`WUZAPI_COURIER_URL`, the signing key), run
   `manage.py wuzapi_webhooks` so every WhatsApp channel is re-registered.
6. Realtime sockets (when `rapidpro_centrifugo_enabled`): if the round touched
   `roles/rapidpro/templates/centrifugo-config.json.j2` or the mailroom unit,
   re-apply `--tags centrifugo`. Then confirm a signed-in desk tab still holds
   live subscriptions — `POST http://127.0.0.1:<centrifugo port>/api` with
   `{"method":"channels","params":{}}` and `Authorization: apikey <key>` lists
   them — and that `/var/log/nginx/rapidpro-internal.log` shows the proxy
   calls answering 200. The socket contract (temba `/ti/websockets/*`,
   mailroom `/mi/socket/publish`, the socket namespaces) is where an upstream
   change would first show; read both changelogs for it.

Or simply `./runrole ai-update`, which does steps 3-5 in that order. Use the
manual path when the render/compare step needs a human eye (new settings).

### Zero-token end-to-end check

Inject through courier's EX channel — no WhatsApp, no LLM call:

```
curl -s -X POST "http://127.0.0.1:8080/c/ex/<channel uuid>/receive" \
     -d "from=<address>" -d "text=sync probe"
```

`from` is the bare address, NOT `mailto:<address>` (courier prefixes the
scheme itself; the double prefix creates a bogus URN). Expected: the gateway
log shows `Unauthorized user … — silent drop` for the probe and the outbound
message count is unchanged. Then release the probe contact in RapidPro.

## 3. wuzapi

`build-go-release.sh wuzapi vN --publish` (own version sequence), bump the
role pin, then `./runrole ai-update` installs it. Losing the wuzapi DBs means
re-linking the number: the backup runs before this step.

## 4. Frappe benches

Per box, as root, so the run survives a dropped SSH session:

```
scp scripts/bench-upgrade.sh root@<box>:/root/
ssh root@<box> systemd-run --unit=bench-upgrade --collect --setenv=HOME=/root /bin/bash /root/bench-upgrade.sh
ssh root@<box> 'tail -f /root/bench-upgrade-$(date +%F).log'   # ends UPGRADE-OK / UPGRADE-FAILED
bench-smoke.sh <box> '<date> 00:00:00'
```

Order: canary box first; the idle boxes in parallel; the live tills one at a
time inside their closed window. `bench-smoke.sh` after each box, before the
next. `PREFLIGHT_ONLY=1` on a box first when a fork jumped far.

## 5. Known noise

- With `rapidpro_centrifugo_enabled` off, courier and mailroom log one
  `centrifugo not reachable` error at startup: that port is closed by design;
  not a fault. With it on, that line means Centrifugo is down — check it.
- `bench-upgrade.sh` may retry `migrate` once (metadata-lock wait); a clean
  retry is normal.
- The Error Log count in `bench-smoke.sh` is site-local time; pass `since` in
  that zone.
