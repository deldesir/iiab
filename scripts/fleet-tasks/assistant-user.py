# The assistant's user on a box: run by `fleet assistant setup` through bench execute, as
#   exec(open("/home/frappe/fleet-assistant-user.py").read(), {"WRITER": False})
# Creates (or re-keys) one System User with a read-only role on what the retail line reads,
# plus a writer role when asked, and prints one line the fleet tool parses. Idempotent.
import json

import frappe

READ_ROLE, WRITE_ROLE = "Assistant Reader", "Assistant Writer"
READS = ["Sales Invoice", "Item", "Item Price", "Price List", "Bin", "Warehouse", "Customer", "Customer Group",
         "POS Opening Shift", "POS Closing Shift", "POS Profile", "Mode of Payment", "Company", "Payment Entry", "Comment"]
WRITES = {"Item Price": {"write": 1}, "Payment Entry": {"create": 1, "write": 1, "submit": 1}, "Comment": {"create": 1}}
EMAIL = f"assistant@{frappe.local.site}"


def ensure_role(name: str) -> None:
    if not frappe.db.exists("Role", name):
        frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 0}).insert(ignore_permissions=True)


def ensure_perm(doctype: str, role: str, **flags) -> None:
    if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
        doc = frappe.get_doc("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0})
        changed = False
        for k, v in {"read": 1, "select": 1, "report": 1, **flags}.items():
            if doc.get(k) != v:
                doc.set(k, v)
                changed = True
        if changed:
            doc.save(ignore_permissions=True)
        return
    frappe.get_doc({"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType", "parentfield": "permissions",
                    "role": role, "permlevel": 0, "read": 1, "select": 1, "report": 1, **flags}).insert(ignore_permissions=True)


writer = bool(globals().get("WRITER"))
ensure_role(READ_ROLE)
for dt in READS:
    if frappe.db.exists("DocType", dt):
        ensure_perm(dt, READ_ROLE)
if writer:
    ensure_role(WRITE_ROLE)
    for dt, flags in WRITES.items():
        if frappe.db.exists("DocType", dt):
            ensure_perm(dt, WRITE_ROLE, **flags)

roles = [READ_ROLE] + ([WRITE_ROLE] if writer else [])
if frappe.db.exists("User", EMAIL):
    user = frappe.get_doc("User", EMAIL)
else:
    user = frappe.get_doc({"doctype": "User", "email": EMAIL, "first_name": "Assistant", "last_name": "WhatsApp",
                           "user_type": "System User", "send_welcome_email": 0, "enabled": 1})
    user.insert(ignore_permissions=True)
user.enabled = 1
user.set("roles", [{"role": r} for r in roles])
user.api_key = frappe.generate_hash(length=15)
api_secret = frappe.generate_hash(length=15)
user.api_secret = api_secret
user.save(ignore_permissions=True)
frappe.clear_cache(user=EMAIL)
frappe.db.commit()
print("ASSISTANT-USER " + json.dumps({"email": EMAIL, "api_key": user.api_key, "api_secret": api_secret, "writer": writer}))
