"""Create the RapidPro External (EX) channel for the email-bridge.

Mirrors temba's External ClaimView (Channel.add_config_external_channel). Run it
in the RapidPro Django context, e.g.:

    cd /opt/iiab/rapidpro && \
      EMAIL_BOT_ADDR=bot@example.com EMAIL_SEND_AUTH='<your-secret>' \
      ./.venv/bin/python manage.py shell \
        -c "exec(open('/opt/iiab/iiab/roles/email-bridge/files/create_email_channel.py').read())"

It prints the new channel UUID — set it as `email_bridge_channel_uuid:` in
/etc/iiab/local_vars.yml (and the same secret as `email_bridge_send_auth_token`),
then run `./runrole email-bridge`.

(The RapidPro web UI — Add Channel → External API — does the exact same thing;
use whichever you prefer. Review this before running.)
"""
import os

from temba.channels.models import Channel
from temba.channels.types.external.type import ExternalType
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

print("EMAIL CHANNEL CREATED")
print("  uuid:        ", channel.uuid)
print("  address:     ", BOT_ADDR, "(scheme=mailto)")
print("  receive URL:  http://localhost:8080/c/ex/%s/receive" % channel.uuid)
print()
print("  -> set in /etc/iiab/local_vars.yml:")
print("       email_bridge_channel_uuid: %s" % channel.uuid)
print("     then: ./runrole email-bridge")
