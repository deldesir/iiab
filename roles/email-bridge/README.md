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
`email_bridge_install` / `email_bridge_enabled`). Service on `:8096`.

## Go-live (4 lines)

**1. In `/etc/iiab/local_vars.yml`** (never in chat) — usually just the mailbox:

```yaml
email_bridge_install: True
email_bridge_enabled: True
email_bridge_smtp_user: bot@example.com    # the mailbox = the bot's address
email_bridge_smtp_pass: "<app-password>"
```

**2. `./runrole email-bridge`** — and that's it. The role:
- derives the **SMTP/IMAP hosts** from the address domain (provider map for
  Gmail/Outlook/Yahoo/Fastmail/iCloud/Zoho/…, else `smtp./imap.<domain>`),
- derives **TLS** from the port (465 → implicit SSL, else STARTTLS),
- reuses the SMTP user/pass for **IMAP** and for the **From** address,
- **auto-generates** the `/send` secret (stored `0600` at
  `/etc/iiab/email_bridge_send_auth.token`),
- **auto-creates** the RapidPro External (`mailto`) channel (idempotent
  get-or-create) and wires its UUID into the bridge.

**3. Point the channel at the AI** the same way the WhatsApp channel is wired
(the flow / call-LLM action that hits `ai-gateway`). Add allowed senders'
addresses to `AUTHORIZED_USERS` (e.g. `...,alice@example.com:Alice`).

**4. Test:** email the bot from an authorized address → you get a reply.
`#health` includes the transport services; the bridge's own health is at
`http://localhost:8096/health`.

### Overrides (only if your provider is unusual)
| Variable | Default | Set it when… |
|---|---|---|
| `email_bridge_smtp_host` / `_imap_host` | from the address domain | host ≠ `smtp./imap.<domain>` and not in the provider map |
| `email_bridge_smtp_port` | `587` | provider needs `465` (TLS auto-flips to SSL) |
| `email_bridge_from` | = `smtp_user` | send-as differs from the login |
| `email_bridge_imap_user` / `_pass` | = SMTP creds | IMAP login differs |
| `email_bridge_channel_uuid` | auto-created | pinning an existing channel |
| `email_bridge_send_auth_token` | auto-generated | pinning your own secret |

Add a provider to `email_bridge_known_providers` to extend the host map.

If RapidPro isn't ready when the role runs, channel creation is skipped softly
(the bridge still installs; inbound polling stays off until the UUID exists) —
re-run the role later, or create it manually via the deployed
`/opt/iiab/email-bridge/create_email_channel.py` (or UI → Add Channel →
*External API*: scheme `mailto`, send URL `http://localhost:8096/send`, body
`id={{id}}&text={{text}}&to={{to}}&from={{from}}`, MT response check `SENT`).

## Notes
- The ai-gateway already handles `mailto:` URNs (parser is scheme-aware; auth +
  wing-scoping use the URN path). It tags Hermes `platform="whatsapp"`, so email
  replies get WhatsApp formatting hints (concise, plain text) — fine for v1.
- Quoted-reply stripping is heuristic (cuts at `On … wrote:`, `>`, `----- Original`).
  Tune `_QUOTE_MARKERS` in `email_bridge.py` if your senders' clients differ.
- For a provider (Mailgun/SendGrid) instead of IMAP polling, swap the poll loop
  for the provider's inbound webhook → same `/c/ex/<uuid>/receive` POST.
