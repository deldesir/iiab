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
