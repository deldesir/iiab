# IIAB Role: ai-update

## Overview
The `ai-update` role is responsible for orchestrating and synchronizing the entire AI Cognitive Pipeline within the IIAB ecosystem. It ensures that all components of the RapidPro CRM infrastructure, the Go-based WhatsApp communication stack, and the autonomous AI personas are up to date and correctly integrated.

## Managed Components
This role actively updates and configures the following subsystems:
1.  **RapidPro**: The core Python/Django CRM engine. (Syncs git, runs database migrations, handles collectstatic).
2.  **Mailroom**: The intelligent message router connecting WhatsApp channels to RapidPro flows. (Pulls the latest dual-arch binary from GitHub releases).
3.  **Courier**: The RapidPro message sender. (Pulls binary from GitHub releases).
4.  **Wuzapi**: The Go-based Baileys interface for WhatsApp Multi-Device. (Pulls binary from GitHub releases).
5.  **Hermes Agent & MemPalace**: The AI personas and cognitive memory (vector database) engines.
6.  **TalkMaster & JWLinker**: Additional ecosystem integration hubs.

## Usage
Because this role frequently updates core binary architectures and pulls live releases, it interacts dynamically with the IIAB state mechanism.

To execute the role safely and bypass the interactive prompt:
```bash
cd /opt/iiab/iiab
echo "Y" | ./runrole ai-update
```

If you encounter missing `safe.directory` errors in git during the update, the role attempts to automatically inject them for the `iiab-admin` user, preventing "dubious ownership" fatal crashes when Ansible runs as root.

## Idempotency
Like all IIAB roles, `ai-update` aims to be idempotent. However, because it downloads binaries from GitHub releases (`v*-linux-*`), it relies on the upstream tag alignment to determine if a fresh download is necessary.
