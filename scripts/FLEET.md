# Fleet operations from the hub

How a hub box manages a fleet of Frappe/ERPNext boxes as a service business: one
inventory, named releases, a nightly check with a digest, minute-level monitoring,
and the commands that act on a box. Everything here is generic; names, hosts, plans
and contacts live in the hub's private inventory, never in this repository.

## Pieces

| Piece | Where | What |
|---|---|---|
| Inventory | `/etc/iiab/fleet.yml` (hub, 0640, private) | one record per box: id, host, business, role (hub / till / customer), owner, profile, plan, status, canary rank, maintenance window, contacts, hosting provider, extra units to watch |
| `fleet` | `scripts/fleet`, installed as a copy at `/usr/local/sbin/fleet` | the command: list, status, check, digest, manifest, upgrade, suspend, resume, offboard, push |
| Release manifests | `/etc/iiab/fleet-releases/<id>.manifest` | `app=commit` per app; what "release 2026.10-r8" means |
| `bench-upgrade.sh` | `scripts/bench-upgrade.sh` | per-box upgrade; with `MANIFEST=` pins every app to the release |
| Nightly check | `fleet-check.timer` → `fleet check --all` → `fleet-digest.service` | writes `/var/lib/fleet/checks/<id>/`, the digest, feeds the monitor's push monitor, mirrors into the fleet site, hands the digest to the gateway for delivery |
| Monitor | Uptime Kuma, tailnet-bound, `scripts/fleet-kuma-seed` | HTTP keyword check on the ERP API and the storefront per box, TLS expiry, a push monitor for the nightly check, a status page; alerts go through the email bridge's `/send-json` |
| Fleet site | second Frappe site on the hub bench, tailnet-bound | ERPNext Customer / Subscription / Sales Invoice / Dunning plus the `fleet_ops` app (Managed Box, Fleet Check, Fleet Release, Fleet Subscription) |
| Private config | `/etc/iiab/fleet.conf` (0600) | fleet site URL and API key pair, monitor URL and push token |

## Version 2: levels, catalog, connectivity, runbook console

- A box is **OK / WARN / CRIT / STALE**; findings carry a level. CRIT = service down or data
  at risk (bench target down, a critical probe failing, disk < 5 GB, backup > 72 h, a
  control-plane unit down). WARN = attention this week (TLS < 14 d, domain < 30 d, behind the
  release, failed timer units, error-log rows, disk < 15 GB). STALE = an offline box past its
  reporting allowance. Only CRIT/STALE trip the monitors and exit 1.
- **Catalog** (`scripts/fleet-catalog.yml`): services a box or the hub provides, with
  probes, units, maturity (ready / on-request / candidate) and a billing item. A box lists
  its `services`; probes derive from them. Mirrored into the fleet site as Fleet Service.
- **Connectivity** per box: `public` (probed over https), `tailnet` (probed over http on
  the tailnet address, no TLS/domain checks), `offline` (never polled: `fleet deploy-agent
  <id>` installs `fleet-agent`, which reports hourly to the fleet site, feeds the box's
  push monitor and self-upgrades to the current manifest inside its window).
- **Per-box push monitors** ("<id> check") plus the fleet-wide one.
- **Hub watchdog**: `fleet deploy-watchdog <box>` installs `fleet-hub-watchdog` on another
  box; after 3 failed probes it asks Contabo for the instance status and sends `start` if
  stopped (credentials in `/etc/iiab/fleet-hub-watchdog.conf` on that box).
- **Semaphore UI** (tailnet-bound) runs each runbook as a template with survey questions;
  templates call `scripts/fleet-tasks/*.sh`, which run `fleet` through a sudoers rule.
- `bench-upgrade.sh` now handles every app in `sites/apps.txt`, follows each app's own
  branch, keeps apps absent from the manifest at their deployed commit, and tolerates a
  remote it cannot read when the manifest pins the deployed commit.

## Naming

Every box has three names with different jobs:

- **id** — `box-001`, `box-002`, …: given once by `fleet onboard`, never reused after an
  offboarding. It keys everything: the inventory record, `/var/lib/fleet/checks/<id>/`,
  `/etc/iiab/fleet-vars/<id>.local_vars.yml`, the Managed Box in the fleet site, the
  monitors, the agent and push tokens, and the machine's own hostname.
- **alias** — a short slug people type (`malianova`), unique at any moment but free to
  change when a business rebrands. Output prints `id alias`; monitors are named
  `<id> <alias> ERP api` and so on.
- **host** — the customer's domain. A field, not an identity: it changes when they
  rebrand or let it lapse.

The role (`hub`, `observer`, `till`, `customer`) is a field too, never a name. Every
command accepts an id, an alias or a host.

```
fleet onboard --host shop.example --business "Shop SA" --alias shop   # next free id
fleet rename shop --alias shop-sa                                     # alias change
fleet rename box-004 box-009                                          # id change (rare)
fleet hostname box-004                                                # machine name = id
```

`rename` updates every store in one go (inventory, check history, vars copy, the fleet
site record with its links, the monitors and the per-box push token) and logs the action.
`hostname` runs inside the box's window: `/etc/hosts`, `hostnamectl`, `iiab_hostname` in
its `local_vars.yml` (so IIAB never writes the old name back) and the tailnet hostname.

## Daily use

```
fleet list                      # the inventory
fleet status                    # last check per box
fleet check --all               # run the check now (the timer does it nightly)
fleet digest                    # the text the nightly delivery sends
fleet digest --send             # deliver it now through the gateway
```

A check records app heads, the three bench targets, failed units, Error Log rows since
local midnight, HTTP codes (login, ping, POS, storefront), disk, RAM, the age of the
newest backup on the box and of the hub's copy, TLS days left, the domain's registrar and
expiry (RDAP, cached a day), open POS shifts, and whether the heads match the current
release. Any problem makes the box FAILED; `fleet check` exits 1 and the push monitor goes
down, so a failed nightly check alerts like an outage.

## Releases and upgrades

```
fleet manifest capture --id 2026.10-r8 --from hub --notes "sync round 8"
fleet manifest set-current 2026.10-r8
fleet upgrade --all --preflight         # every box: would it fast-forward cleanly?
fleet upgrade --all                     # canary order, inside each box's window
fleet upgrade box-003 --now             # one box (id, alias or host), ignore the window
fleet upgrade --all --release 2026.09-r7   # roll to an older manifest (only if still a fast-forward)
```

`upgrade` copies `bench-upgrade.sh` and the manifest to the box, runs the upgrade as a
transient systemd unit (it survives a dropped SSH session), follows the log until
`UPGRADE-OK` / `UPGRADE-FAILED`, then runs a check; the rollout stops at the first failure.
Boxes are ordered by `canary_rank`: hub first, our own till second, customer ERP boxes,
the customer till last.

## Lifecycle

```
fleet suspend <id> --reason "invoice overdue 30 days"   # maintenance page on
fleet resume <id>
fleet dunning [--apply]  # overdue invoices from the fleet site: grace, then suspension past grace
fleet rollback <id> [--release ID] [--no-restore] [--dry-run]   # undo the last upgrade (code + data)
fleet offboard <id>      # final backup with files pulled to the hub + HANDOVER.txt; wipe stays manual
fleet push               # mirror inventory, checks and releases into the fleet site
```

`rollback` reads what `upgrade` recorded on the box before changing anything (the commit
each app ran, the backup it took): apps go back to those commits, requirements and assets
are rebuilt, the database and files are restored from that backup. `dunning` turns unpaid
Sales Invoices in the fleet site into findings (WARN while overdue, CRIT past the box's
`grace_days`), moves the status active/grace with the money, and with `--apply` (or
`auto_suspend: true` in the inventory, nightly) suspends past the grace and lifts automatic
suspensions once paid. The backup mailer can also copy every bundle to object storage
(`RCLONE_REMOTE` in its conf).

`suspend` and `offboard` refuse our own boxes. Every action is appended to
`/var/lib/fleet/actions.log`.

## Adding a box

0. On the new machine, the one-line installer (`curl …/iiab-factory/master/install.txt | bash`)
   key (b) prepares a customer box from `vars/local_vars_fleet_box.yml` (it asks for the box
   id, the business name, the domain and the certificate address, and generates the
   passwords); key (h) does the same for a hub from `vars/local_vars_fleet_hub.yml`.
1. `fleet onboard --host <domain> --business "<name>" --alias <slug> [--role till]
   [--billing <email>] [--technical <email>]`: takes the next free id (inventory and the
   fleet site's registry are both consulted), writes the inventory record, mirrors it to
   the fleet site, creates its monitors, pulls its `local_vars.yml` if the box already
   accepts this machine's key, and prints the `iiab_hostname` / `erpnext_business_name`
   lines for the box's `local_vars.yml`.
2. Add its host to `BOXES` in `/etc/iiab/fleet-backup.conf` on the box that runs
   `fleet-backup-mail`, so its backup is pulled and mailed.
3. `fleet check <id>`; in the fleet site, link the Managed Box to its Customer and start
   its Subscription; add its VPS and domain as Fleet Subscription rows.
4. Inside its window: `fleet hostname <id>`.

## Demo box

`scripts/fleet-install/demo-box.yml` is a cloud-init file: `multipass launch 24.04 -c 2 -m 4G
-d 40G -n demo --cloud-init demo-box.yml` builds a throwaway box in a VM on any laptop
(Linux, Windows, macOS). Its `PROFILE` line picks what it becomes: `shop` (ERPNext, POS,
storefront, seeded with ERPNext's demo catalogue by `demo-seed.py`: wizard done, items,
prices, stock, customers, a POS profile, website items) or `hotel` (the same plus the
Hospitality PMS, `vars/local_vars_demo_hotel.yml`, seeded by `demo-hotel-seed.py`: a 12-room
property, room types, rate plans, the posting profile, guests, guests in house with folio
lines, today's arrivals, future and tentative bookings, housekeeping and a maintenance
ticket). Snapshot it once, restore before each demo. It never joins the fleet.
`demo-seed-drill.sh` proves the shop seed on a scratch site of any bench. `sudo iiab` → key
`x` offers the same two profiles on real hardware.

## Installing the control plane on a hub

- `install -m 0755 scripts/fleet /usr/local/sbin/fleet`; same for `scripts/fleet-kuma-seed`
  (copies, not symlinks, so the running hub never depends on which branch the repo is on;
  `fleet upgrade` reads `bench-upgrade.sh` from the repo, or `FLEET_UPGRADE_SCRIPT`)
  (it runs on a small venv: `uv venv /var/lib/fleet/venv && uv pip install --python
  /var/lib/fleet/venv/bin/python "python-socketio[client]" requests pyyaml websocket-client`).
- Units: `fleet-check.service`, `fleet-check.timer`, `fleet-digest.service` (see the
  private operations manual for the unit files; the digest unit runs as the gateway user
  with the gateway's environment and calls `notify_admin`).
- Uptime Kuma 2.x: `git clone -b <tag> https://github.com/louislam/uptime-kuma /opt/uptime-kuma`,
  `npm ci --omit=dev && npm run download-dist`, a dedicated system user, `DATA_DIR` under
  `/library`, bind the tailnet address only, then `fleet-kuma-seed`.
- The fleet site: a second site on the bench with ERPNext and `fleet_ops`, served by a
  tailnet-only nginx listener; a `fleet-bot` user with the Fleet Manager role whose API key
  pair goes into `/etc/iiab/fleet.conf`.
- The email bridge needs `/send-json` for the monitor's alerts.
- Semaphore UI 2.x: binary from its releases to `/usr/local/bin`, a `semaphore` user, a
  Postgres database, `/etc/semaphore/config.json` (dialect, interface = tailnet address,
  port, tmp_path, web_host, the three keys), `semaphore user add --admin`, a sudoers rule
  for `/usr/local/sbin/fleet*`, then seed project, local repository (this repo's path),
  inventory and templates through its API (see the private operations manual).

## Manifest format

```
# fleet release 2026.10-r8 — captured 2026-10-18T08:04:16Z from hub
release=2026.10-r8
captured=2026-10-18T08:04:16Z
notes=sync round 8
frappe=<40-hex commit>
erpnext=<40-hex commit>
...
```

`bench-upgrade.sh` with `MANIFEST=/root/bench-upgrade.manifest` pins each app to its
line; a commit that is not a fast-forward of the deployed one stops the run (rolling back
is a deliberate manual checkout, never an automatic one).
