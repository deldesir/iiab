# Bagisto — IIAB Role

Installs [Bagisto](https://bagisto.com/) e-commerce platform (Laravel/PHP).

## `local_vars.yml` Configuration

```yaml
bagisto_install: True
bagisto_enabled: True
bagisto_app_url: http://box/bagisto
bagisto_db_pass: "your-db-password"
```

| Variable | Default | Description |
|----------|---------|-------------|
| `bagisto_install` | `False` | Install Bagisto |
| `bagisto_enabled` | `False` | Enable the service |
| `bagisto_src_dir` | `/opt/iiab/bagisto-source` | Source code directory |
| `bagisto_install_path` | `/library` | Parent install directory |
| `bagisto_abs_path` | `{{ bagisto_install_path }}/bagisto` | Full install path |
| `bagisto_app_env` | `production` | Laravel environment |
| `bagisto_app_debug` | `false` | Laravel debug mode |
| `bagisto_app_url` | `http://box/bagisto` | Public URL |
| `bagisto_app_name` | `Bagisto` | Application name |
| `bagisto_db_name` | `bagisto` | MySQL database name |
| `bagisto_db_user` | `bagisto` | MySQL user |
| `bagisto_db_pass` | `bagisto_secret` | MySQL password |
| `bagisto_db_host` | `127.0.0.1` | MySQL host |
| `bagisto_db_port` | `3306` | MySQL port |

## Dependencies (auto-installed)

| Dependency | Via | Purpose |
|------------|-----|---------|
| **mysql** | `include_role` in `install.yml` | Database (MariaDB) |

## What it does

- Installs PHP, Composer, and Laravel dependencies
- Creates MySQL database and user
- Runs Composer install and Laravel migrations
- Configures Nginx reverse proxy
