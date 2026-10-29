# Fleet control-plane units (hub)

Copies of the systemd units the hub runs; see `scripts/FLEET.md`.

- `fleet-check.timer` / `fleet-check.service`: nightly `fleet check --all`, then `fleet digest --send`:
  through `fleet-digest.service` where the gateway runs, else through the fleet site's outgoing
  Email Account to `DIGEST_TO` in `/etc/iiab/fleet.conf`. A failing box is a finding, not a unit failure.
- `fleet-digest.service`: runs as the gateway user with the gateway's environment and
  delivers `/var/lib/fleet/latest-digest.txt` through `notify_admin`.
- `fleet-backup-pull.timer` / `.service`: daily (07:30 Haiti) mirror of the observer's backup
  copies onto the hub (`fleet backup-pull`).
- `fleet-export.timer` / `.service`: nightly (03:00 Haiti) control-plane export, the hub's own
  backup; the observer's `fleet-backup-mail` pulls and mails the newest bundle.
- `uptime-kuma.service`: Uptime Kuma 2.x bound to the tailnet address only
  (`<tailnet-ip>` placeholder), data under `/library/uptime-kuma`.

Install: copy to `/etc/systemd/system/`, `systemctl daemon-reload`,
`systemctl enable --now fleet-check.timer fleet-backup-pull.timer fleet-export.timer` (and
`uptime-kuma` where the monitor runs).

## Box-side units (installed by `fleet deploy-agent <id>` / `fleet deploy-watchdog <id>`)

- `fleet-agent.timer` / `.service`: hourly report from an offline (push-mode) box to the fleet site.
  The metrics come from `scripts/fleet-probe`, installed beside the agent: the same file the hub
  sends over ssh to the boxes it polls.
- `fleet-agent-upgrade.timer` / `.service`: daily, the box fetches the current release manifest and
  upgrades itself inside its maintenance window.
- `fleet-hub-watchdog.timer` / `.service`: every 5 minutes on a non-hub box; after 3 failed probes
  of the hub it asks Contabo for the instance status and sends `start` if it is stopped
  (credentials in `/etc/iiab/fleet-hub-watchdog.conf`, which the operator fills by hand).

## fleet-mirror (observer)

`fleet-mirror.service` + `.timer`: nightly bare mirrors of every repository in
`/etc/iiab/fleet-forks.txt` (shape: `scripts/fleet-forks.sample.txt`) under `/library/mirrors`,
so a hosting outage or an account problem never blocks a rebuild. Install the script to
`/usr/local/sbin/fleet-mirror`, run `gh auth setup-git` once as root for the private
repositories, then `systemctl enable --now fleet-mirror.timer`. `fleet-mirror --check` reports
missing or stale mirrors for the nightly check.
