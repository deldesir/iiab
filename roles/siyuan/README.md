# SiYuan — IIAB Role

Installs [SiYuan](https://github.com/siyuan-note/siyuan) as a bare-metal
note-taking server using pre-built release binaries from GitHub.

## Architecture

- Downloads the official Linux release tarball (Go binary + Electron frontend assets)
- Runs the Go `kernel` binary directly as a systemd service
- No Docker, no build dependencies

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `siyuan_version` | `3.6.1` | Release version to install |
| `siyuan_dir` | `/opt/iiab/siyuan-server` | Install directory |
| `siyuan_port` | `6806` | HTTP port |
| `siyuan_access_auth_code` | *(empty)* | Auth code (empty = no auth) |
| `siyuan_workspace` | `{{ siyuan_dir }}/workspace` | Data directory |

## Role in the Ecosystem

SiYuan serves as the knowledge authoring platform:
- **RiveBot** syncs RiveScript brains from SiYuan notebooks (Mode B/C)
- **TalkMaster** publishes manuscripts and dashboards to SiYuan
- **JWLinker** exports study cards to SiYuan notebooks
