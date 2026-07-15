"""Email ↔ RapidPro bridge — the email analog of wuzapi for WhatsApp.

Speaks courier's External (EX) channel contract so email becomes a normal
RapidPro channel (scheme=mailto), flowing through the same pipeline:

    email  →  this bridge  →  courier (EX, scheme=mailto)  →  mailroom
           →  RapidPro  →  ai-gateway  →  RiveBot / Hermes

Outgoing (courier → us): courier POSTs the rendered send body to ``/send``
(the channel's send_url). We send it as an SMTP email and reply with a token
that matches the channel's ``mt_response_check`` so courier marks it sent.

Attachments (RapidPro-wired): courier's EX handler folds a message's
attachments into the ``{{text}}`` field — it appends each attachment URL on its
own trailing line (see courier ``handlers.GetTextAndAttachments``). ``/send``
therefore peels trailing URL lines off the body, fetches them (host-allowlisted
to avoid SSRF), and attaches the bytes to the email. No channel reconfiguration
is needed — any flow that sends media now produces an email with that file
attached. ``/send-file`` is the sibling path for local, programmatic senders
(e.g. the gateway emailing a generated report): a multipart upload endpoint.

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
import mimetypes
import os
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import unquote, urlparse

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile

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

# ── Attachments ──────────────────────────────────────────────────────────────
# Per-attachment size cap (bytes); default 20 MB — under Gmail's 25 MB ceiling.
MAX_ATTACH_BYTES = int(os.getenv("EMAIL_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))
# SSRF guard for the RapidPro path: hosts we're willing to fetch attachment URLs
# from. Defaults to loopback + the courier host (media is usually served next to
# RapidPro). Add your media/CDN host via EMAIL_ATTACHMENT_HOSTS (comma-sep). A URL
# whose host isn't allowed is NOT fetched — its link stays inline in the body.
_default_hosts = {"localhost", "127.0.0.1"}
if COURIER_RECEIVE_URL:
    _ch = urlparse(COURIER_RECEIVE_URL).hostname
    if _ch:
        _default_hosts.add(_ch)
ATTACHMENT_HOSTS = _default_hosts | {
    h.strip().lower() for h in os.getenv("EMAIL_ATTACHMENT_HOSTS", "").split(",") if h.strip()
}

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
def _split_inband_subject(text: str) -> tuple[str, str]:
    """Lift an in-band subject from the message text.

    The RapidPro broadcast path can only carry a text field, so upstream
    senders (e.g. the gateway's send_email tool) prepend 'Subject: <line>\\n'.
    Returns (subject, body); falls back to the configured default subject.
    """
    if text.startswith("Subject: "):
        first, _, rest = text.partition("\n")
        subject = first[len("Subject: "):].strip()
        if subject:
            return subject, rest.lstrip("\n")
    return SUBJECT, text


_URL_LINE = re.compile(r"^\s*https?://\S+\s*$")


def _split_body_and_urls(text: str) -> tuple[str, list[str]]:
    """Peel trailing bare-URL lines off the body.

    courier's EX handler appends each attachment URL on its own line after the
    message text (GetTextAndAttachments). We treat consecutive trailing lines
    that are a single URL as attachments; everything above them is the body.
    """
    lines = text.split("\n")
    urls: list[str] = []
    while lines and _URL_LINE.match(lines[-1]):
        urls.insert(0, lines.pop().strip())
    # Drop a blank separator line courier may leave between body and URLs.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines), urls


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ATTACHMENT_HOSTS


def _filename_from_url(url: str, content_type: str | None) -> str:
    name = os.path.basename(urlparse(url).path)
    name = unquote(name).strip() or "attachment"
    if "." not in name and content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
        name += ext
    return name


async def _fetch_attachment(url: str) -> tuple[str, bytes, str] | None:
    """Fetch an attachment URL → (filename, bytes, content_type). Returns None
    (and leaves the link inline) if the host isn't allowlisted, the file is too
    big, or the fetch fails — never raises, so one bad URL can't fail the send."""
    if not _host_allowed(url):
        log.warning("attachment host not allowlisted, keeping link inline: %s", url)
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
            if len(data) > MAX_ATTACH_BYTES:
                log.warning("attachment too large (%d B > %d), keeping link inline: %s",
                            len(data), MAX_ATTACH_BYTES, url)
                return None
            ctype = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            return _filename_from_url(url, ctype), data, ctype
    except Exception as e:  # noqa: BLE001
        log.warning("attachment fetch failed (%s), keeping link inline: %s", e, url)
        return None


def _send_smtp(to_addr: str, body: str, subject: str,
               attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_ADDR}>" if FROM_NAME else FROM_ADDR
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body or " ")
    for name, data, ctype in (attachments or []):
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=name)

    if SMTP_SSL:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=60) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
            if SMTP_STARTTLS:
                s.starttls(context=ssl.create_default_context())
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)


def _resolve_subject_body(subject: str | None, text: str) -> tuple[str, str]:
    """An explicit subject form field wins; otherwise lift an in-band Subject:."""
    if subject is not None and subject.strip():
        return subject.strip(), text
    return _split_inband_subject(text)


@app.post("/send")
async def send(
    text: str = Form(""),
    to: str = Form(""),
    id: str = Form(""),
    subject: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    """courier External send_url target. Returns SENT_TOKEN so courier (via the
    channel's mt_response_check) marks the message delivered. Attachments that
    courier folded into the text (trailing URL lines) are fetched and attached."""
    if SEND_AUTH and (authorization or "") != SEND_AUTH:
        raise HTTPException(status_code=401, detail="unauthorized")
    to_addr = parseaddr(to)[1] or to.strip()
    subj, text = _resolve_subject_body(subject, text)
    body, urls = _split_body_and_urls(text)
    attachments: list[tuple[str, bytes, str]] = []
    for u in urls:
        att = await _fetch_attachment(u)
        if att:
            attachments.append(att)
        else:  # fetch declined/failed — keep the link in the body so it's not lost
            body = (body + "\n" + u).strip()
    if not to_addr or (not body and not attachments):
        raise HTTPException(status_code=400, detail="missing to/text")
    try:
        # smtplib is blocking — run off the event loop
        await asyncio.to_thread(_send_smtp, to_addr, body[:MAX_BODY], subj, attachments)
        log.info("sent email to %s (msg %s, %d chars, %d attachment(s))",
                 to_addr, id, len(body), len(attachments))
    except Exception as e:
        log.error("SMTP send failed for %s: %s", to_addr, e)
        raise HTTPException(status_code=502, detail=f"smtp error: {e}")
    return Response(content=SENT_TOKEN, media_type="text/plain")


@app.post("/send-file")
async def send_file(
    to: str = Form(""),
    text: str = Form(""),
    id: str = Form(""),
    subject: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    authorization: str | None = Header(default=None),
):
    """Programmatic send with local file attachments (multipart upload). Used by
    the gateway to email generated artifacts (reports, doc packs). Same auth and
    SENT_TOKEN contract as /send; not part of the courier path."""
    if SEND_AUTH and (authorization or "") != SEND_AUTH:
        raise HTTPException(status_code=401, detail="unauthorized")
    to_addr = parseaddr(to)[1] or to.strip()
    subj, body = _resolve_subject_body(subject, text)
    attachments: list[tuple[str, bytes, str]] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > MAX_ATTACH_BYTES:
            raise HTTPException(status_code=413, detail=f"attachment too large: {f.filename}")
        ctype = (f.content_type or mimetypes.guess_type(f.filename or "")[0]
                 or "application/octet-stream")
        attachments.append((f.filename or "attachment", data, ctype))
    if not to_addr or (not body and not attachments):
        raise HTTPException(status_code=400, detail="missing to and (text or files)")
    try:
        await asyncio.to_thread(_send_smtp, to_addr, body[:MAX_BODY], subj, attachments)
        log.info("sent email to %s (msg %s, %d chars, %d file(s))",
                 to_addr, id, len(body), len(attachments))
    except Exception as e:
        log.error("SMTP send-file failed for %s: %s", to_addr, e)
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
        "attachments": True,
        "attachment_hosts": sorted(ATTACHMENT_HOSTS),
        "poll_interval": POLL_INTERVAL,
    }
