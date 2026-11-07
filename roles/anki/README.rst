
Anki Sync Server Role
=====================

This role installs a self-hosted Anki-compatible sync server (Anki's own, from the ``anki``
package) behind IIAB's nginx reverse proxy.

Usage
-----

Set the following in ``/etc/iiab/local_vars.yml`` before running IIAB:

.. code-block:: yaml

   anki_install: True
   anki_enabled: True
   anki_passwords_hashed: True
   anki_sync_users:
     - 'anki:<PHC hash of your password>'

Make the hash on the box, then paste it in single quotes (the server accepts PBKDF2 hashes in
PHC format; the plain ``user:password`` form also works with ``anki_passwords_hashed: False``):

.. code-block:: bash

   python3 -c 'import base64, getpass, hashlib, os; p = getpass.getpass().encode(); s = os.urandom(16); b = lambda x: base64.b64encode(x).decode().rstrip("="); print(f"$pbkdf2-sha256$i=600000,l=32${b(s)}${b(hashlib.pbkdf2_hmac(\"sha256\", p, s, 600000, 32))}")'

The role refuses to run without users of your own. The logins go to ``/etc/anki-sync.env``
(root only), and the server runs as the ``anki`` system user.

Who can reach it
----------------

By default only localhost, private ranges and a Tailscale tailnet reach ``/anki/``
(``anki_allow``); the server speaks plain HTTP and Anki's manual says not to expose it to the
internet. Clients point their custom sync URL at ``http://<box's tailnet or LAN name>/anki/``
(the trailing slash is required behind a sub-path proxy). ``anki_public: True`` removes the
restriction; put TLS and a strong password in front of it first.

Backups
-------

The server keeps its media index locked while it runs. ``second-brain-backup`` (the ai-gateway
role) stops it for the seconds the copy takes and starts it again.
