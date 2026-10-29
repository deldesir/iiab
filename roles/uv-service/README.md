# uv-service

The shared role behind the services that run from a uv project tree: ai-gateway, rivebot,
email-bridge, social-code and the organized backend. Each of those roles keeps its own
variables, templates, asserts and the steps specific to its service; the common sequence
(source tree, libraries, venv, dependencies, ownership, environment file, units, enable or
disable, update) lives here once.

A calling role passes one dict and includes an entry:

```yaml
- name: Install the tree, venv, environment file and units of RiveBot
  ansible.builtin.include_role:
    name: uv-service
  vars:
    uv_service: "{{ rivebot_service }}"        # defined in the calling role's defaults

- name: Enable or disable RiveBot
  ansible.builtin.include_role:
    name: uv-service
    tasks_from: enable
  vars:
    uv_service: "{{ rivebot_service }}"
```

Entries: `main` installs the tree; `enable` enables and restarts (or disables and stops) the
listed units and timers; `update` moves an installed tree and its libraries to the wanted
version and re-syncs (the ai-update path; the calling role restarts on `uv_service_changed`).

A unit in `units` is a template file name, rendered as itself, or a mapping `{src, name, job}`:
the template `src` rendered as the unit `name` with the mapping `job` in reach as `unit_job`.
The gateway's scheduled jobs use the second form: one pair of templates for every job, the
jobs themselves a dictionary in its defaults.

The keys of the dict and their defaults are in `defaults/main.yml`. Templates and files are
read from the calling role (`roles/<role>/templates`, `roles/<role>/files`), so a service
role still owns its unit and environment templates.

The service tree is cloned and moved as its owner (so a private repository reached with the
owner's deploy key works); the libraries are fetched as root, with root's git credentials,
and handed to the owner afterwards. A move discards local modifications in the libraries,
so a role that patches one re-applies its patches right after the update entry.

Facts left for the calling role: `uv_service_changed` (anything moved, synced or rewritten).
