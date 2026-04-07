# Visual (Bagisto Theme Editor) Role for IIAB

This role installs the [Bagisto Visual Theme Editor](https://visual.bagistoplus.com) extension natively into the IIAB Bagisto environment. 

This role manages the localization and Composer installation of the extension without utilizing a NodeJS build environment locally (relying instead on prepublished assets).

## Requirements

The `visual` role is an extension module for Bagisto and absolute depends on a healthy and previously installed Bagisto instance. 
During the Ansible playbook execution, Bagisto MUST be installed on the system (or have succeeded earlier in the same run).
If `bagisto_installed` evaluates to undefined, the role gracefully skips installation.

## Role Variables

To install the Visual editor, set the following flags to `True` in `/etc/iiab/local_vars.yml`:

```yaml
visual_install: True
visual_enabled: True
```

**Advanced Overrides (via `/etc/iiab/local_vars.yml`)**:
- `visual_source_dir`: Defines the source path on disk where the extension repository lives. Defaults to `/opt/iiab/visual`.
- `visual_composer_package`: Specifies the exact Composer mapping identifier. Defaults to `bagistoplus/visual:^2.0@dev`.

## IIAB Integration

- **Playbook Hooks**: This service relies on `roles/6-generic-apps/tasks/main.yml`, anchoring directly beneath the `BAGISTO` trigger. 
- **Dependencies**: None explicitly defined via `meta/main.yml` to prevent playbook crashes on skipped nodes. Instead, state variables (`bagisto_installed`) are referenced.
- **Port**: This role is an embedded web module within Bagisto and does not operate on a standalone port or bind standard systemd services. 

## Troubleshooting

- Since the module triggers `php artisan vendor:publish`, make sure correct permissions are retained heavily on the Bagisto `public/vendor` folder paths. The role attempts to reset bounds natively to the `apache_user`.
- You can dynamically rebuild Laravel's package discovery without re-running the Ansible role natively by triggering:
  `cd /library/bagisto && php artisan package:discover`
