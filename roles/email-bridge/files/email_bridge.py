"""Email ↔ RapidPro bridge — the email analog of wuzapi for WhatsApp.

Speaks courier's External (EX) channel contract so email becomes a normal
RapidPro channel (scheme=mailto), flowing through the same pipeline:

    email  →  this bridge  →  courier (EX, scheme=mailto)  →  mailroom
           →  RapidPro  →  ai-gateway  →  RiveBot / Hermes

Outgoing (courier → us): courier POSTs the rendered send body to ``/send``
(the channel's send_url). We send it as an SMTP email and reply with a token
that matches the channel's ``mt_response_check`` so courier marks it sent.

Incoming (us → courier): we poll the mailbox over IMAP for unseen mail, extract
the sender + plain-text reply, and POST ``from`` + ``text`` to courier's
``/c/ex/<uuid>/receive``; courier builds the ``mailto:`` URN.

Self-contained for the edge: outbound IMAP/SMTP only, no public ingress, stdlib
smtplib/imaplib. All config via env (rendered from local_vars by the role).
"""
import asyncio
import email
import imaplib
import logging
import os
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("email-bridge")


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


# ── Config (env, rendered from local_vars by the email-bridge role) ──────────
SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))
SMTP_USER = os.getenv("EMAIL_SMTP_USER", "")
SMTP_PASS = os.getenv("EMAIL_SMTP_PASS", "")
SMTP_STARTTLS = _flag("EMAIL_SMTP_STARTTLS", "1")
SMTP_SSL = _flag("EMAIL_SMTP_SSL", "0")  # implicit TLS (port 465); else STARTTLS

FROM_ADDR = os.getenv("EMAIL_FROM", "") or SMTP_USER
FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Assistant")
SUBJECT = os.getenv("EMAIL_SUBJECT", "Re: your message")

IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "")
IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))
IMAP_USER = os.getenv("EMAIL_IMAP_USER", "") or SMTP_USER
IMAP_PASS = os.getenv("EMAIL_IMAP_PASS", "") or SMTP_PASS
IMAP_FOLDER = os.getenv("EMAIL_IMAP_FOLDER", "INBOX")
POLL_INTERVAL = int(os.getenv("EMAIL_POLL_INTERVAL", "20"))
# Where the last-processed IMAP UID is persisted (resume across restarts; on the
# very first run we baseline at the mailbox's UIDNEXT so an existing backlog is
# NEVER ingested). Cap how many new messages we handle per cycle as a flood guard.
UID_STATE = os.getenv("EMAIL_UID_STATE", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_uid"))
MAX_PER_CYCLE = int(os.getenv("EMAIL_MAX_PER_CYCLE", "25"))

# Full courier receive URL, e.g. http://localhost:8080/c/ex/<channel-uuid>/receive
COURIER_RECEIVE_URL = os.getenv("EMAIL_COURIER_RECEIVE_URL", "")
# Shared secret courier sends as the Authorization header (channel send_authorization).
SEND_AUTH = os.getenv("EMAIL_SEND_AUTH", "")
MAX_BODY = int(os.getenv("EMAIL_MAX_BODY", "8000"))
SENT_TOKEN = os.getenv("EMAIL_SENT_TOKEN", "SENT")  # must equal the channel's mt_response_check

# Server-side sender scope: only forward mail FROM these addresses (comma-
# separated, case-insensitive); empty = forward all. Scopes ingestion without
# any Gmail-side filter, and never disturbs other mail in the watched mailbox
# (e.g. the bot's own replies, or unrelated mail that landed under the label).
ALLOWED_SENDERS = {
    a.strip().lower() for a in os.getenv("EMAIL_ALLOWED_SENDERS", "").split(",") if a.strip()
}


def _sender_allowed(addr: str) -> bool:
    return not ALLOWED_SENDERS or (addr or "").strip().lower() in ALLOWED_SENDERS


app = FastAPI(title="email-bridge")


# ── Outgoing: SMTP send ──────────────────────────────────────────────────────
def _send_smtp(to_addr: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_ADDR}>" if FROM_NAME else FROM_ADDR
    msg["To"] = to_addr
    msg["Subject"] = SUBJECT
    msg.set_content(text)

    if SMTP_SSL:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            if SMTP_STARTTLS:
                s.starttls(context=ssl.create_default_context())
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)


@app.post("/send")
async def send(
    text: str = Form(""),
    to: str = Form(""),
    id: str = Form(""),
    authorization: str | None = Header(default=None),
):
    """courier External send_url target. Returns SENT_TOKEN so courier (via the
    channel's mt_response_check) marks the message delivered."""
    if SEND_AUTH and (authorization or "") != SEND_AUTH:
        raise HTTPException(status_code=401, detail="unauthorized")
    to_addr = parseaddr(to)[1] or to.strip()
    if not to_addr or not text:
        raise HTTPException(status_code=400, detail="missing to/text")
    try:
        # smtplib is blocking — run off the event loop
        await asyncio.to_thread(_send_smtp, to_addr, text[:MAX_BODY])
        log.info("sent email to %s (msg %s, %d chars)", to_addr, id, len(text))
    except Exception as e:
        log.error("SMTP send failed for %s: %s", to_addr, e)
        raise HTTPException(status_code=502, detail=f"smtp error: {e}")
    return Response(content=SENT_TOKEN, media_type="text/plain")


# ── Incoming: IMAP poll → courier ────────────────────────────────────────────
_QUOTE_MARKERS = re.compile(
    r"^\s*(On .+wrote:|-+\s*Original Message\s*-+|_{5,}|From: .+|>{1,})",
    re.IGNORECASE,
)


def _strip_quoted(text: str) -> str:
    """Keep only the new reply text — drop quoted history below common markers."""
    out = []
    for line in text.splitlines():
        if _QUOTE_MARKERS.match(line):
            break
        out.append(line)
    return "\n".join(out).strip() or text.strip()


def _plain_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
        # fall back to any text/html stripped crudely
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html = payload.decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


async def _post_to_courier(from_addr: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(COURIER_RECEIVE_URL, data={"from": from_addr, "text": text})
        if r.status_code >= 300:
            log.error("courier receive %s for %s: %s", r.status_code, from_addr, r.text[:200])
        else:
            log.info("delivered inbound from %s to courier (%d chars)", from_addr, len(text))


def _imap_connect() -> imaplib.IMAP4_SSL:
    box = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    box.login(IMAP_USER, IMAP_PASS)
    return box


def _save_uid(uid: int) -> None:
    try:
        with open(UID_STATE, "w") as f:
            f.write(str(uid))
    except OSError as e:
        log.warning("could not persist last UID to %s: %s", UID_STATE, e)


def _initial_baseline() -> int:
    """Last-processed UID to resume from. Resume from the persisted value if any;
    otherwise (first run) baseline at the folder's current UIDNEXT-1 so the
    existing backlog is skipped entirely — only mail arriving AFTER now is ever
    ingested. This is what keeps a real/personal mailbox from being flooded."""
    try:
        return int(open(UID_STATE).read().strip())
    except (OSError, ValueError):
        pass
    box = _imap_connect()
    try:
        typ, data = box.status(IMAP_FOLDER, "(UIDNEXT)")
        m = re.search(rb"UIDNEXT (\d+)", data[0]) if typ == "OK" and data and data[0] else None
        baseline = (int(m.group(1)) - 1) if m else 0
    finally:
        try:
            box.logout()
        except Exception:
            pass
    _save_uid(baseline)
    log.info("first run: baselining at UID %d — existing backlog will NOT be ingested", baseline)
    return baseline


def _fetch_new(last_uid: int) -> tuple[list[tuple[str, str]], int]:
    """Return ([(from_addr, text)], new_last_uid) for unseen messages with
    UID > last_uid, marking them seen. Capped at MAX_PER_CYCLE per cycle."""
    results: list[tuple[str, str]] = []
    box = _imap_connect()
    try:
        box.select(IMAP_FOLDER)
        typ, data = box.uid("SEARCH", "UID", f"{last_uid + 1}:*", "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return results, last_uid
        # IMAP "n:*" always returns the highest UID even when it's < n — so filter.
        uids = sorted(int(u) for u in data[0].split() if int(u) > last_uid)[:MAX_PER_CYCLE]
        for uid in uids:
            typ, msg_data = box.uid("FETCH", str(uid), "(RFC822)")
            forwarded = False
            if typ == "OK" and msg_data and msg_data[0]:
                msg = email.message_from_bytes(msg_data[0][1])
                from_addr = parseaddr(str(make_header(decode_header(msg.get("From", "")))))[1]
                if from_addr and _sender_allowed(from_addr):
                    text = _strip_quoted(_plain_text(msg))[:MAX_BODY]
                    if text:
                        results.append((from_addr, text))
                        forwarded = True
            # Mark seen ONLY mail we actually handle; non-allowed senders' mail is
            # left untouched (unread) so the shared mailbox isn't disturbed. UID
            # still advances so we don't re-scan it next cycle.
            if forwarded:
                box.uid("STORE", str(uid), "+FLAGS", "\\Seen")
            last_uid = max(last_uid, uid)
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return results, last_uid


async def _poll_loop() -> None:
    if not (IMAP_HOST and COURIER_RECEIVE_URL):
        log.warning("IMAP polling disabled (EMAIL_IMAP_HOST / EMAIL_COURIER_RECEIVE_URL unset)")
        return
    last_uid = await asyncio.to_thread(_initial_baseline)
    log.info("IMAP poll loop started (%s every %ds, UID>%d → %s)", IMAP_FOLDER, POLL_INTERVAL, last_uid, COURIER_RECEIVE_URL)
    while True:
        try:
            msgs, new_uid = await asyncio.to_thread(_fetch_new, last_uid)
            for from_addr, text in msgs:
                await _post_to_courier(from_addr, text)
            if new_uid != last_uid:
                last_uid = new_uid
                await asyncio.to_thread(_save_uid, last_uid)
        except Exception as e:
            log.error("poll cycle error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_poll_loop())


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "smtp": bool(SMTP_HOST),
        "imap": bool(IMAP_HOST),
        "courier": bool(COURIER_RECEIVE_URL),
        "poll_interval": POLL_INTERVAL,
    }
