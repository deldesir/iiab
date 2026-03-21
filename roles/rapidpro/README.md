# RapidPro — IIAB Role

Installs [RapidPro](https://github.com/deldesir/rapidpro) flow engine,
Courier message transport, and Dynalite for local DynamoDB emulation.

## `local_vars.yml` Configuration

```yaml
rapidpro_install: True
rapidpro_enabled: True
rapidpro_secret_key: "your-django-secret"
rapidpro_admin_password: "your-admin-password"
```

| Variable | Default | Description |
|----------|---------|-------------|
| `rapidpro_install` | `False` | Install RapidPro |
| `rapidpro_enabled` | `False` | Enable the service |
| `rapidpro_db_name` | `temba` | PostgreSQL database name |
| `rapidpro_db_user` | `temba` | PostgreSQL user |
| `rapidpro_db_pass` | `temba` | PostgreSQL password |
| `admin_email` | `admin@box.lan` | Admin email for login |
| `admin_password` | `changeme` | Admin password |
| `rapidpro_url` | `/rp` | URL path prefix |
| `rapidpro_admin_password` | *(empty)* | Admin password override |
| `rapidpro_secret_key` | *(empty)* | Django secret key |
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
- Installs Dynalite (local DynamoDB for Courier)
- Configures Nginx reverse proxy
- Sets up systemd services for RapidPro, Courier, and Mailroom
