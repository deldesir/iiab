"""Get-or-create the RapidPro External (EX) channel for the email-bridge.

Idempotent: if an active EX channel already exists on this address it is reused;
otherwise it is created, mirroring temba's External ClaimView
(Channel.add_config_external_channel). The role runs this for you, but you can
also run it by hand in the RapidPro Django context:

    cd /opt/iiab/rapidpro && \
      EMAIL_BOT_ADDR=bot@example.com EMAIL_SEND_AUTH='<your-secret>' \
      ./.venv/bin/python manage.py shell \
        -c "exec(open('/opt/iiab/email-bridge/create_email_channel.py').read())"

It prints a stable `UUID=<channel-uuid>` line (parsed by the role) plus a human
summary. The RapidPro web UI — Add Channel → External API — does the same thing.
"""
import os

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.orgs.models import Org

BOT_ADDR = os.environ["EMAIL_BOT_ADDR"]  # the bot's email = channel address = {{from}}
SEND_URL = os.environ.get("EMAIL_SEND_URL", "http://localhost:8096/send")
SEND_AUTH = os.environ.get("EMAIL_SEND_AUTH", "")
MAX_LENGTH = int(os.environ.get("EMAIL_MAX_LENGTH", "8000"))

org = Org.objects.filter(is_active=True).order_by("id").first()
if org is None:
    raise SystemExit("No active org found")
user = org.created_by

# Idempotent: reuse an existing active EX channel on this address if present.
channel = (
    Channel.objects.filter(
        org=org, is_active=True, channel_type="EX", address=BOT_ADDR
    )
    .order_by("id")
    .first()
)

if channel is not None:
    status = "EXISTS"
else:
    from temba.channels.types.external.type import ExternalType

    config = {
        Channel.CONFIG_SEND_URL: SEND_URL,
        ExternalType.CONFIG_SEND_METHOD: "POST",
        ExternalType.CONFIG_CONTENT_TYPE: Channel.CONTENT_TYPE_URLENCODED,
        ExternalType.CONFIG_MAX_LENGTH: MAX_LENGTH,
        Channel.CONFIG_ENCODING: Channel.ENCODING_DEFAULT,
        ExternalType.CONFIG_SEND_BODY: "id={{id}}&text={{text}}&to={{to}}&from={{from}}",
        ExternalType.CONFIG_MT_RESPONSE_CHECK: "SENT",
    }
    if SEND_AUTH:
        config[ExternalType.CONFIG_SEND_AUTHORIZATION] = SEND_AUTH

    role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
    channel = Channel.add_config_external_channel(
        org, user, None, BOT_ADDR, "EX", config, role, [URN.EMAIL_SCHEME]
    )
    status = "CREATED"

# Machine-readable line first (the role greps for 'UUID='); keep it stable.
print("UUID=%s" % channel.uuid)
print("EMAIL CHANNEL %s" % status)
print("  address:     ", BOT_ADDR, "(scheme=mailto)")
print("  receive URL:  http://localhost:8080/c/ex/%s/receive" % channel.uuid)
