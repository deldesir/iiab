# Installing the fleet control plane on a hub

The recipes that built the hub on 2026-10-18, generic. Run as root, in this order; each is
idempotent and prints no secrets (generated passwords go to /etc/iiab/fleet.conf, 0600).

1. `uptime-kuma-install.sh` — Uptime Kuma 2.x natively under /opt/uptime-kuma, data in
   /library/uptime-kuma. Then install `fleet-units/uptime-kuma.service` (tailnet bind) and
   run `fleet-kuma-seed`.
2. `fleet-site-create.sh` — the fleet.local site on the hub bench (DB + user via the MariaDB
   socket, `bench new-site --no-setup-db`, ERPNext). Then `bench --site fleet.local
   install-app fleet_ops`, the setup wizard (`setup_complete` with your company), the
   tailnet nginx listener, and `fleet-site-apiuser.sh` for the fleet-bot API user.
3. `semaphore-install.sh` — Semaphore UI binary, Postgres database on the IIAB cluster,
   config.json, admin user, sudoers rule for the fleet commands, tailnet-only unit. Then
   `semaphore-seed.py` (project, local repository, templates); `semaphore-run.py "<template>"`
   runs one from the command line.
4. `restore-drill.sh <host>` — restores that box's newest hub copy onto a scratch site,
   proves ping and login, removes it, prints the timing. Run quarterly.

Each script logs to /var/lib/fleet/<name>.log. The fleet command itself, the nightly units
and the seeder live in scripts/ and scripts/fleet-units/.

Demo and training: `demo-box.yml` (cloud-init for a Multipass VM), `demo-seed.py` (wizard +
ERPNext's demo catalogue + prices, stock, POS profile, website items; idempotent) and
`demo-seed-drill.sh` (proves the seed on a scratch site, then drops it).
