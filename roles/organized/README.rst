================
Organized README
================

This Ansible role installs `Organized <https://github.com/sws2apps/organized-app>`_
— a scheduling app for Jehovah's Witnesses congregations (meeting schedules,
assignments, field-service reports) — on your
`Internet-in-a-Box (IIAB) <https://internet-in-a-box.org>`_, configured to run
**fully offline / self-hosted** with no third-party cloud services.

The role is **off by default** (IIAB stage 6). It is a two-part application
plus a local source-materials mirror, all reverse-proxied by nginx under the
``/organized/`` subpath.


Architecture
============

**Frontend — organized-app** (``/opt/iiab/organized-app``)
  A React/Vite Progressive Web App, a fork of ``sws2apps/organized-app``. The
  role builds it offline (``npm ci`` + ``npm run build``) and nginx serves the
  static ``dist/`` at ``/organized/``. Built with Vite ``base: '/organized/'``
  so assets, the router basename and the service worker all live under the
  subpath. It is an installable, offline-capable PWA over HTTPS.

**Backend — organized-backend** (``/opt/iiab/organized-backend``)
  A standalone Django REST API (Gunicorn on ``organized_port``, localhost only)
  that **replaces the upstream sws2apps cloud + Firebase**: local cookie/JWT
  auth, congregation/country data, persistence in PostgreSQL. Reached by the
  frontend same-origin at ``/organized/api/`` via nginx. Background work
  (notifications, etc.) runs on Celery with Redis/Valkey as broker.

**Source-materials mirror** (``/library/www/html/organized-sources``, served at
``/organized-sources/``)
  Replaces ``source-materials.organized-app.com``. A systemd timer fetches the
  meeting publications (EPUB) from jw.org's public pub-media API when online,
  parses them locally with the ``meeting-schedules-parser`` npm package, and
  serves the JSON the app's auto-import expects — entirely offline at run time.
  See `Source-materials mirror`_ below.

All cloud egress is removed except optional WhatsApp delivery, which (if
configured) goes out through a **local** RapidPro/WuzAPI gateway, not a SaaS.


Enabling the role
=================

In ``/etc/iiab/local_vars.yml``::

    organized_install: True
    organized_enabled: True

    # JW pub-media language codes to auto-fetch (Latin examples).
    # Haitian Creole = CR (NOT HT), French = F, English = E.
    organized_source_languages: ['CR', 'F', 'E']

    # Recommended: pin the Django secret key so re-running the role does not
    # rotate it and log everyone out. Generate any 50-char random string.
    organized_secret_key: 'CHANGE-ME-to-a-long-random-string'

    # Initial admin login (see "Creating the first account" below). The role
    # provisions it idempotently on every run when these are set.
    organized_admin_email: 'admin@example.com'
    organized_admin_password: 'CHANGE-ME'
    organized_admin_cong_name: 'My Congregation'

Then run::

    cd /opt/iiab/iiab
    ./runrole organized

Use ``./runrole --reinstall organized`` to force a full rebuild (clone, npm
build, migrations) rather than just re-applying services + nginx.


Accessing the app
=================

* **HTTPS (recommended)** — ``https://<your-domain>/organized/`` where the
  domain matches IIAB's ``iiab_ssl_domain``. A trusted certificate is required
  for the PWA service worker to register (secure context), so this is the path
  that gives installability + offline caching.
* **HTTP / LAN** — ``http://box.lan/organized/`` works for browsing, but with
  no service worker (insecure context = no offline PWA caching).

First-run steps inside the app: sign in with the admin account (local auth — no
Google/Firebase; see `Creating the first account`_), then under settings enable
**JW auto-import** and pick the source language. The mirror must have data for
that language (the daily timer or a manual fetch — see below) for auto-import to
populate schedules.


Creating the first account
==========================

The self-hosted build has **no Firebase to mint accounts**, and the app's API
cannot self-register the first user (login needs an existing congregation
profile; congregation-create needs an already-authenticated user). So the first
admin must be provisioned out of band, via the backend's idempotent
``bootstrap_admin`` management command.

**Automatic (recommended).** Set ``organized_admin_email``,
``organized_admin_password`` and ``organized_admin_cong_name`` in
``local_vars.yml`` (see above). The role then creates — and keeps in sync — the
admin login on every run. Optional: ``organized_admin_cong_number``,
``organized_admin_country_code``, ``organized_admin_firstname``,
``organized_admin_lastname``.

**Manual.** Run the command once yourself::

    cd /opt/iiab/organized-backend
    sudo -u organized bash -c 'set -a; . /etc/iiab/organized.env; set +a; \
      ORGANIZED_ADMIN_PASSWORD="your-password" \
      .venv/bin/python manage.py bootstrap_admin \
        --email admin@example.com --cong-name "My Congregation" --admin'

The command creates a Django auth user (username = email), a Congregation, and
an admin ``CongUser`` linking them. It is idempotent and re-syncs the password
on each run. Add more members afterwards from inside the app (Admin → users).


Key variables
=============

See ``defaults/main.yml`` for the full list and inline notes.

``organized_install`` / ``organized_enabled``
  Master on/off switches (both must be ``True``).
``organized_source_languages`` (default ``['E']``)
  JW pub-media codes auto-fetched from jw.org when online. The offline parse
  step is folder-driven and works for any language dropped in manually.
``organized_secret_key`` (unset)
  Django ``SECRET_KEY``. **Unset → regenerated every run → logins reset.** Pin
  it for stable, idempotent re-runs.
``organized_port`` (default ``8088``)
  Localhost Gunicorn port for the backend.
``organized_sources_dir`` (default ``/library/www/html/organized-sources``)
  Mirror root: ``input/<LANG>/`` (drop EPUBs here) and ``api/<LANG>`` (served
  JSON).
``organized_source_materials_api`` / ``organized_notifications_api``
  Build-time frontend config injected via Vite ``.env.production.local``. Point
  auto-import at the local mirror (``/organized-sources``); announcements stay
  disabled (no SaaS).


Source-materials mirror
=======================

Two Node scripts, run by ``organized-sources.service`` (oneshot) on the
``organized-sources.timer`` (daily):

* ``files/organized-sources-fetch.js`` — best-effort download of MWB + Watchtower
  EPUBs from ``app.jw-cdn.org`` pub-media API for each ``organized_source_languages``
  code, cached by file size. Requires internet; missing issues are skipped.
* ``files/organized-sources-generate.js`` — folder-driven: parses every
  ``input/<LANG>/*.epub`` with ``meeting-schedules-parser`` and writes
  ``api/<LANG>`` (atomic temp + rename).

To add materials **manually / fully air-gapped**, drop publication files in
``{{ organized_sources_dir }}/input/<LANG>/`` and run::

    systemctl start organized-sources.service


Services
========

* ``organized.service`` — Gunicorn (Django API)
* ``organized-celery.service`` — Celery worker (background tasks)
* ``organized-sources.timer`` / ``.service`` — daily mirror refresh

nginx config is templated to ``/etc/nginx/conf.d/organized.conf``
(``location`` blocks merged into IIAB's main server on :80 and :443).


Requirements
============

Provided by other IIAB roles / the base system (``meta`` declares no hard
dependencies because IIAB controls ordering by stage): PostgreSQL, Redis/Valkey,
nginx, Node.js + npm, and ``uv`` (Python venv + deps). The backend venv is built
on the system ``/usr/bin/python3``.


Notes for maintainers
=====================

* **Subpath everything.** Because the app is mounted at ``/organized/``, the
  Vite ``base``, the PWA ``manifest.webmanifest`` (scope/start_url/icons), and
  the service-worker registration path must all carry the ``/organized/``
  prefix. ``.webmanifest`` is also served with an explicit
  ``application/manifest+json`` MIME type (nginx default is wrong for PWA install).
* **ALLOWED_HOSTS.** The backend's ``DJANGO_ALLOWED_HOSTS`` includes
  ``iiab_ssl_domain`` when SSL is enabled — otherwise Django returns
  *400 DisallowedHost* for same-origin API calls made over the HTTPS domain.
* The frontend is built offline; cloud features (Firebase messaging, Sentry,
  announcements) are neutralized at the source in the fork, not just unset.
