RapidPro Role
=============

This role installs and configures RapidPro, a platform for building and managing mobile-based services.

Requirements
------------

This role requires the following:

*   Debian/Ubuntu-based system
*   PostgreSQL
*   Nginx
*   Redis or Valkey

These dependencies are automatically installed by this role.

Role Variables
--------------

*   ``rapidpro_install``: Set to ``True`` to install RapidPro. Default is ``True``.
*   ``rapidpro_enabled``: Set to ``True`` to enable and start the RapidPro services. Default is ``True``.
*   ``rapidpro_db_name``: The name of the PostgreSQL database for RapidPro. Default is ``temba``.
*   ``rapidpro_db_user``: The PostgreSQL user for the RapidPro database. Default is ``temba``.
*   ``rapidpro_db_pass``: The password for the PostgreSQL user. Default is ``temba``.
*   ``admin_email``: The email address for the RapidPro admin user. Default is ``admin@box.lan``.
*   ``admin_password``: The password for the RapidPro admin user. Default is ``changeme``.
*   ``rapidpro_url``: The URL path for RapidPro. Default is ``/rp``.

Services
--------

This role configures and manages the following systemd services:

*   ``rapidpro-gunicorn``: The Gunicorn server for the RapidPro Django application.
*   ``rapidpro-mailroom``: The Mailroom service for handling incoming messages.
*   ``rapidpro-courier``: The Courier service for sending outgoing messages.

Usage
-----

To install and enable RapidPro, add the following to your ``/etc/iiab/local_vars.yml``::

    rapidpro_install: True
    rapidpro_enabled: True

Then run the IIAB installer.

After installation, RapidPro will be available at ``http://box.lan/rp``.