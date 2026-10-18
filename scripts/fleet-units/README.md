# Fleet control-plane units (hub)

Copies of the systemd units the hub runs; see `scripts/FLEET.md`.

- `fleet-check.timer` / `fleet-check.service`: nightly `fleet check --all`, then starts
  `fleet-digest.service`. A failing box is a finding, not a unit failure.
- `fleet-digest.service`: runs as the gateway user with the gateway's environment and
  delivers `/var/lib/fleet/latest-digest.txt` through `notify_admin`.
- `uptime-kuma.service`: Uptime Kuma 2.x bound to the tailnet address only
  (`<tailnet-ip>` placeholder), data under `/library/uptime-kuma`.

Install: copy to `/etc/systemd/system/`, `systemctl daemon-reload`,
`systemctl enable --now fleet-check.timer uptime-kuma`.

## Box-side units (installed by `fleet deploy-agent <id>` / `fleet deploy-watchdog <id>`)

- `fleet-agent.timer` / `.service`: hourly report from an offline (push-mode) box to the fleet site.
- `fleet-agent-upgrade.timer` / `.service`: daily, the box fetches the current release manifest and
  upgrades itself inside its maintenance window.
- `fleet-hub-watchdog.timer` / `.service`: every 5 minutes on a non-hub box; after 3 failed probes
  of the hub it asks Contabo for the instance status and sends `start` if it is stopped
  (credentials in `/etc/iiab/fleet-hub-watchdog.conf`, which the operator fills by hand).
