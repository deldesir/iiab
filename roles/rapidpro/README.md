# RapidPro — IIAB Role

Installs [RapidPro](https://github.com/deldesir/rapidpro) flow engine,
Courier message transport, and Mailroom background worker.

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
