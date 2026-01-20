# RapidPro for IIAB: Technical Architecture & Evolution Report

**Date:** January 20, 2026
**Scope:** Technical Lifecycle, Deployment Strategy, and Architectural Layout.

## 1. Executive Summary

This document details the engineering effort to adapt the enterprise-grade RapidPro ecosystem for the IIAB (Internet-in-a-Box) platform. The project has evolved through two distinct phases:
1.  **VM Integration:** Initial stabilization and synergy development on a standard Ubuntu VM.
2.  **Android Adaptation:** Hardening and re-architecture for constrained Android hardware (Termux/Proot).

The resulting architecture delivers a "VM-Parity" experience on mobile hardware through a unified "Fork & Binary" pipeline.

---

## 2. Project Evolution

### Phase 1: IIAB Integration (Ubuntu VM)
**Timeline:** December 9, 2025 – December 22, 2025
**Strategy:** Patch-at-Install (Source Compilation)
**Status:** Completed

This phase achieved functional synergy between the disparate Go and Python components of the RapidPro stack, tailored for the IIAB environment.

*   **Upstream Architecture:** Cloned directly from `nyaruka/rapidpro`, `nyaruka/courier`, `nyaruka/mailroom`, and `asternic/wuzapi`.
*   **Core Synergies & "Leveraging" Logic:**
    *   **Courier adjusted to leverage Wuzapi:** A native Go handler (`WZ`) was integrated into Courier to handle direct REST mapping of RapidPro channels to Wuzapi endpoints. This handler utilizes a **synchronous REST pipeline** targeting `/chat/send/{type}` (image, video, audio, document) based on MIME-type discovery.
    *   **Wuzapi adjusted to leverage Courier:** Wuzapi was configured to operate as a secure producer for Courier webhooks, utilizing a **mutual HMAC handshake** (negotiated via the `hmac_key` channel config and transmitted via `X-HMAC-Signature` headers) for payload verification.
    *   **Mailroom-Courier Linkage:** To ensure consistency, the **Mailroom** build was patched via `go.mod` to utilize the local, customized **Courier** source, creating a synchronized Go binary ecosystem.
    *   **Binary Media Orchestration:** RapidPro's Python layer was extended with custom handlers (in `temba/channels/types/wuzapi/views.py`) to orchestrate binary media downloads (specifically WhatsApp Voice Messages) via Wuzapi's download endpoints.
*   **Identified Technical Gaps & Regressions (Root Cause Analysis):**
    *   **Payload Key Mismatch (Media Send):** The Courier `WZ` handler's transition to media support introduced a regression where the media URL is transmitted using type-specific keys (e.g., `{"Image": "http://..."}`) instead of the generic `{"Body": "..."}` key expected by Wuzapi's legacy REST endpoints. This leads to successful HTTP 200 responses but "silent" delivery failures or empty messages on the device.
    *   **Web Chat Response Wrapping Failure:** The implementation of synchronous send results for `WZ` channels introduced a regression in `temba/contacts/views.py`. RapidPro's web chat UI expects a specifically wrapped `msg_created` event structure. When the `WZ` handler returns a synchronous success, the Python layer's attempt to wrap this response (via patch `0003`) occasionally results in malformed JSON or missing `_user` references, causing "Sending..." to hang indefinitely in the frontend.
    *   **JID Parsing & Voice Orchestration:** The attempt to support Voice Messages (PTT) introduced complexity in the JID parsing logic. The Go handler's switch to `fmt.Sprintf("%v", ...)` for sender resolution occasionally captures internal struct representation (e.g., `{User Server}`) instead of the raw JID string, breaking URN mapping and causing incoming messages to be dropped before they reach the Python layer.
    *   **Media Endpoint Gaps:** The Courier `WZ` handler currently lacks a unified `/media` or `/chat/downloadmedia` endpoint, relying instead on hardcoded type-specific routes, which causes failures for non-standard binary attachments that don't match the `image|video|audio` MIME types.
*   **Achievements:**
    *   **Subpath Resolution:** Full compatibility with the `/rp/` subpath via `FORCE_SCRIPT_NAME`.
    *   **Service Reliability:** Stabilized `systemd` definitions for production-grade reliability.

### Phase 2: Android Adaptation (Termux/Proot)
**Timeline:** January 6, 2026 – Present
**Strategy:** Fork & Binary (Pre-built Artifacts)
**Status:** Active

This phase transitioned the project for the unique constraints of the Android Proot environment.

*   **Repository Sovereignty:** Migrated to **`deldesir`** forks where all Phase 1 synergies are permanently baked into the source code, eliminating the overhead of runtime patching.
*   **Binary Delivery:** Shifted to deploying **Pre-built Binaries** (Cross-compiled for ARM64) to avoid the battery and memory cost of on-device compilation.
*   **Achievements:**
    *   **PDSM Innovation:** Adopted the upstream **Proot Debian Service Manager (PDSM)** architecture, decoupling the Android installer logic into a separate repository (`iiab-android`) while maintaining the core service definitions within the role.
    *   **Infrastructure Parity:** Re-architected inter-service communication to use **Unix Domain Sockets** (`/tmp/*.sock`), bypassing Android network restrictions.
    *   **Resource Tuning:** Tuned Gunicorn and Celery concurrency (1 Worker) to stabilize the stack on 4GB RAM.
    *   **DynamoDB Hardening:** Stabilized the `rapidpro-dynamo` service by restoring port 4567, implementing direct binary execution, and fixing race conditions with `dynalite`.
    *   **Upstream Synchronization:** Merged the latest `iiab/iiab` upstream changes (as of Jan 20, 2026), fully removing legacy local bootstrap scripts to align with the upstream separation of concerns.

---

---

## 3. Patch Management Evolution

A comparative audit against the **`legacy` branch** of the `deldesir/iiab` repository reveals a significant shift in deployment methodology:

*   **Legacy Methodology (Phase 1):** Relied on exhaustive "Patch-at-Install" logic. For example, the legacy `0001-Courier-Wuzapi.patch` was a ~500-line implementation of the entire `WZ` handler, injected into the upstream source during Ansible execution.
*   **Current Methodology (Phase 2):** Migrated to "Proactive Source Ownership." The massive historical patches have been merged directly into the **`deldesir` forks**. Consequently, files like `0001-Courier-Wuzapi.patch` in the current role are now **empty artifacts**, serving only as placeholders to maintain project structure, while the logic lives permanently in the `courier` repository.
*   **Residual Build-Time Patches:** Minimal patches, such as `0001-Mailroom-Wuzapi.patch`, are maintained to inject local `go.mod` dependency overrides during the cross-compilation process on the host VM.

---

## 4. Codebase Access & Location

### A. VM Layer (Development & Build)
*   **Context:** The standard Ubuntu VM Hosting this workspace.
*   **Key Paths:**
    *   **Development Source:** `/root/wuzapi_src`, `/root/courier_src`, `/root/mailroom_src`.
    *   **Assembly Line:** `/opt/iiab/iiab/roles/rapidpro/` (where the role architecture is managed).
    *   **Legacy Artifacts:** `/opt/iiab/iiab/roles/rapidpro/files/*.patch` (Phase 1 history).

### B. Android Layer (Target Deployment)
*   **Context:** Android device accessing the environment via Termux/Proot (`100.64.0.14`).
*   **Key Paths:**
    *   **Binaries:** `/usr/local/bin` (Optimized native binaries).
    *   **Service Control:** `/usr/local/pdsm/services-available/*` (PDSM scripts using numerical prefixes `01-`, `02-`, etc., for ordered dependency startup).
    *   **Static Assets:** `/data/data/com.termux/files/home/wuzapi` (Host-writable storage).

### C. Remote Layer (Source of Truth)
*   **Organization:** `https://github.com/deldesir`
*   **Role:** Central hub for forks (`rapidpro`, `wuzapi`, `courier`, `mailroom`) and the release distribution point for pre-compiled ARM64 binaries, as required for Android deployment.

---

---

## 5. Roadmap & Next Steps

Building on the successes of Phase 2, the following technical items define the immediate priority for the project to reach production maturity:

### A. Core Regression Remediation
*   **Media Payload Standardization:** Refactor the Courier `WZ` handler to use the `Body` param for media URLs to restore compatibility with the legacy Wuzapi REST API.
*   **Web Chat Response Wrapping:** Fix the implementation in `temba/contacts/views.py` to correctly wrap synchronous success responses, restoring the RapidPro web chat interface functionality.
*   **JID Type Safety:** Update the Go handler to explicitly handle `JID` types instead of generic string formatting to prevent message dropping due to JID-URN mismatches.

### B. Platform Parity & Stabilization
*   **IP Tooling Reconciliation:** Restore compatibility for standard `iproute2` tools on x86_64 environments. Recent Android-specific optimizations (using Proot/Termux-friendly fallbacks) must be guarded with environment detection to prevent breakage on standard Ubuntu IIAB deployments.
*   **Settings Unified Schema:** Consolidate `settings.py` logic to dynamically toggle between Unix Domain Sockets (Android/Proot) and TCP/IP (Standard VM) based on the detected hardware layer.

### C. UI/UX & Subpath Finalization
*   **Deep Subpath Audit:** Resolve remaining edge cases in frontend asset loading (specifically ticket-related UI elements) that still bypass the `/rp/` prefix.
*   **Wuzapi Management UI:** Polish the subpath handling in the integrated Wuzapi dashboard to ensure a seamless "IIAB-First" user experience.

---

## Technical Audit Appendix: Context for Future Refinement

This appendix provide high-fidelity technical snippets to assist in the Phase 3 stabilization effort.

### 1. Courier `WZ` Handler: Media Send and Receive Failure

### 2. JID Resolution: Text Message sending via Web Chat Bug

### 3. Settings Parity: Sockets vs. IP
Workarounds for Android 15's IP tooling restrictions (using socket detection fallbacks) have introduced overhead on x86_64 distributions.

**Impact Area:** `temba/channels/types/wuzapi/views.py` (ClaimView IP detection) and `iptools` usage in `settings_common.py.j2`.

### 4. Service Persistence: The "PostgreSQL Pattern"
**Problem:** In PRoot, processes started via SSH (even with `nohup`, `setsid`, or `daemonize`) are often killed when the SSH session terminates because the parent TTY is destroyed.
**Solution:** The only reliable persistence mechanism found is using `screen` to anchor the session to the root context.

**Implementation:**
```bash
# Correct Pattern
screen -dmS <service_name> su - <user> -c "<full_command_string>"
```
*   **Why it works:** `screen` acts as the persistent parent (anchored to init/root), and `su` correctly switches the user context *inside* the persistent screen session.
*   **Common Failure:** `su - <user> -c "screen ..."` fails because `su` terminates when the SSH TTY closes, killing the child `screen`.

### 5. PostgreSQL Networking & Auth
**Problem A (Networking):** PRoot's network translation layer adds overhead and can be flaky with TCP/IP localhost connections under load.
**Solution:** Prefer **Unix Domain Sockets** (`/var/run/postgresql`) for all local connections.
*   **Django:** Set `HOST: /var/run/postgresql` explicitly in `settings.py`.
*   **Connection Strings:** Use URL encoding: `postgres://user:pass@%2Fvar%2Frun%2Fpostgresql/dbname`.

**Problem B (Authentication):** `peer` authentication fails in PRoot because all internal users map to the single Linux UID of the Termux app. Postgres sees all connections as same-user.
**Solution:** Use `trust` authentication for local connections in `pg_hba.conf`.

### 6. Valkey/Redis Configuration Quirks
*   **Mailroom (Go Client):**
    *   Strictly enforces the `valkey://` URL scheme for configuration (rejects `redis://`).
    *   Does not gracefully handle `unix://` socket URLs in some versions/configurations.
    *   **Best Practice:** Bind Valkey to TCP `127.0.0.1:6379` and use `valkey://127.0.0.1:6379/0`.
*   **Celery (Python):**
    *   Defaults to reading `CELERY_BROKER_URL` from settings.
    *   If settings use a socket (optimized for Django), Celery might fail if it expects a TCP URL.
    *   **Fix:** Explicitly export `CELERY_BROKER_URL='redis://127.0.0.1:6379/15'` in the service wrapper.

### 7. PDSM (Process Definition Service Manager) on Android
*   **Process Detection:** `pgrep -u <user>` is unreliable in PRoot due to UID aliasing.
    *   **Fix:** Use `pgrep -f "<unique_command_string>"` to identify running services.
*   **Service Duplication:** Ensure `services-enabled` does not contain both numbered (startup order) and unnumbered symlinks to the same service. PDSM will attempt to start/status them twice.


---

## 6. Summary

The project has evolved from reactive patching to proactive architectural ownership. The transition to the "Fork & Binary" model ensures that the complex synergies developed in Phase 1 (HMAC handshakes, media orchestration, and Go-dependency linkage) are delivered as a stable, optimized artifact for Android deployment, while clearly identifying the remaining path towards full media parity and cross-platform stability.
