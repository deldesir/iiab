# demo-seed.py — turn a fresh ERPNext site into a clickable demo shop (idempotent).
#
# Run inside the bench, on the site to seed:
#   bench --site site.local execute "exec(open('/opt/iiab/iiab/scripts/fleet-install/demo-seed.py').read(), {})"
# What it does: completes the setup wizard as "Demo Shop" (Haiti, HTG) if it has not run,
# then creates the demo catalogue ERPNext ships (item group, 10 items with pictures,
# customers, suppliers), sells prices, opening stock, a POS Profile for POS Next, and
# website items for the storefront when the webshop app is present. Administrator's
# password becomes DEMO_PASSWORD. It runs only on a site whose config carries demo: 1.
import json
import frappe
from frappe.utils import nowdate

# Refuse any site not marked as a demo (bench --site <site> set-config demo 1, which demo-rebuild.sh
# and demo-seed-drill.sh do for the sites they build): the seed resets Administrator's password to
# a public constant, so a real site must never be able to run it by accident.
if not frappe.conf.get("demo"):
    raise RuntimeError("demo seed refused: this site is not marked as a demo (set-config demo 1 on a demo site only)")

DEMO_PASSWORD = "demo1234"
COMPANY, ABBR = "Demo Shop", "DS"
ARGS = {
    "language": "English", "country": "Haiti", "timezone": "America/Port-au-Prince", "currency": "HTG",
    "full_name": "Demo Operator", "email": "demo@example.com", "password": DEMO_PASSWORD,
    "company_name": COMPANY, "company_abbr": ABBR, "chart_of_accounts": "Standard",
    "fy_start_date": f"{nowdate()[:4]}-01-01", "fy_end_date": f"{nowdate()[:4]}-12-31", "bank_account": "Demo Bank",
}
out = {"wizard": "already complete"}

if not frappe.is_setup_complete():
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
    out["wizard"] = setup_complete(ARGS)
    frappe.db.commit()

company = frappe.db.get_single_value("Global Defaults", "default_company") or COMPANY
if not frappe.db.exists("Company", company):
    raise SystemExit(f"DEMO-SEED-FAILED: no company after the wizard ({out['wizard']})")
warehouse = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "Stores"}, "name") \
    or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
cost_center = frappe.get_cached_value("Company", company, "cost_center")
currency = frappe.get_cached_value("Company", company, "default_currency")


def ensure(doctype, filters, values):
    """Insert a record once; return its name."""
    name = frappe.db.get_value(doctype, filters, "name")
    if name:
        return name
    doc = frappe.get_doc({"doctype": doctype, **filters, **values})
    doc.insert(ignore_permissions=True)
    return doc.name


from erpnext.setup.demo import read_data_file_using_hooks   # the JSON files ERPNext ships

counts = {}
for group_dt, parent in (("Item Group", "All Item Groups"), ("Customer Group", "All Customer Groups"), ("Supplier Group", "All Supplier Groups")):
    key = group_dt.lower().replace(" ", "_")
    for d in json.loads(read_data_file_using_hooks(key)):
        name_field = f"{key}_name"
        ensure(group_dt, {name_field: d[name_field]}, {f"parent_{key}": parent, "is_group": 0})

items = []
for d in json.loads(read_data_file_using_hooks("item")):
    d = {k: v for k, v in d.items() if k not in ("doctype", "gst_hsn_code")}
    name = ensure("Item", {"item_code": d["item_code"]}, {**d, "stock_uom": "Nos", "is_stock_item": 1, "include_item_in_manufacturing": 0})
    items.append((name, float(d.get("valuation_rate") or 100)))
counts["items"] = len(items)

for d in json.loads(read_data_file_using_hooks("customer")):
    ensure("Customer", {"customer_name": d["customer_name"]}, {"customer_group": d["customer_group"], "customer_type": "Company", "territory": "All Territories"})
for d in json.loads(read_data_file_using_hooks("supplier")):
    ensure("Supplier", {"supplier_name": d["supplier_name"]}, {"supplier_group": d["supplier_group"], "supplier_type": "Company"})
walk_in = ensure("Customer", {"customer_name": "Walk-in Customer"}, {"customer_group": "Individual", "customer_type": "Individual", "territory": "All Territories"})

price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
for code, rate in items:
    ensure("Item Price", {"item_code": code, "price_list": price_list}, {"price_list_rate": round(rate * 1.5, 2), "currency": currency, "selling": 1})
counts["prices"] = len(items)

# opening stock: one Material Receipt, only for items that have none yet
missing = [(c, r) for c, r in items if not frappe.db.get_value("Bin", {"item_code": c, "warehouse": warehouse}, "actual_qty")]
if missing:
    se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "purpose": "Material Receipt", "company": company,
                         "to_warehouse": warehouse, "posting_date": nowdate(),
                         "items": [{"item_code": c, "qty": 50, "basic_rate": r, "t_warehouse": warehouse, "uom": "Nos", "conversion_factor": 1} for c, r in missing]})
    se.insert(ignore_permissions=True)
    se.submit()
counts["stocked"] = len(missing)

# POS profile for POS Next
mop = "Cash"
if not frappe.db.get_value("Mode of Payment Account", {"parent": mop, "company": company}, "name"):
    cash_acct = frappe.get_cached_value("Company", company, "default_cash_account")
    if cash_acct:
        m = frappe.get_doc("Mode of Payment", mop)
        m.append("accounts", {"company": company, "default_account": cash_acct})
        m.save(ignore_permissions=True)
if not frappe.db.exists("POS Profile", "Demo POS"):
    p = frappe.get_doc({"doctype": "POS Profile", "name": "Demo POS", "company": company, "warehouse": warehouse,
                        "selling_price_list": price_list, "currency": currency, "customer": walk_in,
                        "write_off_account": frappe.get_cached_value("Company", company, "write_off_account"),
                        "write_off_cost_center": cost_center, "write_off_limit": 1, "disable_rounded_total": 1, "disabled": 0,
                        "payments": [{"mode_of_payment": mop, "default": 1}],
                        "applicable_for_users": [{"user": "Administrator", "default": 1}]})
    p.insert(ignore_permissions=True)
counts["pos_profile"] = "Demo POS"

# storefront: website items when the webshop app is installed
if "webshop" in frappe.get_installed_apps():
    from webshop.webshop.doctype.website_item.website_item import make_website_item
    n = 0
    for code, _ in items:
        if not frappe.db.exists("Website Item", {"item_code": code}):
            make_website_item(frappe.get_doc("Item", code), save=True, suppress_error=True)
            n += 1
    counts["website_items"] = n

# the documented login, whatever password the install gave Administrator
from frappe.utils.password import update_password
update_password("Administrator", DEMO_PASSWORD)
frappe.db.commit()
print("DEMO-SEED-OK", json.dumps({"company": company, "warehouse": warehouse, "price_list": price_list, **counts}))
