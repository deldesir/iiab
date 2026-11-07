# Snipe-IT README

[Snipe-IT](https://snipe-it.io) is a free and open-source asset register for IT departments: every piece of equipment by serial number, model and status, who holds it (check-out and check-in to a person or a location, with history), consumables, components and accessories by quantity, licences, physical audits, warranty alerts, labels with barcodes or QR codes, CSV import and export, and a REST API. It needs no prices to work, which suits a school, a clinic or an NGO office that receives equipment rather than buying it.

It runs natively on the Internet-in-a-Box stack: nginx and php-fpm from [roles/nginx](../nginx), MariaDB from [roles/mysql](../mysql). This role adds the PHP extensions it needs, checks out a release, installs the composer dependencies, creates the database and the `.env`, runs the migrations, and gives it a listener of its own plus a systemd timer for Laravel's scheduler.

## Install It

(1) Set these two variables in [/etc/iiab/local_vars.yml](http://FAQ.IIAB.IO#What_is_local_vars.yml_and_how_do_I_customize_it%3F) prior to installing Internet-in-a-Box (or before running `./runrole snipeit`):

    snipeit_install: True
    snipeit_enabled: True

(2) A Laravel app wants the site root, so Snipe-IT does not live under an IIAB sub-path. It listens on its own port (default 8934) on every interface. On a box with a public side, bind it to a private address instead, for example a tailnet address:

    snipeit_bind: 100.64.0.12

(3) Open `http://<box>:8934/` once the role has run. Snipe-IT's own pre-flight page checks the install and creates the first administrator account. There is no default user.

## Using It

- Equipment goes in as **Assets** (model, serial, status, location) and is **checked out** to a person or a location; check-in brings it back. The history of every asset is kept.
- Things counted rather than tracked by serial (toner, cables, chargers) go in as **Consumables**, **Components** or **Accessories**.
- **Audits** schedule physical inventories; **labels** print from the asset list.
- Users can be imported from CSV, created by hand, or synced from the REST API by a script of yours.

API keys: the install runs `passport:install` once (OAuth keys under `storage/`, the personal
access client), so a user can create an API key from their profile and scripts can use the REST API.

## Variables

| Variable | Default | What it does |
|---|---|---|
| `snipeit_version` | a release tag | The Git tag to install (`git ls-remote --tags` lists newer ones) |
| `snipeit_dir` | `/opt/iiab/snipe-it` | Where the code lives |
| `snipeit_port` | `8934` | The listener's port |
| `snipeit_bind` | `""` (every interface) | A single address to listen on |
| `snipeit_locale` | `en-US` | UI language of new users (`fr-FR` for French) |
| `snipeit_max_upload` | `32m` | nginx body-size limit for images, imports and restores |
| `snipeit_db_name`, `snipeit_db_user` | `snipeit` | MariaDB database and user; the password is generated once into `/etc/iiab/snipeit-db.pw` |

Mail is written to the application log (`MAIL_MAILER=log`) until you point `.env` at a relay. The `.env` is created once from `.env.example` and keeps its `APP_KEY` across re-runs: never regenerate the key on a live install, or encrypted fields become unreadable.

## Backups

Everything that matters is the database, `storage/app` (private uploads, Snipe-IT's own backups) and `public/uploads` (images). Snipe-IT can also take its own backups from Admin → Backups. The fleet hub's nightly control-plane export bundles all three when this role is present on the hub.

## Upgrading

Set `snipeit_version` to the new tag, then as root in `snipeit_dir`:

    sudo -u snipeit -H git fetch --depth 1 origin tag <tag> && sudo -u snipeit git checkout <tag>
    sudo -u snipeit -H composer install --no-dev --prefer-dist --no-interaction
    sudo -u www-data php artisan migrate --force && sudo -u www-data php artisan config:clear

Read the release notes first; major versions sometimes add `.env` keys.

## Known Limitations

- Snipe-IT 8.x lists PHP 8.2 as its minimum and 8.4 as recommended. Ubuntu 26.04 ships PHP 8.5, on which the install, the migrations and the UI were verified; it is not a pairing the project lists.
- No sub-path: the listener needs a port of its own (or a hostname, if you give it one).
