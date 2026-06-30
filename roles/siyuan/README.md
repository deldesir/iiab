# SiYuan — IIAB Role

Installs [SiYuan](https://github.com/siyuan-note/siyuan) as a headless
note-taking server using pre-built release binaries from GitHub.

## Architecture

- Downloads the official Linux release tarball
- Extracts only `resources/` (kernel binary + frontend assets), skipping Electron desktop bloat
- Sets `RUN_IN_CONTAINER=true` so the kernel binds `0.0.0.0:6806`, reached through the
  nginx reverse proxy (direct `:6806` is firewalled to non-local clients by iptables)
- Web UI at `https://box/sy/` — localhost / LAN / Tailscale only (see Security model)

## Version selection

`siyuan_version: "latest"` (default) installs the newest **published** release of
any kind — the newest pre-release (beta/rc) when it is more recent than the newest
stable, otherwise the newest stable. Pin an exact version (e.g. `"3.6.5"`) to stay
on stable only.

SiYuan **3.7.0** moved the kernel to a cobra CLI: serving now requires an explicit
`serve` subcommand. The service template detects `>= 3.7.0` and launches
`SiYuan-Kernel serve …` accordingly, so older pins keep working too.

> Upgrading across a minor version (e.g. 3.6 → 3.7) performs a **one-way data
> format upgrade** of `/library/siyuan`. Back up the workspace before re-running
> the role; you cannot downgrade afterwards.

## Security model

SiYuan holds personal notebooks, and **two independent credentials** guard it — do
not confuse them:

- **Lock screen password** (`siyuan_access_auth_code`; called "Access auth code"
  before v3.7.0) — gates the web UI and remote sessions.
- **API token** (`conf.json` `api.token`) — grants **full admin** and is checked
  *before* the lock-screen password, so it **bypasses** it. **SiYuan generates a
  random token** on first run (view/regenerate in Settings → About → API token);
  by default the role leaves it alone. Set `siyuan_api_token` in `local_vars.yml`
  only to have Ansible manage/propagate it to consumers. (Older installs may carry
  a hand-set, guessable value like `iiab-siyuan-token` — rotate it.)

The kernel binds `0.0.0.0:6806` (via `RUN_IN_CONTAINER=true`), so the nginx config
(`siyuan-nginx.conf.j2`) restricts **every** SiYuan location to localhost + LAN +
Tailscale (`allow … ; deny all;`) — matching `ai-gateway.conf` / `wuzapi.conf`.
Without that ACL the `/api/` catch-all would expose the admin API to the public
internet. Keep the ACL unless you deliberately intend to publish personal data.

## Reverse proxy (`/sy`)

SiYuan has no native sub-path support, so `siyuan-nginx.conf.j2` serves it at `/sy/`
and rewrites root-absolute URLs. Three non-obvious requirements keep it robust:
`proxy_set_header Accept-Encoding "";` (SiYuan gzips, and `sub_filter` cannot rewrite
compressed bytes — silent breakage), `proxy_cookie_path / /sy/;`, and `proxy_redirect`
into `/sy/`. Root catch-alls (`/api`, `/ws`, `/check-auth`, assets) handle the URLs
SiYuan's JS builds at runtime without the prefix. The old `/siyuan/` path 301-redirects
to `/sy/`. For a fully collision-free setup, serve SiYuan on its own `server_name`.

## `local_vars.yml` Configuration

```yaml
siyuan_install: True
siyuan_enabled: True
siyuan_access_auth_code: "my-secret-code"   # recommended on shared networks
```

| Variable | Default | Description |
|----------|---------|-------------|
| `siyuan_install` | `False` | Install SiYuan |
| `siyuan_enabled` | `False` | Enable the service |
| `siyuan_version` | `latest` | Release to install — `latest` includes pre-releases (see above); or pin e.g. `3.6.5` |
| `siyuan_dir` | `/opt/iiab/siyuan-server` | Install directory |
| `siyuan_port` | `6806` | HTTP port |
| `siyuan_access_auth_code` | *(empty)* | Auth code (empty = no auth) |
| `siyuan_workspace` | `/library/siyuan` | Data directory (survives reinstalls) |
| `siyuan_user` | `{{ iiab_admin_user }}` | System user |
