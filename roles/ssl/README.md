# SSL — Automated Let's Encrypt Certificate Provisioning

Automates HTTPS certificate management for IIAB deployments using [Let's Encrypt](https://letsencrypt.org/) via `certbot`.

## What it does

1. Installs `certbot`
2. Obtains a TLS certificate using the **webroot** ACME challenge
3. Symlinks the certificate to IIAB's expected paths (`/etc/nginx/ssl/`)
4. Installs a **post-renewal hook** that reloads nginx after cert renewal
5. Enables a **systemd timer** for automatic renewal (~60 days)

## Configuration

Set these in `/etc/iiab/local_vars.yml` **before** running `./iiab-install` or `./runrole ssl`:

```yaml
# Required
ssl_install: True
ssl_enabled: True
iiab_ssl_enabled: True
iiab_ssl_domain: "yourdomain.com"
iiab_ssl_certbot_email: "you@email.com"

# Optional
iiab_ssl_certbot_staging: False       # Use Let's Encrypt staging CA (for testing)
iiab_ssl_certbot_auto_renew: True     # Enable systemd timer for auto-renewal
```

Without these settings, IIAB works normally over HTTP on port 80.

## How it works

```
certbot certonly --webroot -w /library/www/html -d yourdomain.com
```

The role installs an nginx snippet at `/etc/nginx/conf.d/acme-challenge.conf` that serves the ACME challenge directory. After certbot obtains the certificate, it creates symlinks:

```
/etc/nginx/ssl/yourdomain.com.cert.pem → /etc/letsencrypt/live/yourdomain.com/fullchain.pem
/etc/nginx/ssl/yourdomain.com.key.pem  → /etc/letsencrypt/live/yourdomain.com/privkey.pem
```

## Migration from manual certs

If you previously installed certificates manually, the role will:
1. Back up existing files to `*.bak-manual`
2. Replace them with symlinks to Let's Encrypt managed certs

## Renewal

Certificates auto-renew via `certbot.timer`. After renewal, the post-hook at `/etc/letsencrypt/renewal-hooks/post/nginx-reload.sh` reloads nginx.

Verify: `sudo certbot renew --dry-run`

## Pipeline

The SSL role runs in **Stage 3** (`3-base-server`), after nginx and www_base, gated by:

```yaml
when: iiab_ssl_enabled is defined and iiab_ssl_enabled and iiab_ssl_domain != "box.lan"
```

## Manual run

```bash
cd /opt/iiab/iiab
./runrole ssl
```
