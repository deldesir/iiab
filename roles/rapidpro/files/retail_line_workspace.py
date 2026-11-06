"""The retail line through the box's own RapidPro: the shop's workspace, the gateway key as a global,
the catch-all flow that asks the gateway what to say, and its trigger. Idempotent; prints no secrets.

Run on the box as the RapidPro user, from the RapidPro tree:
  LINE_ORG="<shop>" LINE_TZ="America/Port-au-Prince" LINE_FLOW_FILE=/path/retail_line_flow.json \
  GATEWAY_KEY="$(cat /etc/iiab/gateway_internal_key)" .venv/bin/python manage.py shell < retail_line_workspace.py
The WhatsApp channel itself is claimed in the web interface (Add channel, WhatsApp bridge, the shop's number,
then the Connect page shows the code to scan), so the number is typed by the person who holds the phone.
"""
import json
import os

from django.conf import settings
from django.db import transaction

from temba.flows.models import Flow
from temba.globals.models import Global
from temba.orgs.models import Org, OrgRole
from temba.triggers.models import Trigger
from temba.users.models import User

ORG = os.environ.get("LINE_ORG") or "Shop"
TZ = os.environ.get("LINE_TZ") or "America/Port-au-Prince"
FLOW_FILE = os.environ["LINE_FLOW_FILE"]
KEY = os.environ.get("GATEWAY_KEY") or ""
SITE = os.environ.get("LINE_SITE") or f"https://{settings.HOSTNAME}"

user = User.objects.filter(is_superuser=True, is_active=True).exclude(email="system").order_by("id").first()
assert user, "no superuser to own the workspace"

org = Org.objects.filter(name=ORG, is_active=True).first()
made_org = False
if not org:
    with transaction.atomic():
        org = Org.objects.create(name=ORG, timezone=TZ, date_format=Org.DATE_FORMAT_DAY_FIRST, language=settings.DEFAULT_LANGUAGE,
                                 flow_languages=["eng"], slug=Org.get_unique_slug(ORG), created_by=user, modified_by=user)
        org.add_user(user, OrgRole.ADMINISTRATOR)
        org.initialize(sample_flows=False)      # the system groups and fields, none of the sample flows
    made_org = True

key_state = "unset"
if KEY:
    g = Global.objects.filter(org=org, key="gateway_key", is_active=True).first()
    if g is None:
        Global.get_or_create(org, user, "gateway_key", "Gateway key", KEY)
        key_state = "created"
    elif g.value != KEY:
        g.value = KEY
        g.save(update_fields=("value",))
        key_state = "updated"
    else:
        key_state = "unchanged"

export = json.load(open(FLOW_FILE))
flow_name = export["flows"][0]["name"]
flow = org.flows.filter(name=flow_name, is_active=True).first()
imported = False
if flow is None:
    org.import_app(export, user, site=SITE)      # the flow and its catch-all trigger
    flow = org.flows.filter(name=flow_name, is_active=True).first()
    imported = True

trigger = Trigger.objects.filter(org=org, flow=flow, trigger_type=Trigger.TYPE_CATCH_ALL, is_active=True, is_archived=False).first()
if trigger is None:
    trigger = Trigger.create(org, user, Trigger.TYPE_CATCH_ALL, flow)

channels = org.channels.filter(is_active=True).count()
print(f"LINE-WORKSPACE org={org.id} slug={org.slug} created={made_org} gateway_key={key_state} flow={flow.uuid} imported={imported} "
      f"issues={flow.has_issues} catch_all={trigger.uuid} channels={channels}")
