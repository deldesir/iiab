# RiveBot — IIAB Ansible Role

Deploys the [RiveBot](https://github.com/deldesir/rivebot) service: a standalone
RiveScript brain engine that provides deterministic intent matching ahead of LLM
invocation in the IIAB AI Gateway.

## Role Variables

| Variable | Default | Description |
|---|---|---|
| `rivebot_install` | `True` | Install the service |
| `rivebot_enabled` | `True` | Start and enable the systemd service |
| `rivebot_home` | `/opt/iiab/rivebot` | Install directory |
| `rivebot_user` | `{{ iiab_admin_user }}` | Service user |
| `rivebot_port` | `8087` | Listening port (localhost only) |
| `rivebot_repo` | GitHub URL | Source repository |
| `rivebot_repo_version` | `main` | Branch / tag |
| `rivebot_gateway_url` | `http://127.0.0.1:8086` | AI Gateway base URL |
| `rivebot_macro_timeout_s` | `10` | Per-macro HTTP timeout |
| `rivebot_siyuan_api_url` | `` | SiYuan HTTP API (Mode C sync) |
| `rivebot_siyuan_api_token` | `` | SiYuan API token |
| `rivebot_siyuan_notebook_id` | `` | "Bot Brains" notebook ID |
| `rivebot_siyuan_poll_interval_s` | `30` | SiYuan poll interval |

## SiYuan Integration

Set these in `local_vars.yml` to enable automatic brain sync from SiYuan:

```yaml
rivebot_siyuan_api_url: "http://100.64.0.11:56260"
rivebot_siyuan_api_token: "your-token-here"
rivebot_siyuan_notebook_id: "20260321012908-iej1pzy"
```

RiveBot will fetch brain documents from the **Bot Brains** notebook on startup
and every `rivebot_siyuan_poll_interval_s` seconds. Edit the `rivescript` code
blocks in SiYuan and the brains reload automatically.

## Example `local_vars.yml`

```yaml
rivebot_install: True
rivebot_enabled: True
rivebot_siyuan_api_url: "http://100.64.0.11:56260"
rivebot_siyuan_api_token: "59973h7dz4jr4moa"
rivebot_siyuan_notebook_id: "20260321012908-iej1pzy"
```
