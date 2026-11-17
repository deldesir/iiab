# Sync round — whole-estate upstream sync runbook

One "round" brings every fork (RapidPro suite, wuzapi, the Frappe apps) up to
its upstream, then deploys the result across the fleet. Tools in this folder:
`render-rapidpro-settings.py`, `build-go-release.sh`, `bench-upgrade.sh`,
`bench-smoke.sh`. Never use real hostnames in anything committed: `<box>`.

## 1. Sync the forks (no deploy yet)

- Each fork is synced by **merge** of the upstream branch, never rebase: the
  fork's history is what the boxes run and the release tags shadow upstream's.
  The fork's own commits are its philosophy (the Postgres-only mode, the
  sub-path desk, the WhatsApp channel, the relative assets): a conflict is a
  decision, never an auto-resolution.
- `scripts/sync-forks.sh` does it for every fork of `scripts/forks.tsv`: a
  private cache clone, the upstream branch head (or the newest upstream tag
  for courier, mailroom and temba) merged `--no-ff`, conflicts aborted and
  reported, `--tests` running the fork's suite on the merged tree, and a dry
  run unless `--pr` (one PR per repo from a `sync-<date>` branch) or
  `--release` (a push, for the unprotected branches). `sync-forks.sh courier
  mailroom --tests` before a round tells what the round will be.
- Mains are ruleset-protected; the operator merges. Nothing below happens
  until the PRs are merged.
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
   The script builds with the newest patch release of the toolchain go.mod
   names and refuses a binary in which govulncheck finds standard-library
   findings; module findings are listed (a fork-local `go get` on a branch, or
   upstream's next sync). A rebuild without a source change takes the `-N`
   suffix (v26.3.120-1). **Bump `rapidpro_courier_release` and
   `rapidpro_mailroom_release` in roles/rapidpro/defaults in the same iiab PR:**
   the pins are what a fresh install and the next ai-update after a source pull
   put on disk, and round 11 left them two releases behind what ran.
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
   restart `rapidpro-courier`, `rapidpro-mailroom`. If the round moved mailroom
   to a new goflow release (`go.mod`), re-apply `--tags mailroom-docs` so the
   flow editor's completion docs match. If the round changed how the
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
role pin (`wuzapi_release`, same PR), then `./runrole ai-update` installs it.
The service runs `{{ wuzapi_dir }}/wuzapi`; `/usr/local/bin/wuzapi` is only a
symlink on Android, so scan and back up the binary the unit names
(`systemctl show -p ExecStart --value wuzapi`). Losing the wuzapi DBs means
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

## 4b. Abort or roll back a bench

`bench-upgrade.sh` records the app commits and the backup it took in
`/root/bench-upgrade.last.env` before it changes anything. To undo the last upgrade on a box:

```
ssh root@<box> systemd-run --unit=bench-rollback --collect --setenv=HOME=/root /bin/bash /opt/iiab/iiab/scripts/bench-rollback.sh
ssh root@<box> 'tail -f /root/bench-rollback-$(date +%F).log'
```

Apps return to the recorded commits, requirements and assets are rebuilt, the database and
files are restored from that backup (code and data go back together). `MANIFEST=file` targets
a release manifest instead; `RESTORE=0` keeps the current database (code-only). Run
`bench-smoke.sh` afterwards, and re-run the round on that box only when the cause is known.

## 5. Frappe apps: what the 2026-10 round taught

- `crm` (develop) hard-requires the `whatsapp` app (`required_apps`, a linked
  `@whatsapp/ui` package, patches importing `whatsapp.*`). The erpnext role
  installs it before crm; an existing bench needs a one-time
  `bench get-app whatsapp --branch develop` + `bench --site <site> install-app
  whatsapp`, and `bench-upgrade.sh` fast-forwards it from upstream like payments.
- `install-app` on a running bench leaves the web workers with the old app list:
  the site answers 500 until the `frappe-bench-*` units (minus redis) are
  restarted. `bench-upgrade.sh` restarts them itself; restart by hand after an
  install done ahead of it.
- `POS Settings` exists in both erpnext and pos_next; pos_next syncs last and
  wins, so ERPNext's own fields live in pos_next's DocType JSON (POSNext#32).
- The desk under the sub-path: upstream code keeps adding bare `/desk` and
  `/app` spellings. The fork spells the prefix at the source (frappe#11:
  `frappe.boot.subpath_prefix`, the router's `desk_path`, `subpath.js` edge
  net). After a frappe sync, drive the desk in a headless browser on one box and
  check that `location.pathname` keeps the prefix after `frappe.set_route(...)`
  (a list, a workspace, a form, a cross-shell route), that no `a[href^="/desk"]`
  is left on the page, and that no 30x hops are seen for `/api/method` or
  `/website_script.js` during a desk session. Builder page data scripts run in
  Builder's own sandbox (no `frappe.call`); the storefront's `/store` script
  uses the sandbox helpers and lives in the storefront repo as
  `data_script.py`.

## 6. Known noise

- With `rapidpro_centrifugo_enabled` off, courier and mailroom log one
  `centrifugo not reachable` error at startup: that port is closed by design;
  not a fault. With it on, that line means Centrifugo is down — check it.
- `bench-upgrade.sh` may retry `migrate` once (metadata-lock wait); a clean
  retry is normal.
- The Error Log count in `bench-smoke.sh` is site-local time; pass `since` in
  that zone.

## 7. What the 2026-10-18 round taught

- temba `LLM_TYPES` model definitions are dicts since v26.3.144
  (`{"max_output_tokens": N, "roles": "TGC"}`); a bare integer fails the
  startup assertion in `temba/ai/types/__init__.py`. The fork's `custom_ai`
  entry in `temba/settings_common.py` follows the new shape; the rendered
  `settings.py` does not define `LLM_TYPES`, so nothing to re-render.
  v26.3.140 dropped the Anthropic LLM type (and the `anthropic` dependency).
- `users.0024_lowercase_emails` / `orgs.0193_lowercase_invitation_emails`
  rewrite emails in place: check for pairs that differ only by case before
  migrating (`select lower(email), count(*) … having count(*) > 1`).
- Flow spec 14.7.0: temba v26.3.138+ and mailroom on goflow 0.300.0 go out
  together; the editor refuses revisions from an older minor spec.
- hermes-agent upstream re-numbered its tags (`v2026.9.24` → `v0.22.x`); the
  `v0.22.2` tag descends from `v2026.9.24`. Bump the pin only after the staged
  test (scratch gateway copy, `uv sync` against a worktree of the tag, the 3
  patches applied, gateway tests green).
- Running `git` as root inside the service-user checkouts (`/opt/iiab/rapidpro`,
  `wuzapi`, `hermes-agent`, `mempalace`, `rivebot`) leaves root-owned refs,
  packs and index files; the next role run or `sudo -u iiab-admin git` then
  fails with "dubious ownership" or permission errors. Fetch and merge as the
  owner (`sudo -u iiab-admin git -C …`), and after any root-run git:
  `chown -R iiab-admin:iiab-admin <repo>/.git`.
- POSNext upstream renamed the Governorate/District DocTypes to
  `POS Governorate` / `POS District` (patch `v2_4_0.rename_locations_doctypes`,
  idempotent, runs at migrate). frappe upstream's new `mobile_nav.js` spells
  bare `/desk/…` routes; the fork's `subpath.js` edge net prefixes them at
  runtime, the headless desk check (§5) is what proves it.
- mempalace 3.11 records the embedder that built a palace and refuses a
  mismatch; a palace with no recorded model opens with the configured one
  (`MEMPALACE_EMBEDDING_MODEL=minilm` here) and warns. Its test suite needs
  `hypothesis` and `scipy`, which the gateway venv does not carry, and
  `mempalace/__init__.py` strips every `PYTHONPATH` entry from `sys.path`, so
  the environment variable is useless: `uv pip install --python <venv python>
  --target <dir> hypothesis scipy` (then delete the `numpy*` it drags into
  `<dir>`, the venv's own must win), make `<dir>` readable by the service user,
  and run `sudo -u iiab-admin … python -m pytest tests -o "pythonpath=. <dir>"`
  from the worktree. Four permission-gate tests fail under root by design.

## 8. What the 2026-11-07 review taught

- The role pins lagged the releases (courier v26.3.64 / mailroom v26.3.102
  pinned, v26.3.68 / v26.3.120 running): a pin left behind is a downgrade
  waiting for the next source pull. The pin moves in the release's PR.
- `GOTOOLCHAIN=auto` builds with exactly the release go.mod names, so the
  round's binaries were go1.27.0 while go1.27.3 was out: 14 standard-library
  CVEs per binary. `build-go-release.sh` now resolves the newest patch release
  and gates on govulncheck; the fixed binaries are the `-1` rebuilds.
- Compiling mailroom on the pipeline box needs one package at a time and a
  tight collector (`GO_BUILD_JOBS`, `GOGC` in the script): three parallel
  compiles of the elasticsearch client got OOM-killed.
- Dependabot alerts are off on every fork (GitHub's default), so nothing on
  GitHub watches the dependencies of the code that runs. `supply-check`
  (roles/ai-gateway/files, daily 08:30 Haiti after the health check) does:
  govulncheck on the binaries the units run, pip-audit on the service venvs,
  npm audit on the lock files, the forks of `scripts/forks.tsv` against their
  upstreams and pins, Dependabot alerts on our own repositories. CRIT fails the
  unit (notify-failure mails it); the report is mailed on Mondays and kept in
  /var/lib/supply-check/. Tolerated advisories go in /etc/iiab/supply-check.accept.
- Our own repositories: Dependabot (the `uv` ecosystem, one grouped PR a week) on
  rivebot, jwlinker, speechcraft, playbooks and builder-cli (npm). The gateway and
  talkmaster depend on private siblings by path, which Dependabot cannot resolve:
  `deps-refresh` (roles/ai-gateway/files, Sundays 06:00 Haiti) clones them, runs
  `uv lock --upgrade`, the tests and pip-audit, and opens the `deps-<date>` pull
  request (a draft when the tests fail). Merging one is the usual pull + `uv sync
  --frozen`. pip-audit on a lock:
  `uv export --frozen --no-hashes --no-emit-project -o req.txt` (drop the
  path/editable lines) then `pip-audit --disable-pip --no-deps -r req.txt`;
  on a venv: `uv pip freeze --python <venv>/bin/python`. Go binaries:
  `govulncheck -mode=binary <binary>` (`go run golang.org/x/vuln/cmd/govulncheck@latest`).
- email-bridge ships as files inside its role (the repository is archived): a
  lock refresh reaches a box only through `./runrole email-bridge --reinstall`;
  a plain `runrole` skips an installed service.
- rivebot's venv ran Python 3.10 (end of life 2026-10) from root's uv
  directory: `rivebot_python` pins 3.14 and roles/uv-service recreates the venv.
- Units start with `uv run --no-sync` (the email-bridge template lacked it): a
  service start must never re-resolve the lock.

## 9. What the 2026-11-15 round taught

- `sync-forks.sh` dry run first, `--tests` on the Go forks, and the four
  conflicts of the round were each a fork customisation meeting an upstream
  change in the same lines (courier's Dynamo guard and its x/net bump,
  temba's sub-path imports, mempalace's `MEMPALACE_KG_PATH`, the installer's
  Galaxy retry). Resolve in the cache clone, commit the merge, test, push the
  `sync-<date>` branch and open the PR by hand; the script does not touch a
  resolved merge.
- **A clean merge can still drop a customisation.** Upstream split
  mempalace's `mcp_server.py` into a package and the fork's 500-drawer cap
  vanished without a conflict. After every merge, `git grep` the fork's own
  commits' distinctive lines in the merged tree (`git log upstream..HEAD` for
  the list) before trusting the summary.
- A clean merge can also stop satisfying upstream's contracts: courier's test
  harness now refuses a send that reports no cost (v26.3.76), so the WhatsApp
  bridge handler needed `res.AddCost(1)`. The fork suites are the gate.
- Two fork suites must not run at once: `go-fork-tests.sh` drops the test
  roles when it ends, and the other run then fails with "password
  authentication failed for user courier_test". A `[build failed]` with no
  compiler output is the compiler OOM-killed by other builds on the box.
- temba before the binaries this time: mailroom v26.3.138+ and courier
  v26.3.81+ write channel tallies to `channels_channeltally`, which temba
  migration 0224 creates. The round's 17 migrations include backfills that
  lock source rows; run them at a quiet hour, `showmigrations --plan` first.
- `deps-refresh` syncs every repository with `--all-extras`: the gateway's
  talk-preparation extra is what its suite imports (81 errors otherwise).
- hermes-agent v0.23.1 staged the runbook way (worktree + the three patches +
  a gateway clone locked against it): green, pinned here. The gateway's lock
  moves in its own PR; deploy order is the hermes checkout, then the lock.
- Centrifugo 6.9.6 → 6.10.3: stricter client-protocol JSON parsing (official
  SDKs unaffected), `temporary` on server API errors, no configuration change
  for the role's keys; confirm the socket contract (§2.6) after the restart.

### What the deploy of that round added (2026-11-17)

- **A fork that jumps many upstream releases at once can hit a migration order upstream never
  runs.** temba v26.3.172 → .196 brought 21 migrations; `flows.0418_backfill_flow_tallies` reads
  `orgs_tallyflush`, created by `orgs.0198_tallyflush`, and declares no dependency on it, so the
  plan put the backfill first and it failed on the missing table. Upstream deploys one release at a
  time, where 0198 is already applied. Read `showmigrations --plan` for backfills whose SQL names
  another app's new table, and apply that table's migration first (`manage.py migrate orgs
  0198_tallyflush`, then `migrate`). A failed RunPython is rolled back; the plan resumes cleanly.
- **The gateway lock pins its path dependencies by version.** mempalace moved to 3.12.0 on disk and
  `uv lock --check` in the gateway failed until the lock was refreshed, although no dependency
  changed. After every path-dependency sync (hermes, mempalace, talkmaster, jwlinker), run
  `uv lock --check` in a scratch clone of the gateway and ship the refreshed lock in the round.
- **Upstream temba moved the login page to `/accounts/login/`**; `/users/login/` is a 404 under the
  sub-path, not a broken desk. Probe `/rp/accounts/login/`.
- **The service user cannot pull a private repository over HTTPS** (talkmaster): root holds the
  credentials, the tree belongs to the service user, and a root-run pull leaves root-owned files
  (23 in talkmaster, 413 in the gateway, found and chowned back this round). Fetch as root into a
  bare mirror under `/opt/iiab/.fork-sync/<repo>.git`, chown it to the service user, and pull from
  the mirror as the service user. A deploy key for the service user would remove the detour.
- **Look at the fork's CI after a merge.** `fork-checks` on deldesir/iiab had been red since
  2026-11-12 (four task names in `roles/anki` with a template in the middle, the production
  profile's `name[template]`), and nothing surfaced it until this round. The GitHub token had also
  expired, which stops releases and pull requests cold: `gh auth status` belongs at the start of a
  round.
- **The zero-token probe (§2) no longer reaches the gateway from the EX channel.** Workspace 1's
  catch-all trigger has been inactive since 2026-11-04; "sync probe" matches no keyword, so the
  message is handled without a flow (`msgs_msg.flow_id` null) and nothing is sent. The probe still
  proves courier → mailroom → database on the new binaries. The gateway leg is covered by the
  gateway suite and its health endpoint, or by a keyword the probe contact is allowed to use.
- **organized-app deploys with `./runrole organized --reinstall`.** The role's git task (force
  clone at `organized_app_version`, then `npm ci`, the Vite env, a fresh `dist`) lives in its
  install path; a plain `./runrole organized` on an installed box only re-renders and restarts, and
  the app stays at its old commit with the old `dist`. `ai-update` does not cover this app. Back up
  `dist` first (`tar -C /opt/iiab/organized-app -czf … dist`): the role removes it before the build.
- **An app that follows its own upstream can outrun the fork that bundles it.** CRM's frontend
  build compiles the whatsapp app's UI sources through the `@framework/ui` alias. whatsapp's
  develop of 2026-11-16 (2c3a3d5) started importing `getSocketInstance`, which no frappe revision
  exports there, and the CRM build on the hub stopped (`UPGRADE-FAILED: build`) with the apps
  already fast-forwarded and the migration not yet run. Fix: a manifest that pins whatsapp one
  commit earlier (62e2909) and a `MANIFEST=` rerun, which the script treats as a no-op move for
  the apps already in place. When a fork sync moves CRM or frappe, build CRM's frontend on the
  canary with the whatsapp commit the manifest will carry (`cd apps/crm/frontend && yarn build`)
  before the fleet rollout; the preflight only checks fast-forwards, not builds.
- **Upstream frappe now renders templates in its sandbox.** `render_safe_globals()` gives templates
  a `frappe.utils` that is the `VALID_UTILS` subset of `frappe.utils.data`; a fork helper a template
  calls (`get_url_prefix()` in `base.html`, the sub-path) must be listed there or every website
  page answers 500 with "There was an error while rendering this template" (deldesir/frappe
  `subpath-template-prefix`). The desk, POS and the Vue apps keep working, so a ping-only check
  passes: probe `/erp/login` on the canary before capturing the release. The hub ran the fix
  commit (fast-forwarded on its `develop`) until the fork merged it.
