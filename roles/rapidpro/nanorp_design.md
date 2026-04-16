# Technical Design Document: Nanorp — RapidPro Without AWS

**Status:** Revised — Post Adversarial Review  
**Author:** Engineering Team  
**Date:** April 16, 2026  
**Revised:** April 16, 2026 — incorporating 15 verified findings from adversarial review  
**Scope:** RapidPro Django monolith (`deldesir/rapidpro`), Courier (`deldesir/courier`), Mailroom (`deldesir/mailroom`), Ansible role (`iiab/roles/rapidpro`)  
**Dependencies:** PostgreSQL 16, Valkey (Redis-compatible), Go 1.25+, Python 3.12

---

## Table of Contents

1. [System Context](#1-system-context)
2. [Executive Summary](#2-executive-summary)
3. [Goals and Non-Goals](#3-goals-and-non-goals)
4. [Current Architecture — What Depends on AWS](#4-current-architecture--what-depends-on-aws)
5. [Proposed Architecture — PostgreSQL Parity](#5-proposed-architecture--postgresql-parity)
6. [Workstream 1: Contact Event History](#6-workstream-1-contact-event-history)
7. [Workstream 2: Contact Search and Smart Groups](#7-workstream-2-contact-search-and-smart-groups)
8. [Workstream 3: Message Search](#8-workstream-3-message-search)
9. [Workstream 4: Channel Logs](#9-workstream-4-channel-logs)
10. [Workstream 5: Spool Cleanup and Metrics Safety](#10-workstream-5-spool-cleanup-and-metrics-safety)
11. [Workstream 6: Exception Hardening — Cross-Cutting Concerns](#11-workstream-6-exception-hardening--cross-cutting-concerns)
12. [Data Retention Strategy](#12-data-retention-strategy)
13. [Migration Plan](#13-migration-plan)
14. [Rollback Strategy](#14-rollback-strategy)
15. [Verification Plan](#15-verification-plan)
16. [Open Questions](#16-open-questions)
17. [Appendix A: Challenge Prompt for Adversarial Review](#appendix-a-challenge-prompt-for-adversarial-review)
18. [Appendix D: Adversarial Review Resolution Log](#appendix-d-adversarial-review-resolution-log)

---

## 1. System Context

RapidPro is an open-source CRM and flow engine for messaging. The upstream Nyaruka codebase uses three AWS services as core infrastructure:

| AWS Service | What RapidPro Uses It For | Scale |
|---|---|---|
| **DynamoDB** | Contact event history (the chat timeline), channel log storage | ~28 event types, 7-day to infinite TTL |
| **Elasticsearch** | Contact search, Smart Group population, message text search | Full-text + structured queries |
| **S3** | Media attachments (images, voice notes, documents) | Blob storage |

The IIAB project deploys RapidPro on self-hosted hardware with no AWS account. The "Nanorp" initiative removes all three dependencies, replacing them with PostgreSQL-backed equivalents. S3 has already been replaced with local filesystem storage. This document addresses DynamoDB and Elasticsearch.

### 1.1 Deployment Profile

- **Users:** 1-10 concurrent WhatsApp users (the operator and close friends/family)
- **Messages:** ~50-500 per day
- **Contacts:** ~10-100 total
- **Hardware:** 2-4 CPU, 4-8GB RAM VPS or ARM board
- **Storage:** 40-100GB SSD

This is not a high-scale deployment. The design can trade horizontal scalability for implementation simplicity. A PostgreSQL `ILIKE` query scanning 100 contacts is perfectly acceptable where Elasticsearch would be required at 100,000.

---

## 2. Executive Summary

This document proposes replacing DynamoDB and Elasticsearch with PostgreSQL-native implementations across five workstreams:

1. **Contact Event History** — Replace DynamoDB History table reads with a unified PostgreSQL query across `msgs_msg`, `tickets_ticket`, `flows_flowrun`, and `contacts_contactevent` tables.
2. **Contact Search & Smart Groups** — Replace Elasticsearch contact queries with PostgreSQL `pg_trgm` full-text search and direct ORM queries.
3. **Message Search** — Replace Elasticsearch message index with PostgreSQL `ts_vector` full-text search on `msgs_msg.text`.
4. **Channel Logs** — Replace DynamoDB Main table with PostgreSQL or local filesystem storage.
5. **Spool Cleanup & Metrics Safety** — Create a `NopElasticWriter`, guard CloudWatch metrics against nil Spool, and purge legacy spool files.

Changes span three codebases:
- **RapidPro** (Python/Django) — Event history fallback, search views
- **Mailroom** (Go) — Contact search, message search, event indexing
- **Courier** (Go) — Channel log storage, metrics reporting

---

## 3. Goals and Non-Goals

### Goals

- **G1:** Every feature that worked with DynamoDB + Elasticsearch must work without them. No silent degradation. The UI must show the same information.
- **G2:** Zero new infrastructure. No new services, databases, or Docker containers. PostgreSQL is already running.
- **G3:** The nanorp mode must be toggleable. When `DYNAMO_TABLE_PREFIX` is empty and `ELASTIC` points to nothing, the system uses PostgreSQL. When they are configured, the system uses AWS. One codebase, two modes.
- **G4:** Changes must be upstreamable or cleanly isolated. No monkey-patching Django internals.
- **G5:** Data retention parity. DynamoDB TTLs (1 day, 1 year, eternity) must have PostgreSQL equivalents.

### Non-Goals

- **NG1:** Supporting high-scale deployments (>1000 contacts). At that scale, Elasticsearch is worth the operational cost.
- **NG2:** Replicating DynamoDB's sub-millisecond read latency. PostgreSQL at our scale will be <10ms, which is acceptable.
- **NG3:** Implementing the `rp-archiver` DynamoDB migration. The archiver is a separate process that reads from DynamoDB; we disable it in nanorp mode.
- **NG4:** Running Elasticsearch in a degraded/embedded mode. We remove the dependency entirely.

---

## 4. Current Architecture — What Depends on AWS

### 4.1 DynamoDB Write Path (Mailroom → DynamoDB History)

When Mailroom processes a message or applies a modifier to a contact, it generates engine events. These events are persisted to DynamoDB via the History writer:

```
Mailroom scene.go:409  →  rt.Dynamo.History.Queue(evt)  →  DynamoDB "TembaHistory" table
```

In nanorp mode, `History` is a `NopWriter` that discards all events. **This means events are generated but never stored.** The PostgreSQL tables (`msgs_msg`, `tickets_ticket`, etc.) still receive their respective records via pre-commit hooks, but the unified timeline is lost.

### 4.2 DynamoDB Read Path (Django → DynamoDB History)

When a user views a contact's chat in the RapidPro UI:

```
views.py Chat.get()  →  contact.get_history()  →  Event.get_by_contact()
  →  Event._query_history()  →  dynamo.HISTORY.query(PK="con#<uuid>")
```

In nanorp mode, `dynamo.HISTORY` is `None`, so `_query_history` returns immediately. The chat panel is empty.

### 4.3 Elasticsearch Write Path (Mailroom → ES)

Two types of documents are indexed:
- **Contact documents** — indexed on every contact modification via `IndexContacts()` ([contacts.go:138](file:///opt/iiab/mailroom/core/search/contacts.go#L138))
- **Message documents** — indexed on every message via `index_messages.go` hook ([index_messages.go:39](file:///opt/iiab/mailroom/core/runner/hooks/index_messages.go#L39))

In nanorp mode, these writes go to the ES writer, which fails and spools to disk.

### 4.4 Elasticsearch Read Path (Mailroom → ES)

Five query types:
1. **Contact search (paged)** — `GetContactUUIDsForQueryPage()` ([search.go:106](file:///opt/iiab/mailroom/core/search/search.go#L106)) — used by the contact list UI when search/sort is active
2. **Contact search (unpaged)** — `GetContactUUIDsForQuery()` ([search.go:170](file:///opt/iiab/mailroom/core/search/search.go#L170)) — used by Smart Group population (`PopulateGroup`), broadcast targeting, and campaign events. Uses ES Point-In-Time iterator for large result sets.
3. **Contact count** — `GetContactTotal()` ([search.go:97](file:///opt/iiab/mailroom/core/search/search.go#L97)) — used by export previews and Smart Group counts
4. **Message search** — `SearchMessages()` ([messages.go:85](file:///opt/iiab/mailroom/core/search/messages.go#L85)) — used by the in-chat `ChatSearch` feature
5. **Contact/message de-indexing** — `DeindexContactsByUUID()` ([contacts.go:175](file:///opt/iiab/mailroom/core/search/contacts.go#L175)), `DeindexMessages()` ([messages.go:158](file:///opt/iiab/mailroom/core/search/messages.go#L158)) — called during contact/message deletion

> [!IMPORTANT]
> **Clarification (post-review):** In nanorp mode, `rt.ES.Client` is NOT nil. `runtime/search.go:19` unconditionally calls `elastic.NewClient(cfg.Elastic, ...)` even when `cfg.Elastic` is empty. The `go-elasticsearch` library creates a valid client struct pointing to an invalid URL. All ES read operations fail with **HTTP transport errors** (connection refused), not nil pointer panics. This means Mailroom returns 500-class errors to Django, but does not crash.

---

## 5. Proposed Architecture — PostgreSQL Parity

### 5.1 Feature Parity Matrix

| Feature | AWS Implementation | Nanorp Implementation | Parity Level |
|---|---|---|---|
| Contact chat timeline | DynamoDB History table (28 event types) | PostgreSQL UNION query across 4+ tables | **Full** |
| Contact search | Elasticsearch contacts index | PostgreSQL `pg_trgm` + ORM | **Functional** (no relevance scoring) |
| Smart Group population | Elasticsearch query | PostgreSQL ORM via `contactql` | **Full** (at small scale) |
| Message text search | Elasticsearch messages index | PostgreSQL `tsvector` on `msgs_msg` | **Full** |
| Channel log storage | DynamoDB Main table | PostgreSQL `channels_channellog` | **Full** (already exists) |
| Media storage | S3 | Local filesystem | **Full** (already done) |
| Data retention / TTL | DynamoDB TTL attribute | PostgreSQL cron-based cleanup | **Full** |

### 5.2 Decision: Where to Implement

| Component | Change Location | Rationale |
|---|---|---|
| Contact event history | **RapidPro Django** (`temba/mailroom/events.py`) | The read path is Django-only. Mailroom writes are already in PostgreSQL via pre-commit hooks. |
| Contact search | **Mailroom Go** (`core/search/search.go`) + **RapidPro Django** (views fallback) | Search is called via Mailroom's `/contact/search` API. Must add PostgreSQL fallback in Go. |
| Message search | **Mailroom Go** (`core/search/messages.go`) | Same pattern as contact search. |
| Channel logs | **Courier Go** (`backends/rapidpro/channel_log.go`) | Already partially done (NopWriter). Need to verify Django reads. |
| Spool/metrics | **Mailroom Go** + **Courier Go** (runtime) | Guard nil pointers, create NopElasticWriter. |

### 5.3 Operational Gotchas for Disabling AWS Dependencies

> [!WARNING]
> **Live Deployment Hazard:** When disabling AWS DynamoDB on a live, running server, simply updating the configuration (`MAILROOM_DYNAMO_TABLE_PREFIX`, `COURIER_DYNAMO_TABLE_PREFIX`) and recompiling is **not sufficient**.

1. **Text File Busy Lock:** If the `mailroom` and `courier` daemons are actively running as systemd services, trying to `cp` a newly recompiled Go binary to `/usr/local/bin/` will fail silently or abort with a "Text file busy" error. The services must be entirely stopped (`systemctl stop`) prior to replacing the binaries, otherwise the old instance remains active.
2. **AWS IMDS Metadata Timeouts:** If a daemon is accidentally running with DynamoDB enabled in a local-only environment, it will attempt to fetch security credentials from the AWS EC2 IMDS endpoint (`http://169.254.169.254/latest/meta-data/iam/security-credentials/`). Because this endpoint doesn't exist locally, it consistently triggers a 3+ second `i/o timeout` for *every* batch write. This completely blocks Mailroom HTTP handlers and Courier Valkey polling, appearing as random "frozen" or "missing" message symptoms in the Django UI (e.g. messages stuck in `status: Q`).
3. **Legacy Spool Files:** Both Mailroom and Courier write background items to local `.jsonl` spools before flushing them to AWS in batches. Even after correctly deploying the DynamoDB-disabled binaries, the daemons will detect any un-flushed legacy files in `/opt/iiab/rapidpro/spool/mailroom/dynamo` and `/opt/iiab/rapidpro/spool/courier/dynamo/` and aggressively attempt to retry flushing them to AWS on boot. These stale directories **must be manually purged** (`rm -rf`) to prevent a continuous retry loop.

---

## 6. Workstream 1: Contact Event History

**Goal:** When a user clicks on a contact in the RapidPro UI, they see a complete, interleaved timeline of messages, ticket events, flow runs, group changes, and status changes — identical to what DynamoDB provided.

### 6.1 Current State (Broken)

The current `_query_history_postgres` fallback in [events.py](file:///opt/iiab/rapidpro/temba/mailroom/events.py) only queries `msgs_msg`. It is missing 26 of 28 event types.

### 6.2 Proposed Implementation

Replace `_query_history_postgres` with a method that queries multiple PostgreSQL tables and merges them into the DynamoDB event schema.

**Source tables and their event types:**

| PostgreSQL Table | Event Types Produced | Key Fields |
|---|---|---|
| `msgs_msg` | `msg_received`, `msg_created`, `ivr_created` | `uuid`, `text`, `attachments`, `direction`, `created_on`, `contact_id` |
| `tickets_ticket` + `tickets_ticketevent` | `ticket_opened`, `ticket_closed`, `ticket_reopened`, `ticket_assignee_changed`, `ticket_note_added`, `ticket_topic_changed` | `uuid`, `event_type`, `created_on`, `contact_id` |
| `flows_flowrun` | `run_started`, `run_ended` | `uuid`, `flow_id`, `status`, `created_on`, `exited_on`, `contact_id` |
| `contacts_contactevent` (if it exists) or Django audit log | `contact_name_changed`, `contact_field_changed`, `contact_groups_changed`, `contact_language_changed`, `contact_status_changed`, `contact_urns_changed` | Varies |

> [!IMPORTANT]
> **Key investigation needed:** Not all 28 DynamoDB event types have corresponding PostgreSQL tables. Contact field/name/group changes are applied via Mailroom modifiers and written to DynamoDB but may not have separate PostgreSQL audit tables. For these, we have two options:
> 1. **Accept the gap** — contact attribute change events are TTL'd to 1 year in DynamoDB anyway. For a small deployment, this is acceptable.
> 2. **Create a `contact_events` PostgreSQL table** — a lightweight audit log that Mailroom writes to alongside DynamoDB. This requires a Go-side change.

### 6.3 Implementation Detail

> [!WARNING]
> **Post-review revision:** The original design had four bugs identified by adversarial review. All four are corrected in the code below:
> 1. **Missing `visibility` filter** — deleted/archived messages appeared in timeline (Finding 12)
> 2. **Non-deterministic sort** — events with identical timestamps could paginate incorrectly (Finding 10)
> 3. **Cursor UUID only checked in `msgs_msg`** — pagination broke when cursor was a ticket/flow event (Finding 6)
> 4. **Missing nested `msg.uuid`** — read receipts failed to render in chat UI (Finding 8)

```python
# temba/mailroom/events.py — _query_history_postgres (REVISED)

@classmethod
def _query_history_postgres(cls, pk, *, after_sk, before_sk, limit, callback):
    """PostgreSQL fallback for contact event history when DynamoDB is disabled."""
    from temba.msgs.models import Msg
    from temba.contacts.models import Contact
    from temba.tickets.models import TicketEvent
    from temba.flows.models import FlowRun

    contact_uuid = pk[4:]  # strip "con#"
    try:
        contact = Contact.objects.get(uuid=contact_uuid)
    except Contact.DoesNotExist:
        return

    # Determine time bounds from cursor UUIDs — search across ALL event tables
    cursor_time = None
    if before_sk or after_sk:
        cursor_uuid = (before_sk or after_sk)[4:]  # strip "evt#"
        cursor_time = _resolve_cursor_timestamp(contact, cursor_uuid)

    before_time = cursor_time if before_sk else None
    after_time = cursor_time if after_sk else None

    # Collect events from multiple sources
    events = []
    events.extend(_msgs_to_events(contact, before_time, after_time, limit))
    events.extend(_tickets_to_events(contact, before_time, after_time, limit))
    events.extend(_runs_to_events(contact, before_time, after_time, limit))

    # Sort with deterministic tiebreaker to prevent pagination skips
    if after_time:
        events.sort(key=lambda e: (e["_sort_key"], e["uuid"]))
    else:
        events.sort(key=lambda e: (e["_sort_key"], e["uuid"]), reverse=True)

    # Trim to limit and feed through callback
    for evt in events[:limit]:
        item = {
            "OrgID": contact.org_id,
            "PK": pk,
            "SK": f"evt#{evt['uuid']}",
            "Data": evt,
        }
        if not callback(item):
            return


def _resolve_cursor_timestamp(contact, cursor_uuid):
    """Resolve a cursor UUID to a timestamp by checking all event source tables."""
    from temba.msgs.models import Msg
    from temba.tickets.models import TicketEvent
    from temba.flows.models import FlowRun

    # Check msgs_msg first (most common)
    ts = Msg.objects.filter(uuid=cursor_uuid).values_list("created_on", flat=True).first()
    if ts:
        return ts

    # Check ticket events
    ts = TicketEvent.objects.filter(id=cursor_uuid).values_list("created_on", flat=True).first()
    if ts:
        return ts

    # Check flow runs
    ts = FlowRun.objects.filter(uuid=cursor_uuid).values_list("created_on", flat=True).first()
    if ts:
        return ts

    # Cursor not found — return None (query will fetch most recent events)
    return None
```

**Each `_*_to_events` helper** maps Django model instances to the DynamoDB event schema that `_from_item` and `_postprocess_events` expect:

```python
def _msgs_to_events(contact, before_time, after_time, limit):
    # Filter out deleted/archived messages (visibility='V' only)
    qs = Msg.objects.filter(contact=contact, visibility="V")
    if before_time:
        qs = qs.filter(created_on__lt=before_time)
    elif after_time:
        qs = qs.filter(created_on__gt=after_time)
    # Deterministic sort: timestamp + id tiebreaker
    qs = qs.order_by("-created_on", "-id" if not after_time else "created_on", "id")[:limit]

    events = []
    for msg in qs:
        evt_type = "msg_received" if msg.direction == "I" else "msg_created"
        events.append({
            "uuid": str(msg.uuid),
            "type": evt_type,
            "created_on": msg.created_on.isoformat(),
            "occurred_on": msg.created_on.isoformat(),
            "msg": {
                "uuid": str(msg.uuid),  # Required for read receipt linking
                "text": msg.text,
                "attachments": msg.attachments or [],
            },
            "_sort_key": msg.created_on,
        })
    return events
```

### 6.4 Risks

| Risk | Likelihood | Mitigation | Review Status |
|---|---|---|---|
| Contact attribute change events have no PostgreSQL source | High | Accept gap for V1; create `contact_events` table in V2 | Confirmed (F13) |
| Pagination cursor from UUID may not find a matching `msgs_msg` record | High | **Fixed:** `_resolve_cursor_timestamp` now checks `msgs_msg`, `TicketEvent`, and `FlowRun` | Resolved (F6) |
| Deleted messages appear in timeline | Confirmed | **Fixed:** Added `visibility="V"` filter to exclude deleted/archived messages | Resolved (F12) |
| Non-deterministic sort on identical timestamps | Medium | **Fixed:** Added `id` tiebreaker to `ORDER BY` clause | Resolved (F10) |
| Missing `msg.uuid` in nested dict breaks read receipts | Confirmed | **Fixed:** Added `"uuid": str(msg.uuid)` to msg sub-dict | Resolved (F8) |
| Performance on large contact histories (>10K messages) | Low (small deployment) | Add pagination index; acceptable at our scale | — |

---

## 7. Workstream 2: Contact Search and Smart Groups

**Goal:** Typing in the RapidPro contact search box returns matching contacts. Smart Groups correctly calculate membership. Broadcast targeting and campaign events resolve their recipient queries.

### 7.1 Current State (Broken)

All search queries go through Mailroom's Elasticsearch client. There are **two distinct search functions** that must both be replaced:

| Function | Used By | ES Mechanism |
|---|---|---|
| `GetContactUUIDsForQueryPage()` ([search.go:106](file:///opt/iiab/mailroom/core/search/search.go#L106)) | UI contact list, search preview | Single `Search()` call with offset/limit |
| `GetContactUUIDsForQuery()` ([search.go:170](file:///opt/iiab/mailroom/core/search/search.go#L170)) | Smart Group population, broadcast targeting, campaign events | ES Point-In-Time iterator with `search_after` |
| `GetContactTotal()` ([search.go:80](file:///opt/iiab/mailroom/core/search/search.go#L80)) | Export preview | `Count()` API |
| `DeindexContactsByUUID()` ([contacts.go:175](file:///opt/iiab/mailroom/core/search/contacts.go#L175)) | Contact deletion | Bulk delete |

With ES unreachable, all four return HTTP transport errors to Django.

> [!WARNING]
> **Post-review decision (Findings 3, 4):** Option B (Django-side `ILIKE` fallback) has been **withdrawn**. It fundamentally breaks `contactql` semantics — queries like `age > 30` would be interpreted as literal string matches. Additionally, it only covers the paged UI search, not the unpaged `GetContactUUIDsForQuery` used by Smart Group population, broadcast targeting, and campaign events.

### 7.2 Proposed Implementation — Go-side `contactql` to SQL (Option A, Required)

Add `search_postgres.go` alongside `search.go`. When `cfg.Elastic` is empty, route **all** search functions to PostgreSQL implementations:

```go
// core/search/search_postgres.go

// isNanorpMode returns true when Elasticsearch is disabled.
func isNanorpMode(rt *runtime.Runtime) bool {
    return rt.Config.Elastic == ""
}

// contactqlToSQL transpiles a parsed contactql AST into parameterized SQL.
// Uses $N placeholders to prevent SQL injection.
func contactqlToSQL(parsed *contactql.ContactQuery, oa *models.OrgAssets) (string, []any) {
    // Walk the AST and build parameterized WHERE clauses:
    //   name ~ "Joe"     → "LOWER(name) LIKE $N"  with arg "%joe%"
    //   age > 30         → "fields->>'age' > $N"   with arg "30"
    //   group = "VIP"    → "id IN (SELECT contact_id FROM contacts_contactgroup_contacts WHERE ...)"
    //   has email         → "fields ? $N"           with arg "email"
    // All values are parameterized — never interpolated.
}

func GetContactUUIDsForQueryPagePostgres(
    ctx context.Context, rt *runtime.Runtime, oa *models.OrgAssets,
    group *models.Group, excludeUUIDs []flows.ContactUUID, query string,
    sort string, offset, pageSize int,
) (*contactql.ContactQuery, []flows.ContactUUID, int64, error) {
    // Parse, transpile, execute against contacts_contact
    // Returns UUIDs (not integer IDs) per upstream migration C.1
}

// GetContactUUIDsForQueryPostgres replaces the ES Point-In-Time iterator
// with chunked PostgreSQL queries using server-side cursors.
func GetContactUUIDsForQueryPostgres(
    ctx context.Context, rt *runtime.Runtime, oa *models.OrgAssets,
    group *models.Group, status models.ContactStatus, query string, limit int,
) ([]flows.ContactUUID, error) {
    // For limit <= 10000: single SELECT with LIMIT
    // For limit == -1 (all): use DECLARE CURSOR / FETCH 10000
}

// GetContactTotalPostgres replaces ES Count() with PostgreSQL COUNT(*)
func GetContactTotalPostgres(
    ctx context.Context, rt *runtime.Runtime, oa *models.OrgAssets,
    group *models.Group, query string,
) (*contactql.ContactQuery, int64, error) {
    // Parse query, transpile to SQL, execute COUNT(*)
}
```

**Routing:** Each exported function in `search.go` checks `isNanorpMode(rt)` at the top and dispatches to the Postgres variant:

```go
func GetContactTotal(ctx context.Context, rt *runtime.Runtime, ...) (*contactql.ContactQuery, int64, error) {
    if isNanorpMode(rt) {
        return GetContactTotalPostgres(ctx, rt, ...)
    }
    // ... existing ES code
}
```

**De-indexing:** When `isNanorpMode(rt)` is true, `DeindexContactsByUUID` and `DeindexMessages` immediately return `(0, nil)` — there is no index to remove from.

> [!IMPORTANT]
> **SQL injection prevention:** The `contactqlToSQL` transpiler must use parameterized queries exclusively. Custom field names are validated against `oa.SessionAssets()` (only known field keys are accepted). Values use `$N` placeholders. The `sort` parameter is mapped through an allowlist, never interpolated.

### 7.3 Smart Group Membership

Smart Groups use `GetContactIDsForQuery` → `GetContactUUIDsForQuery` ([search.go:276](file:///opt/iiab/mailroom/core/search/search.go#L276)) for initial population. This calls the **unpaged** search function which uses the ES Point-In-Time iterator.

**Nanorp path:** `PopulateGroup.Perform()` ([populate_group.go:89](file:///opt/iiab/mailroom/core/tasks/populate_group.go#L89)) calls `GetContactIDsForQuery` which calls `GetContactUUIDsForQuery`. With the `isNanorpMode` dispatch, this routes to `GetContactUUIDsForQueryPostgres` which uses chunked PostgreSQL cursors.

Real-time membership updates during flow processing already work via `modifiers.ReevaluateGroups` in memory. Only **initial population** and **UI display** depend on the search function.

---

## 8. Workstream 3: Message Search

**Goal:** The in-chat "Search messages" feature returns matching messages for a contact.

### 8.1 Current State (Broken)

`SearchMessages()` in [messages.go:85](file:///opt/iiab/mailroom/core/search/messages.go#L85) queries the Elasticsearch messages index.

### 8.2 Proposed Implementation

> [!WARNING]
> **Post-review revision (Finding 16):** The original design proposed a flat SQL query returning columns. The upstream `SearchMessages` returns `[]MessageResult` where each result contains a `ContactUUID` and a **nested event dict** matching the DynamoDB schema. The Django `ChatSearch` view ([views.py:446-448](file:///opt/iiab/rapidpro/temba/contacts/views.py#L446-L448)) passes this directly to the JS frontend. The PostgreSQL fallback must produce the same nested structure.

Add a PostgreSQL fallback in `SearchMessages`:

```go
func SearchMessagesPostgres(
    ctx context.Context, rt *runtime.Runtime, orgID models.OrgID,
    text string, contactUUID flows.ContactUUID, createdAfter, createdBefore *time.Time, limit int,
) ([]MessageResult, error) {
    query := `
        SELECT m.uuid, m.text, m.created_on, m.direction,
               m.attachments, m.status,
               c.uuid as contact_uuid
        FROM msgs_msg m
        JOIN contacts_contact c ON m.contact_id = c.id
        WHERE m.org_id = $1 AND m.text ILIKE $2 AND m.visibility = 'V'
    `
    args := []any{orgID, "%" + text + "%"}
    argN := 3

    if contactUUID != "" {
        query += fmt.Sprintf(" AND c.uuid = $%d", argN)
        args = append(args, contactUUID)
        argN++
    }

    query += fmt.Sprintf(" ORDER BY m.created_on DESC LIMIT $%d", argN)
    args = append(args, limit)

    rows, err := rt.DB.QueryxContext(ctx, query, args...)
    if err != nil {
        return nil, fmt.Errorf("error searching messages in postgres: %w", err)
    }
    defer rows.Close()

    results := make([]MessageResult, 0, limit)
    for rows.Next() {
        var uuid, text, contactUUID, direction, status string
        var createdOn time.Time
        var attachments []string
        rows.Scan(&uuid, &text, &createdOn, &direction, &attachments, &status, &contactUUID)

        evtType := "msg_received"
        if direction == "O" {
            evtType = "msg_created"
        }

        // Build the nested event dict matching DynamoDB schema
        event := map[string]any{
            "type":       evtType,
            "created_on": createdOn.Format(time.RFC3339Nano),
            "msg": map[string]any{
                "uuid":        uuid,
                "text":        text,
                "attachments": attachments,
            },
        }

        results = append(results, MessageResult{ContactUUID: flows.ContactUUID(contactUUID), Event: event})
    }
    return results, nil
}
```

**De-indexing:** `DeindexMessages` and `DeindexMessagesByContact` return `(0, nil)` immediately in nanorp mode.

**Enhancement for Phase 2:** Add a `tsvector` column and GIN index to `msgs_msg` for proper full-text search:

```sql
ALTER TABLE msgs_msg ADD COLUMN text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX msgs_msg_text_search_idx ON msgs_msg USING gin(text_search);
```

---

## 9. Workstream 4: Channel Logs

**Goal:** Channel logs (HTTP request/response pairs for debugging) are stored and retrievable.

### 9.1 Current State (Partially Working)

Channel logs are written to the DynamoDB Main table via Courier's `queueChannelLog`. In nanorp mode, `NopWriter` discards them. However, Django already has a `channels_channellog` PostgreSQL table that Courier writes to directly for some log types.

### 9.2 Proposed Implementation

Verify that Courier's `writeChannelLog` function already falls back to PostgreSQL when DynamoDB is unavailable. The `ChannelLog.get_by_uuid` method in Django should query the PostgreSQL table. This may already work — needs verification.

---

## 10. Workstream 5: Spool Cleanup, Lifecycle Guards, and Metrics Safety

### 10.1 Elasticsearch Short-Circuit in `newElastic`

> [!WARNING]
> **Post-review clarification:** The current `runtime/search.go:18-31` unconditionally creates an ES client even when `cfg.Elastic` is empty. The client receives an invalid URL and all operations fail with HTTP transport errors. This does NOT cause panics (the client struct is non-nil), but it does cause spool accumulation because the Writer tries to flush, fails, and writes to disk.

Create a short-circuit in `newElastic` that returns a stub with a no-op Writer **and** a nil Client:

```go
// mailroom/runtime/search.go
func newElastic(cfg *Config) (*Elastic, error) {
    if cfg.Elastic == "" {
        slog.Info("Elasticsearch disabled (MAILROOM_ELASTIC is empty)")
        return &Elastic{
            Client: nil,  // Explicitly nil — read functions check isNanorpMode()
            Writer: &NopElasticWriter{},
            Spool:  nil,
        }, nil
    }
    // ... existing code
}
```

With `Client` explicitly nil, all search functions dispatch through `isNanorpMode()` (Workstream 2) rather than attempting connections. The `NopElasticWriter` prevents spool accumulation.

### 10.2 Elastic Lifecycle Nil Guards — `start()` and `stop()`

> [!CAUTION]
> **Self-inflicted bug (Gap 1-2):** Setting `Spool: nil` in `newElastic` creates nil-pointer panics in `Elastic.start()` and `Elastic.stop()`. These are called from `runtime.go:105` and `runtime.go:113` during Mailroom boot and shutdown. **Mailroom will not start without this fix.**
>
> Compare with `dynamo.go:57` which correctly guards `if d.Spool != nil`.

Add nil guards to both lifecycle methods in `runtime/search.go`:

```go
func (s *Elastic) start() error {
    if s.Spool != nil {
        if err := s.Spool.Start(); err != nil {
            return fmt.Errorf("error starting elastic spool: %w", err)
        }
    }
    if s.Writer != nil {
        s.Writer.Start()
    }
    return nil
}

func (s *Elastic) stop() {
    if s.Writer != nil {
        s.Writer.Stop()
    }
    if s.Spool != nil {
        s.Spool.Stop()
    }
}
```

### 10.3 Startup Health Check Nil Guard

> [!CAUTION]
> **Self-inflicted bug (Gap 4):** `service.go:115` calls `s.rt.ES.Client.Ping()` without checking for nil Client. With the proposed `newElastic` change, this panics on startup. Compare with line 93 which correctly guards: `if s.rt.Dynamo.Client() != nil`.

Add nil guard in `service.go`:

```go
// test Elasticsearch
if s.rt.ES.Client != nil {
    ping, err := s.rt.ES.Client.Ping().Do(s.ctx)
    if err != nil {
        log.Error("elasticsearch not available", "error", err)
    } else if !ping {
        log.Error("elasticsearch cluster not reachable")
    } else {
        log.Info("elastic ok")
    }
} else {
    log.Info("elasticsearch disabled (nanoRP mode)")
}
```

### 10.4 Metrics Reporter Nil Guards

> [!CAUTION]
> **Self-inflicted bug (Gap 3):** `service.go:268` accesses `s.rt.ES.Spool.Size()` without nil guard. This panics in the metrics goroutine every 60 seconds. Unlike the DynamoDB spool metric (line 261) which has `if s.rt.Dynamo.Spool != nil`, the ES metric has no guard.

Fix both Mailroom (`service.go:268`) and Courier (`backend.go`):

```go
// Mailroom service.go — ES spool metric
if s.rt.ES.Spool != nil {
    metrics = append(metrics,
        cwatch.Datum("ElasticSpooledItems", float64(s.rt.ES.Spool.Size()), types.StandardUnitCount, hostDim),
    )
}

// Courier backend.go — Dynamo spool metric
var spoolSize float64
if b.rt.Spool != nil {
    spoolSize = float64(b.rt.Spool.Size())
}
```

### 10.5 `DeindexContactsByOrg` Cron Guard

The `DeindexDeletedOrgsCron` ([deindex_deleted_orgs.go:46](file:///opt/iiab/mailroom/core/crons/deindex_deleted_orgs.go#L46)) runs every 5 minutes and calls `search.DeindexContactsByOrg()` → `rt.ES.Client.DeleteByQuery()`. With nil Client, this panics.

```go
func DeindexContactsByOrg(ctx context.Context, rt *runtime.Runtime, orgID models.OrgID, limit int) (int, error) {
    if isNanorpMode(rt) {
        return 0, nil
    }
    // ... existing ES code
}
```

### 10.6 Legacy Spool Purge

Add to the Ansible rapidpro role:

```yaml
- name: Purge legacy spool files from pre-nanorp era
  shell: rm -f /opt/iiab/rapidpro/spool/*/dynamo/*.jsonl /opt/iiab/rapidpro/spool/*/elastic/*.jsonl
  when: not rapidpro_aws_enabled
```

---

## 11. Workstream 6: Exception Hardening — Cross-Cutting Concerns

**Goal:** Every Django view and Mailroom endpoint that touches ES or DynamoDB must degrade gracefully in nanorp mode instead of crashing with unhandled exceptions.

> [!IMPORTANT]
> **Post-review addition (Findings 2, 5, 11, 15):** The adversarial review identified multiple Django views that catch only `mailroom.QueryValidationException` but receive transport-level errors (`MailroomException`, `RequestException`). These unhandled exceptions surface as 500 errors to the user.

### 11.1 Django Views Requiring Exception Widening

| View | File:Line | Current Exception | Required Fix |
|---|---|---|---|
| `ContactListView.get_queryset()` | [views.py:127](file:///opt/iiab/rapidpro/temba/contacts/views.py#L127) | `QueryValidationException` | Catch `Exception`, return `Contact.objects.none()` with error msg |
| `Search.get()` | [views.py:473](file:///opt/iiab/rapidpro/temba/contacts/views.py#L473) | `QueryValidationException` | Catch `Exception`, return `{"total": 0, "error": str(e)}` |
| `List.build_context_menu()` | [views.py:526](file:///opt/iiab/rapidpro/temba/contacts/views.py#L526) | `QueryValidationException` | Catch `Exception`, skip Smart Group create button |
| `ChatSearch.get()` | [views.py:446](file:///opt/iiab/rapidpro/temba/contacts/views.py#L446) | **No try/except** | Wrap in try/except, return `{"results": []}` |
| `Export.get_blocker()` | [views.py:278](file:///opt/iiab/rapidpro/temba/contacts/views.py#L278) | **No try/except** | Wrap in try/except, fall back to `group.get_member_count()` |
| `Omnibox._mixed_search()` | [omnibox.py:82](file:///opt/iiab/rapidpro/temba/contacts/omnibox.py#L82) | `QueryValidationException` | Catch `Exception`, return groups-only (contacts empty) |
| `FlowCRUDL.Start` | [flows/views.py:1332](file:///opt/iiab/rapidpro/temba/flows/views.py#L1332) | **No try/except on preview** | Guard `flow_start_preview()` call |
| `BroadcastCRUDL.Preview` | [msgs/views.py](file:///opt/iiab/rapidpro/temba/msgs/views.py) | **No try/except on preview** | Guard `msg_broadcast_preview()` call |

**Implementation pattern:**

```python
# In each view: widen the exception handler
try:
    results = mailroom.get_client().contact_search(...)
except mailroom.QueryValidationException as e:
    self.search_error = str(e)
    return Contact.objects.none()
except Exception as e:
    # Nanorp fallback: Mailroom ES transport error
    logger.warning("Mailroom search unavailable (nanorp mode?): %s", e)
    self.search_error = _("Search is temporarily unavailable")
    return Contact.objects.none()
```

### 11.2 Contact Deletion De-indexing

When a contact is deleted, `Contact.release()` ([models.py:961-970](file:///opt/iiab/rapidpro/temba/contacts/models.py#L961-L970)) calls `mailroom.get_client().contact_deindex()` **before** the PostgreSQL transaction. If de-indexing fails (ES transport error), the deletion is blocked.

**Fix:** Guard the deindex call in nanorp mode:

```python
def release(self, user, *, immediately=False, deindex=True):
    if deindex:
        try:
            mailroom.get_client().contact_deindex(self.org, [self])
        except Exception:
            # In nanorp mode, there's no ES index to clean up
            if not getattr(settings, 'NANORP_MODE', False):
                raise  # Re-raise only if ES should be available

    with transaction.atomic():
        # ... proceed with deletion
```

Alternatively, the Mailroom `handleDeindex` endpoint itself returns `(0, nil)` when `isNanorpMode(rt)` is true (Workstream 2).

---

## 12. Data Retention Strategy

DynamoDB uses a TTL attribute per item. PostgreSQL needs explicit cleanup:

| Event Type | DynamoDB TTL | PostgreSQL Equivalent |
|---|---|---|
| `msg_created`, `msg_received` | Eternity | No cleanup needed (archiver handles if desired) |
| `contact_field_changed`, `contact_name_changed` | 1 year | Cron job: `DELETE FROM contact_audit WHERE created_on < NOW() - INTERVAL '1 year'` |
| `msg_deleted` (tag) | 1 day | Cron job or Django Celery beat task |

**Implementation:** A systemd timer or Celery periodic task runs daily:

```python
# temba/contacts/tasks.py
@shared_task
def cleanup_stale_events():
    """Remove audit trail entries older than their retention period."""
    # Only needed if we create the contact_audit table (Workstream 1 V2)
    pass
```

---

## 13. Migration Plan

### Phase 1: Critical Path (Week 1)
1. ✅ Remove `log_policy` from Courier (DONE)
2. ✅ Add basic `_query_history_postgres` for messages (DONE)
3. Fix `_query_history_postgres`: add `visibility` filter, sort tiebreaker, cross-table cursor resolution, nested `msg.uuid`
4. Short-circuit `newElastic` when `cfg.Elastic` is empty (return nil Client + NopWriter)
5. **Add nil guards to `Elastic.start()` and `Elastic.stop()`** — without this, item 4 crashes Mailroom on boot (Gap 1-2)
6. **Add nil guard to `service.go:115` ES health check ping** — without this, item 4 crashes Mailroom on startup (Gap 4)
7. **Add nil guard to `service.go:268` ES spool metrics** — without this, item 4 crashes metrics goroutine every 60s (Gap 3)
8. Add `isNanorpMode()` dispatch in all `core/search/*.go` exported functions
9. Implement `GetContactUUIDsForQueryPagePostgres` with `contactqlToSQL` transpiler
10. Implement `GetContactUUIDsForQueryPostgres` (unpaged, for Smart Groups/campaigns)
11. Implement `GetContactTotalPostgres` (for export preview, broadcast preview, flow start preview)
12. Guard `DeindexContactsByUUID`, `DeindexMessages`, `DeindexMessagesByContact`, `DeindexContactsByOrg` to return `(0, nil)` in nanorp mode
13. Widen exception handlers in 8 Django views (Workstream 6)
14. Guard `Contact.release()` deindex call
15. Guard CloudWatch `Spool.Size()` nil dereference in Courier
16. Purge legacy spool files via Ansible

> [!CAUTION]
> **Items 5-7 MUST be deployed atomically with item 4.** The `newElastic` short-circuit sets `Spool` and `Client` to nil. Without the corresponding nil guards in `start()`, `stop()`, `Ping()`, and `Spool.Size()`, Mailroom will crash immediately on startup.

### Phase 2: Full Parity (Week 2-3)
17. Implement `SearchMessagesPostgres` with correct nested event schema
18. Expand `_query_history_postgres` to include tickets and flow runs
19. Add `contact_audit` PostgreSQL table for attribute change events
20. Rebuild Courier and Mailroom binaries with all changes
21. Update `update_rapidpro_binaries.sh` to include nanorp patches

### Phase 3: Hardening (Week 4)
22. Add `tsvector` GIN index for message search performance
23. Add `pg_trgm` GIN index for contact name/URN search
24. Add data retention cron jobs
25. End-to-end testing with Rodney headless browser
26. Verify all 8 Django exception-widened views render gracefully

---

## 14. Rollback Strategy

Every change is guarded by a runtime check (`if dynamo.HISTORY is None` / `isNanorpMode(rt)` / `if cfg.Elastic == ""`). To rollback:

1. Set `DYNAMO_TABLE_PREFIX=Temba` and `ELASTIC=http://elastic:9200` in systemd services
2. Restart services
3. The system reverts to AWS-backed behavior with no code changes

> [!WARNING]
> **One-way data gap:** Events discarded by `NopWriter` during nanorp mode are permanently lost from DynamoDB. Switching back to AWS mode creates a timeline gap for the nanorp period. PostgreSQL still has the primary data (`msgs_msg`, `tickets_ticket`, etc.) but DynamoDB History will be empty for that window.

---

## 15. Verification Plan

### 14.1 Automated Checks

```bash
# 1. Courier accepts webhooks without log_policy errors
curl -X POST http://localhost:8080/c/wz/<channel-uuid>/receive \
  -d '{}' -H "Content-Type: application/json"
# Expected: 200 OK

# 2. Contact history returns events
curl -s http://localhost:8000/contact/chat/<contact-uuid>/?before=<uuid> \
  -H "Cookie: <session>" | python3 -m json.tool
# Expected: {"events": [...], "next": ...}

# 3. Contact search works
curl -X POST http://localhost:8090/mr/contact/search \
  -d '{"org_id": 1, "group_id": 1, "query": "name ~ Joe"}' \
  -H "Content-Type: application/json"
# Expected: {"contact_uuids": [...]}

# 4. No spool file accumulation
find /opt/iiab/rapidpro/spool/ -name "*.jsonl" -newer /tmp/test_marker | wc -l
# Expected: 0
```

### 14.2 Visual Verification (Rodney)

```bash
# Capture contact chat view
/opt/iiab/rodney/rodney screenshot \
  --url "http://box.lan/rapidpro/contact/read/<uuid>/" \
  --output /tmp/nanorp_chat.png \
  --wait 3000
```

### 14.3 End-to-End Message Flow

1. Send a WhatsApp message via Wuzapi
2. Verify contact is created in PostgreSQL
3. Verify message appears in the contact's chat timeline via the RapidPro UI
4. Verify message count is correct in the Inbox view

---

## 16. Open Questions

> [!IMPORTANT]
> 1. **Do contact attribute changes (name, language, groups) have corresponding PostgreSQL audit records?** If not, we must either accept the gap or create a new table. This determines the completeness of Workstream 1.

> [!IMPORTANT]
> 2. **Is `public_file_storage` correctly configured to use `FileSystemStorage` in nanorp mode?** If it still points to S3, media uploads will fail silently.

> [!WARNING]
> 3. **What happens to the `rp-archiver` process?** It reads from DynamoDB History for long-term archival. In nanorp mode, it should be disabled entirely or reconfigured to archive directly from PostgreSQL.

4. ~~**Should we implement the Go-side PostgreSQL contact search (Option A) now, or defer it?**~~ **Resolved:** Option A is required. Option B was withdrawn per adversarial review Finding 3.

5. **Is there an existing Django migration that adds `tsvector` to `msgs_msg`?** If upstream RapidPro already has this, we should use it rather than adding our own.

6. **How should the `contactqlToSQL` transpiler handle custom field queries on JSONB?** Fields are stored as `contacts_contact.fields` (JSONB). Queries like `age > 30` need `fields->>'age'::int > 30`. Type coercion must match the field type definitions in `contacts_contactfield`.

7. **Should we add a nanorp health-check endpoint?** An operator currently has no way to confirm that nanorp mode is fully active vs. ES/DynamoDB silently failing.

---

## Appendix A: Challenge Prompt for Adversarial Review

Use the following prompt to challenge this design with another frontier model. Provide this document as context alongside the codebases.

---

> **Prompt:**
> 
> You are a senior distributed systems engineer conducting an adversarial design review. You have been given a design document titled "Nanorp — RapidPro Without AWS" that proposes removing DynamoDB and Elasticsearch from a production RapidPro deployment, replacing them with PostgreSQL-only implementations.
> 
> Your role is NOT to validate. Your role is to stress-test. Assume the author is competent but has blind spots. Your job is to find them.
> 
> You have access to the following codebases as context:
> - `/opt/iiab/rapidpro/` — Django monolith (Python)
> - `/opt/iiab/courier/` — Message routing binary (Go)
> - `/opt/iiab/mailroom/` — Background processing binary (Go)
> 
> For each finding, you must:
> 1. **Cite the specific file and line** where the assumption breaks
> 2. **Explain the failure mode** — what breaks, when, and what the user sees
> 3. **Classify severity** — Critical (data loss/crash), High (feature broken), Medium (degraded UX), Low (cosmetic)
> 4. **Propose a fix** — or explain why no fix exists
> 
> Specifically investigate:
> 
> **A. Completeness:**
> - Are there code paths that call `rt.ES.Client` directly (not through the Writer) that will crash with a nil pointer in nanorp mode?
> - Are there Django views or API endpoints that assume DynamoDB/ES availability without try/catch?
> - Does the `contactql` query language have operators that ONLY work with Elasticsearch (e.g., full-text `~` matching on custom fields)?
> 
> **B. Correctness:**
> - Does the PostgreSQL event history fallback produce events in the exact schema that the JavaScript chat UI expects? Check field names, nesting, and types.
> - When the UI polls for new events (the `after` parameter), does the PostgreSQL fallback handle the edge case where the cursor UUID doesn't exist in `msgs_msg` (because it was a non-message event)?
> - Can Smart Group membership get permanently out of sync if initial population fails but in-memory re-evaluation succeeds?
> 
> **C. Consistency:**
> - The design claims NopWriter safely discards DynamoDB writes. But does Mailroom's `BulkCommit` in `scene.go` check the return value of `History.Queue()`? If `Queue()` returns an error, does the entire transaction rollback?
> - Are there race conditions between the PostgreSQL commit and the event history query? (i.e., a user could send a message, then immediately view the chat, and the message hasn't been committed yet)
> 
> **D. Operations:**
> - What monitoring/alerting exists to detect if the PostgreSQL fallback silently returns fewer events than DynamoDB would have?
> - Is there a way to validate that nanorp mode is fully active? (e.g., a health check endpoint that confirms ES and DynamoDB are disabled)
> 
> **E. The Questions They Should Have Asked:**
> - What other RapidPro features depend on Elasticsearch that aren't mentioned? (Check: contact exports, flow start previews, campaign targeting)
> - What happens to the RapidPro API v2 endpoints? Do they use ES for contact search?
> 
> Do not soften your findings. If something is wrong, say it's wrong. Cite the code.

See [nanorp_challenge_prompt.md](file:///opt/iiab/nanorp_challenge_prompt.md) for the full standalone challenge prompt.

---

## Appendix D: Adversarial Review Resolution Log

The following findings were received from an adversarial review and have been verified against source code. See [nanorp_review_verification.md](file:///opt/iiab/nanorp_review_verification.md) for the full verification report.

| # | Finding | Verdict | Resolution |
|---|---|---|---|
| F1 | ES nil pointer panic | ⚠️ Wrong mechanism | ES client is non-nil (transport error, not panic). Fixed in WS5: `newElastic` now returns nil Client explicitly. |
| F2 | Deletion rollback | ⚠️ Wrong mechanism | Deindex runs before tx — no rollback, but delete is blocked. Fixed in WS6. |
| F3 | Option B breaks contactql | ✅ Confirmed | Option B withdrawn. Option A (Go-side transpiler) is now required. |
| F4 | Missing unpaged search | ✅ Confirmed | Added `GetContactUUIDsForQueryPostgres` for Smart Groups/campaigns. |
| F5 | Wrong exception type | ✅ Confirmed | Added exception widening for 6 Django views in WS6. |
| F6 | Cursor UUID cross-table | ⚠️ Not infinite loop | Added `_resolve_cursor_timestamp` checking 3 tables in WS1. |
| F7 | NopWriter duplicate execution | ❌ Incorrect | `NopWriter.Queue()` already returns `(1000, nil)`. No action needed. |
| F8 | Missing nested msg.uuid | ⚠️ Partial | Top-level uuid exists; nested `msg.uuid` was missing. Fixed in WS1. |
| F9 | Missing occurred_on | ⚠️ Needs verification | Added `occurred_on` to fallback payload in WS1. |
| F10 | Non-deterministic sort | ✅ Confirmed | Added `id` tiebreaker to ORDER BY in WS1. |
| F11 | Export hard-fail | ✅ Confirmed | Added `GetContactTotalPostgres` in WS2; exception guard in WS6. |
| F12 | Deleted messages resurrected | ✅ Confirmed | Added `visibility="V"` filter in WS1. |
| F13 | Audit trail gap | ✅ Confirmed | Accepted for V1. `contact_events` table planned for V2 (Phase 2). |
| F14 | rp-indexer crash loop | ❌ Incorrect | Service does not exist on this system. No action needed. |
| F15 | Omnibox breaks | ⚠️ Partial | Groups still work. Contact search fails ≥3 chars. Fixed in WS6. |
| F16 | Message search schema mismatch | ✅ Confirmed | Rewrote `SearchMessagesPostgres` with nested event dict in WS3. |
| F17 | SQL VIEW alternative | ✅ Valid suggestion | Noted as Phase 3 optimization. |

**Additional gaps found through independent analysis (post-review):**

| # | Gap | Severity | Resolution |
|---|---|---|---|
| G1 | `Elastic.start()` panics on nil Spool | 🔴 Critical | Added nil guards in WS5 §10.2 — `if s.Spool != nil` |
| G2 | `Elastic.stop()` panics on nil Spool | 🔴 Critical | Same fix as G1 |
| G3 | Metrics reporter panics on nil `ES.Spool` | 🔴 Critical | Added nil guard in WS5 §10.4 — `if s.rt.ES.Spool != nil` |
| G4 | Startup health check panics on nil `ES.Client` | 🔴 Critical | Added nil guard in WS5 §10.3 — `if s.rt.ES.Client != nil` |
| G5 | `DeindexDeletedOrgsCron` crashes on nil Client | Medium | Added `isNanorpMode` guard in WS5 §10.5 |
| G6 | `deindexMessages` hook spams error logs | Low | Covered by WS2 `DeindexMessages` guard |
| G7 | Broadcast/flow preview fail without exception guard | High | Added to WS6 §11.1 table |

> [!CAUTION]
> Gaps G1-G4 are **caused by the design's own proposed fix** (setting `Spool: nil` and `Client: nil` in `newElastic`). They must be deployed atomically with the `newElastic` change. See [nanorp_additional_gaps.md](file:///opt/iiab/nanorp_additional_gaps.md) for the full analysis.

---

## Appendix B: Prior Art — Original Nanorp Roadmap

The nanorp initiative was originally conceived across two earlier planning sessions (preserved in `/opt/iiab/antigravity_backup/brain/`). This section documents the lineage and what was already accomplished to avoid re-discovery.

### B.1 Original 4-Phase Roadmap

The earliest nanorp plan defined four phases:

| Phase | Goal | Status |
|---|---|---|
| **Phase 1:** Disable Archiver | Keep all data in PostgreSQL, no S3 cold storage | ✅ Done — `rapidpro-archiver` disabled |
| **Phase 2:** Eliminate S3 | Replace `S3Boto3Storage` with `FileSystemStorage` | ✅ Done — media served via Nginx from local disk |
| **Phase 3:** Eliminate DynamoDB | Move dedup and delivery tracking to PostgreSQL | 🔶 Partially done — `NopWriter` in place, event history incomplete |
| **Phase 4:** Keep Go binaries lean | Courier (~30MB) + Mailroom (~50MB) on PostgreSQL + Valkey only | ✅ Done — binaries run without AWS |

> **Key original insight (preserved):** *"Valkey (Redis) handles the extreme throughput needed for real-time messaging queues (`msgs:active`, `msgs:throttled`) and state management natively required by Courier/Mailroom. The Go binaries process messages at microsecond speeds. Together, they form an incredibly lean local engine (~100MB RAM combined) that should not be replaced with slower Celery tasks."*

### B.2 What the Earlier Plan Got Right

- **`SESSION_STORAGE=db`** — Mailroom already supported PostgreSQL-backed flow sessions natively. This was identified early and is now the default.
- **Courier's fallback logic** — The original plan correctly identified that Courier's attachment handling already falls back to local filesystem when S3 fails.
- **The Go binary keep decision** — Phase 3 (merge Go into Django) was explicitly marked "High Risk" and deferred. This remains the correct call.

### B.3 What the Earlier Plan Missed

The original plans focused on **write-path elimination** (stop writing to DynamoDB/S3) but did not address **read-path reconstruction** (what does the UI display when DynamoDB History is empty?). This current document fills that gap with Workstreams 1-3.

The original plans also did not identify the Elasticsearch dependency as a blocker. The `NopWriter` for DynamoDB was implemented, but Elasticsearch was left unconditionally active, leading to the spool file accumulation discovered in the adversarial review.

### B.4 GitHub Repository

The original plan designated `deldesir/nanoRP` on GitHub as the central repository for application-level changes. All Go binary patches (Courier, Mailroom) should be tracked there alongside the Django-side event history fallback.

---

## Appendix C: Upstream RapidPro Changes Affecting Nanorp

An upstream analysis of `nyaruka/rapidpro` (141 new commits, v26.1.87) was conducted in a prior session and is preserved in the current antigravity brain (`3108dbcd`). The following upstream changes directly impact nanorp:

### C.1 Contact ID → UUID Migration

Upstream has migrated contact search and export functions from integer IDs to UUIDs. Elasticsearch queries to Mailroom now strictly return `contact_uuids` instead of integer IDs.

**Impact on nanorp:** The PostgreSQL contact search fallback (Workstream 2) must return UUIDs, not integer IDs. The `SearchSliceQuerySet` in `temba/utils/models/es.py` expects UUIDs. The Django views already handle this via `results.contact_uuids`.

### C.2 ChatSearch Feature

Upstream added a `ChatSearch` view ([views.py:433-448](file:///opt/iiab/rapidpro/temba/contacts/views.py#L433-L448)) that searches message text within a contact's chat history via Elasticsearch.

**Impact on nanorp:** This is exactly Workstream 3 (Message Search). The feature is already deployed in the RapidPro UI but will fail in nanorp mode because it calls `mailroom.get_client().msg_search()` → `SearchMessages()` → Elasticsearch. The PostgreSQL fallback must cover this code path.

### C.3 Merge Safety Checks

The upstream merge task list (`task_rapidpro.md`) identified two safety checks that must survive any merge:
1. ✅ `wuzapi` channel folder exists in `temba/channels/types/`
2. ✅ `DYNAMO_TABLE_PREFIX` bypass exists in `temba/settings_common.py`

Any future upstream merge must verify these two conditions remain intact, or nanorp will regress.

### C.4 Memory Lifecycle Protections

Gunicorn and Celery services were hardened with `--max-requests 1000` and `--max-tasks-per-child=1000` respectively, plus `Restart=on-failure` directives. These protect the Django process against memory leaks from long-running PostgreSQL queries — relevant because nanorp's PostgreSQL fallbacks will add query load that the DynamoDB/ES path previously offloaded.

