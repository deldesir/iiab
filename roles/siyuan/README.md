# SiYuan — IIAB Role

Installs [SiYuan](https://github.com/siyuan-note/siyuan) as a headless
note-taking server using pre-built release binaries from GitHub.

## Architecture

- Downloads the official Linux release tarball
- Extracts only `resources/` (kernel binary + frontend assets), skipping Electron desktop bloat
- Sets `RUN_IN_CONTAINER=true` so the kernel binds to `0.0.0.0` for network access
- Full web UI accessible at `http://box:6806`

## `local_vars.yml` Configuration

```yaml
siyuan_install: True
siyuan_enabled: True
siyuan_access_auth_code: "my-secret-code"   # recommended on shared networks
```

| Variable | Default | Description |
|----------|---------|-------------|
| `siyuan_install` | `False` | Install SiYuan |
| `siyuan_enabled` | `False` | Enable the service |
| `siyuan_version` | `3.6.1` | Release version to install |
| `siyuan_dir` | `/opt/iiab/siyuan-server` | Install directory |
| `siyuan_port` | `6806` | HTTP port |
| `siyuan_access_auth_code` | *(empty)* | Auth code (empty = no auth) |
| `siyuan_workspace` | `{{ siyuan_dir }}/workspace` | Data directory |
| `siyuan_user` | `{{ iiab_admin_user }}` | System user |

## Dependencies

None (standalone).

## Role in the Ecosystem

SiYuan serves as the knowledge authoring platform:
- **RiveBot** syncs RiveScript brains from SiYuan notebooks (Mode B/C)
- **TalkMaster** publishes manuscripts and dashboards to SiYuan
- **JWLinker** exports study cards to SiYuan notebooks
