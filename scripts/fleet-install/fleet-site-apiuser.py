"""Create (or refresh) the fleet-bot API user on the fleet site and print its key pair, once,
on a line the installer reads: `KEYS <api_key> <api_secret>`. Runs inside the bench:

    bench --site fleet.local execute "exec(open('<this file>').read(), {})"

The user carries the Fleet Manager role the fleet_ops methods require; a run on an existing
user issues a new key pair (the old one stops working), which is how a leaked pair is rotated.
Nothing else is printed.
"""
import frappe
from frappe.core.doctype.user.user import generate_keys

EMAIL = f"fleet-bot@{frappe.local.site}"
ROLE = "Fleet Manager"

if not frappe.db.exists("User", EMAIL):
    user = frappe.get_doc({"doctype": "User", "email": EMAIL, "first_name": "Fleet bot", "user_type": "System User",
                           "send_welcome_email": 0, "enabled": 1})
    user.insert(ignore_permissions=True)
user = frappe.get_doc("User", EMAIL)
if ROLE not in [r.role for r in user.roles]:
    user.append("roles", {"role": ROLE})
    user.save(ignore_permissions=True)
keys = generate_keys(EMAIL)            # sets api_key on the user, returns the secret once
frappe.db.commit()
api_key = frappe.db.get_value("User", EMAIL, "api_key")
print(f"KEYS {api_key} {keys['api_secret']}")
