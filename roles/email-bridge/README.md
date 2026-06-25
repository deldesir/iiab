# email-bridge

Email ⇄ RapidPro bridge — the **email analog of wuzapi**. It turns email into a
normal RapidPro channel (scheme `mailto`) so messages flow through the same
pipeline as WhatsApp:

```
email → email-bridge → courier (External "EX" channel, scheme=mailto)
      → mailroom → RapidPro → ai-gateway → RiveBot / Hermes
```

- **Outgoing** (courier → bridge): courier POSTs the rendered send body to the
  bridge's `/send`; the bridge sends it as an SMTP email and replies `SENT` so
  courier (via the channel's `mt_response_check`) marks it delivered.
- **Incoming** (bridge → courier): the bridge polls the mailbox over **IMAP**,
  extracts the sender + plain-text reply (quoted history stripped), and POSTs
  `from` + `text` to courier's `/c/ex/<uuid>/receive`. courier builds a
  `mailto:` URN. No public ingress needed (outbound IMAP/SMTP only).

It is a normal IIAB role: `./runrole email-bridge` (gated on
`email_bridge_install` / `email_bridge_enabled`). Service on `:8092`.

## Go-live runbook

**1. Put the mailbox creds in `/etc/iiab/local_vars.yml`** (never in chat):

```yaml
email_bridge_install: True
email_bridge_enabled: True
email_bridge_smtp_host: smtp.example.com
email_bridge_smtp_port: 587          # 587 STARTTLS, or 465 + ssl
email_bridge_smtp_user: bot@example.com
email_bridge_smtp_pass: "<app-password>"
email_bridge_from: bot@example.com   # the channel address
email_bridge_imap_host: imap.example.com
email_bridge_imap_port: 993
# IMAP user/pass default to the SMTP ones if your mailbox shares them
email_bridge_send_auth_token: "<a-long-random-secret>"   # = channel send_authorization
```

**2. Create the RapidPro External (EX) channel** — Add Channel → *External API*,
or via `scripts/create_email_channel.py`. Use exactly:

| Field | Value |
|---|---|
| URN scheme | `mailto` (Email Address) |
| Address / number | `bot@example.com` (the bot's email = `{{from}}`) |
| Send URL | `http://localhost:8092/send` |
| Method | `POST` |
| Content type | URL Encoded |
| Body | `id={{id}}&text={{text}}&to={{to}}&from={{from}}` |
| Max length | `8000` |
| MT response check | `SENT` |
| Send authorization (header) | the same `email_bridge_send_auth_token` |

Copy the new channel's **UUID**.

**3. Finish wiring + run the role:**

```yaml
email_bridge_channel_uuid: "<the-channel-uuid>"
```
```bash
./runrole email-bridge
```

**4. Point the channel at the AI** the same way the WhatsApp channel is wired
(the flow / call-LLM action that hits `ai-gateway`). Add allowed senders'
addresses to `AUTHORIZED_USERS` (e.g. `...,alice@example.com:Alice`).

**5. Test:** email the bot from an authorized address → you get a reply.
`#health` now includes the transport services; the bridge's own health is at
`http://localhost:8092/health`.

## Notes
- The ai-gateway already handles `mailto:` URNs (parser is scheme-aware; auth +
  wing-scoping use the URN path). It tags Hermes `platform="whatsapp"`, so email
  replies get WhatsApp formatting hints (concise, plain text) — fine for v1.
- Quoted-reply stripping is heuristic (cuts at `On … wrote:`, `>`, `----- Original`).
  Tune `_QUOTE_MARKERS` in `email_bridge.py` if your senders' clients differ.
- For a provider (Mailgun/SendGrid) instead of IMAP polling, swap the poll loop
  for the provider's inbound webhook → same `/c/ex/<uuid>/receive` POST.
