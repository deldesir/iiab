# OpenClaw Role

This role installs and configures [OpenClaw](https://github.com/openclaw/openclaw), a universal messaging gateway (featuring native WhatsApp support via Baileys), and integrates it with RapidPro.

## Requirements

*   Debian/Ubuntu-based system
*   Node.js (handled by the role dependency)
*   Nginx (handled by the role dependency)

## Role Variables

*   `openclaw_install`: Set to `True` to install OpenClaw. Default is `False` (in `vars/default_vars.yml`).
*   `openclaw_enabled`: Set to `True` to enable and start the OpenClaw service. Default is `True` (once installed).
*   `openclaw_port`: The port OpenClaw listens on. Default is `3030`.
*   `openclaw_repo`: Git repository URL. Default is `https://github.com/deldesir/openclaw.git`.

### Security Requirements (MANDATORY)

This role requires admin tokens for API security. You **MUST** define them in your local configuration (e.g., `/etc/iiab/local_vars.yml`).

**Example `/etc/iiab/local_vars.yml`:**
```yaml
openclaw_install: True
openclaw_enabled: True
openclaw_admin_token: "your_secure_admin_token"
rapidpro_auth_token: "your_secure_rp_token"
```

## Services

This role configures and manages the following systemd service:

*   `openclaw`: The OpenClaw Node.js application.

## Usage

To install and enable OpenClaw, add the following to your `/etc/iiab/local_vars.yml`:

```yaml
openclaw_install: True
openclaw_enabled: True
# Optional Overrides
# openclaw_port: 3030
```

Then run the IIAB installer or roles script:
`./runrole openclaw`
