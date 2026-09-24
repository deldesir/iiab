# RapidPro — IIAB Role

Installs [RapidPro](https://github.com/deldesir/rapidpro) flow engine,
Courier message transport, and Mailroom background worker.

## Settings

`temba/settings_common.py` is tracked in the fork and arrives with the code, so
an upstream release's settings changes merge like any other file. Everything
that belongs to one deployment - the secret key, the database password, the
bridge and internal-API tokens, hostnames, storage, mail policy, the sub-path -
is rendered by this role into the ignored `temba/settings.py` from
`settings.py.j2`. After changing one of those values in `local_vars.yml`,
re-render that file alone with the `settings` tag:

```bash
cd /opt/iiab/iiab && ansible-playbook -i ansible_hosts run-one-role.yml --connection=local \
  --extra-vars '{"role_to_run":"rapidpro"}' --tags settings
```

Two management commands the role runs for you, and that you can run by hand:
`nanorp_indexes` creates the Postgres indexes contact and message search use
without Elasticsearch (idempotent, after every migrate), and `wuzapi_webhooks`
re-registers every WhatsApp channel's webhook and signing key with the bridge.

## `local_vars.yml` Configuration

```yaml
rapidpro_install: True
rapidpro_enabled: True
rapidpro_secret_key: "your-django-secret"
rapidpro_admin_password: "your-admin-password"
```

### AWS vs Local-First Mode

By default, RapidPro uses **AWS S3 + DynamoDB** for file storage and event
history. Set `rapidpro_aws_enabled: false` to disable all AWS dependencies
and use local filesystem + PostgreSQL only:

```yaml
# Standard AWS mode (default):
rapidpro_aws_enabled: true
rapidpro_aws_access_key_id: "AKIA..."
rapidpro_aws_secret_access_key: "..."
rapidpro_aws_region: "us-east-1"
rapidpro_s3_bucket_prefix: "my-bucket"
rapidpro_dynamo_table_prefix: "Temba"

# Local-first mode (no AWS dependencies):
rapidpro_aws_enabled: false
# All AWS variables below are ignored.
```

| Feature | `rapidpro_aws_enabled: true` | `rapidpro_aws_enabled: false` (Nanorp Mode) |
|---------|------|-------|
| File storage | AWS S3 (`S3Boto3Storage`) | Local filesystem (`FileSystemStorage`) |
| DynamoDB | Real AWS DynamoDB | Disabled — events stored in PostgreSQL |
| Elasticsearch | Full-text contact/msg search | Disabled — uses PostgreSQL `pg_trgm` and `tsvector` |
| Courier attachments | Uploaded to S3 bucket | Saved to `/opt/iiab/rapidpro/media/` |
| Cloud Services setup | DynamoDB migrations run | Skipped. Legacy spool explicitly purged. |

> **Note:** The same highly optimized UPX-compressed Go binaries (courier/mailroom) support both modes natively. The
> toggle only changes which environment variables are passed via systemd.

### All Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `rapidpro_install` | `False` | Install RapidPro |
| `rapidpro_enabled` | `False` | Enable the service |
| `rapidpro_aws_enabled` | `true` | Use AWS S3 + DynamoDB (set `false` for local-only) |
| `rapidpro_db_name` | `temba` | PostgreSQL database name |
| `rapidpro_db_user` | `temba` | PostgreSQL user |
| `rapidpro_db_pass` | `temba` | PostgreSQL password |
| `admin_email` | `admin@box.lan` | Admin email for login |
| `rapidpro_admin_password` | *(empty)* | Admin password override |
| `rapidpro_secret_key` | *(empty)* | Django secret key |
| `rapidpro_url` | `/rp` | URL path prefix |
| `rapidpro_domain` | `{{ iiab_hostname }}` | Domain for courier attachment URLs |
| `rapidpro_aws_access_key_id` | *(empty)* | AWS access key (when AWS enabled) |
| `rapidpro_aws_secret_access_key` | *(empty)* | AWS secret key (when AWS enabled) |
| `rapidpro_aws_region` | `us-east-1` | AWS region (when AWS enabled) |
| `rapidpro_s3_bucket_prefix` | `temba` | S3 bucket prefix (when AWS enabled) |
| `rapidpro_dynamo_table_prefix` | `Temba` | DynamoDB table prefix (when AWS enabled) |
| `rapidpro_drop_db_on_reinstall` | `false` | Drop DB when reinstalling |
| `rapidpro_force_drop_db` | `false` | Force DB drop |

## Dependencies (auto-installed)

| Dependency | Via | Purpose |
|------------|-----|---------|
| **nginx** | `meta/main.yml` | Reverse proxy |
| **postgresql** | `meta/main.yml` | Database |
| **wuzapi** | `meta/main.yml` | WhatsApp bridge |

## What it does

- Clones `deldesir/rapidpro` (Django app + Courier + Mailroom)
- Creates PostgreSQL database and runs migrations
- Configures Nginx reverse proxy
- Sets up systemd services for RapidPro, Courier, and Mailroom
- If `rapidpro_aws_enabled: true`: creates DynamoDB tables, requires Elasticsearch, and configures S3.
- If `rapidpro_aws_enabled: false`: automatically bypasses Elasticsearch and DynamoDB, purging legacy spools and falling back to native high-performance GIN PostgreSQL deployments using hyper-compressed UPX binaries.

### Sub-path and the front-end

The desk is served under `rapidpro_url` (`/rp` by default). The app's frame
publishes the root in `window.URLS.root`, and the components and the frame's own
scripts resolve every root-absolute app path against it, so nothing rewrites
the built bundles at deploy time and nginx carries no root-level catch-all for
the desk's paths; `/api/v2/` at the host root stays as an alias for
integrations. The flow editor's completion docs come from mailroom, which
serves the goflow docs the role fetches for the release named in mailroom's
`go.mod` (`--tags mailroom-docs` refreshes them; `--tags nginx` re-renders and
reloads the site config alone).

### Realtime sockets (Centrifugo)

temba's live pages — the ticket inbox, contact chat, typing indicators, in-app
notifications, flow-editor activity and the webchat widget — connect to
`wss://<host>/ws/connect`, which must be a [Centrifugo](https://centrifugal.dev)
server. Without one those pages only change on reload. The role can run a
native, loopback-only Centrifugo (systemd unit `centrifugo`, config in
`/etc/centrifugo/config.json`, stateless so nothing to back up):

```yaml
rapidpro_centrifugo_enabled: true
rapidpro_centrifugo_api_key: "<openssl rand -hex 32>"   # shared with mailroom
# optional:
rapidpro_centrifugo_allowed_origins: ["*"]   # narrow if you never embed webchat elsewhere
rapidpro_centrifugo_port: 8092
rapidpro_internal_http_port: 8093            # loopback nginx listener for temba's /ti/ API
```

How the pieces connect: nginx proxies `/ws/connect` to Centrifugo; Centrifugo
proxies every connect, refresh, subscribe and sub_refresh to temba's internal
`/ti/websockets/` API through a loopback nginx listener (session cookie and
Origin forwarded, `INTERNAL_AUTH_TOKEN` added); mailroom publishes events to
Centrifugo's HTTP API with the shared key; agent typing publications are
proxied to mailroom's internal server. Everything in this section is tagged
`centrifugo`, so it can be (re)applied on its own:

```bash
cd /opt/iiab/iiab && ansible-playbook -i ansible_hosts run-one-role.yml --connection=local \
  --extra-vars '{"role_to_run":"rapidpro"}' --tags centrifugo
```

Quick checks: `curl -s localhost:8092/health` answers `{}`, and an open desk
page logs `GET /ws/connect` with status 101 in the nginx access log.
