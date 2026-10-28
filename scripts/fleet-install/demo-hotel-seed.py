# demo-hotel-seed.py — turn a fresh ERPNext + Hospitality PMS site into a clickable demo hotel
# (idempotent). The hotel profile of the demo box; the shop seed (demo-seed.py) is not needed.
#
# Run inside the bench, on the site to seed:
#   bench --site site.local execute "exec(open('/opt/iiab/iiab/scripts/fleet-install/demo-hotel-seed.py').read(), {})"
#
# What it does: completes the setup wizard as "Demo Hotel" (Haiti, HTG) if it has not run, then
# builds a 12-room hotel with a bar in the PMS and gives it a week of history, so every screen
# has something on it:
#
# * the property with its building, floors, departments, shifts, warehouses; three room types
#   with amenities, twelve rooms with features and a connecting pair; night, corporate, day-use
#   and hourly rate plans; cancellation, no-show and deposit policies; a New Year week at
#   seasonal rates with a minimum stay, and a day closed to arrivals;
# * the posting profile that turns folio charges into ERPNext invoices, with the service items
#   it needs, and payment modes with their accounts;
# * a staff: six users with the PMS roles (front desk, housekeeping, maintenance, night
#   auditor, manager, bartender), their Employee records when HR is installed;
# * guests with preferences, identification, consents and an allergy alert; a corporate
#   account with negotiated rates and credit; a wedding group block with its rooming list;
# * five closed night audits: two guests who stayed and checked out (ERPNext holds their
#   invoices and payments), one no-show, the room charges of the guests still in house;
# * a front desk with something to show on the business date: three guests in house, a day
#   room, an hourly booking, a walk-in, arrivals, a cancelled booking, a booking from a
#   channel manager, bookings for later, a guaranteed one, a tentative one;
# * the bar and restaurant: a stocked bar, a requisition from the stores, a wastage entry,
#   a restaurant bill signed to a room, a breakfast being prepared;
# * guest requests, a complaint, housekeeping tasks in every state, a room on Do Not Disturb,
#   a maintenance ticket in progress with its work log and a room out of order, key cards,
#   guest registrations for the authorities.
#
# Administrator's password becomes DEMO_PASSWORD, as does every demo user's. It runs only on a
# site whose config carries demo: 1.
import unicodedata

import re

import frappe
from datetime import timedelta

from frappe.utils import add_days, flt, get_datetime, getdate, nowdate

# Refuse any site not marked as a demo (bench --site <site> set-config demo 1, which demo-rebuild.sh
# and demo-seed-drill.sh do for the sites they build): the seed resets Administrator's password to
# a public constant, so a real site must never be able to run it by accident.
if not frappe.conf.get("demo"):
    raise RuntimeError("demo seed refused: this site is not marked as a demo (set-config demo 1 on a demo site only)")

DEMO_PASSWORD = "demo1234"
COMPANY, ABBR = "Demo Hotel", "DH"
PROPERTY = "DEMO"
ARGS = {
    "language": "English", "country": "Haiti", "timezone": "America/Port-au-Prince", "currency": "HTG",
    "full_name": "Demo Operator", "email": "demo@example.com", "password": DEMO_PASSWORD,
    "company_name": COMPANY, "company_abbr": ABBR, "chart_of_accounts": "Standard",
    "fy_start_date": f"{nowdate()[:4]}-01-01", "fy_end_date": f"{nowdate()[:4]}-12-31", "bank_account": "Demo Bank",
}

if "hospitality_pms" not in frappe.get_installed_apps():
    raise SystemExit("DEMO-HOTEL-SEED-FAILED: hospitality_pms is not installed on this site")

frappe.flags.mute_emails = True
out = {"wizard": "already complete"}
if not frappe.is_setup_complete():
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
    out["wizard"] = setup_complete(ARGS)
    frappe.db.commit()
company = frappe.db.get_single_value("Global Defaults", "default_company") or COMPANY
if not frappe.db.exists("Company", company):
    raise SystemExit(f"DEMO-HOTEL-SEED-FAILED: no company after the wizard ({out['wizard']})")

currency = frappe.get_cached_value("Company", company, "default_currency")
cost_center = frappe.get_cached_value("Company", company, "cost_center")
receivable = frappe.get_cached_value("Company", company, "default_receivable_account")
income = frappe.db.get_value("Account", {"company": company, "account_name": "Sales", "is_group": 0}, "name") \
    or frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 0}, "name")
today = nowdate()
D = lambda n: add_days(today, n)  # noqa: E731 - the business dates of the story, relative to today

from hospitality_pms.services import checkout as checkout_service
from hospitality_pms.services import folio as folio_service
from hospitality_pms.services import guest_services as request_service
from hospitality_pms.services import housekeeping as housekeeping_service
from hospitality_pms.services import kitchen as kitchen_service
from hospitality_pms.services import maintenance as maintenance_service
from hospitality_pms.services import night_audit as night_audit_service
from hospitality_pms.services import reservations as reservation_service
from hospitality_pms.services import rooms as room_service
from hospitality_pms.services import stays as stay_service
from hospitality_pms.services import walk_in as walk_in_service
from hospitality_pms.services.property import BUSINESS_DATE_FLAG


def ascii_slug(text):
    """A name as it can appear in an e-mail address: accents stripped, letters and digits only."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if c.isascii() and c.isalnum()).lower()


def fields_of(doctype):
    return {f.fieldname for f in frappe.get_meta(doctype).fields}


def ensure(doctype, filters, values):
    """Insert a record once; return its name. Keys the installed version does not know are dropped."""
    name = frappe.db.get_value(doctype, filters, "name")
    if name:
        return name
    known = fields_of(doctype)
    doc = frappe.get_doc({"doctype": doctype, **filters, **{k: v for k, v in values.items() if k in known}})
    doc.insert(ignore_permissions=True)
    return doc.name


def ensure_rows(doctype, name, table, key, rows):
    """Append child rows (matched on `key`) a document does not have yet."""
    doc = frappe.get_doc(doctype, name)
    have = {row.get(key) for row in doc.get(table) or []}
    added = [row for row in rows if row[key] not in have]
    if not added:
        return
    for row in added:
        doc.append(table, row)
    doc.save(ignore_permissions=True)
    frappe.db.commit()


def set_business_date(value):
    """Move the property's operating day the way the Night Audit does (the controller refuses
    any other writer). The history below is then written one day at a time and each day is
    closed by a real audit, which moves the date forward by itself."""
    frappe.flags[BUSINESS_DATE_FLAG] = True
    try:
        frappe.db.set_value("Property", PROPERTY, "business_date", getdate(value))
    finally:
        frappe.flags[BUSINESS_DATE_FLAG] = False
    frappe.db.commit()


def business_date():
    return getdate(frappe.db.get_value("Property", PROPERTY, "business_date"))


# 1. The service items folio charges post to (non-stock)
item_group = ensure("Item Group", {"item_group_name": "Hotel Services"}, {"parent_item_group": "All Item Groups", "is_group": 0})
for code, name in (("ROOM-NIGHT", "Room night"), ("ROOM-SERVICE", "Room service"), ("MINIBAR", "Minibar"),
                   ("LAUNDRY", "Laundry"), ("BREAKFAST", "Breakfast"), ("DAY-USE", "Day use"), ("HOURLY", "Hourly stay"),
                   ("TRANSPORT", "Airport transfer"), ("MISC", "Miscellaneous")):
    ensure("Item", {"item_code": code}, {"item_name": name, "item_group": item_group, "stock_uom": "Nos",
                                         "is_stock_item": 0, "is_sales_item": 1, "is_purchase_item": 0})

# 2. The property, opened a week ago so the history below can be written day by day
ensure("Property", {"property_code": PROPERTY}, {
    "property_name": COMPANY, "property_type": "Hotel", "star_rating": "3", "is_active": 1,
    "company": company, "currency": currency, "country": "Haiti", "time_zone": "America/Port-au-Prince",
    "default_language": "en", "business_date": D(-5),
    "check_in_time": "14:00:00", "check_out_time": "12:00:00", "total_rooms": 12,
    "receivable_account": receivable, "room_revenue_account": income, "default_price_list": "Standard Selling",
    "email": "frontdesk@example.com", "cost_center": cost_center,
})
frappe.db.set_single_value("PMS Settings", "default_property", PROPERTY)

# 2b. Where things are: one building, three floors, the pool zone; who works where: departments
# and shifts; the stores the kitchen and the bar draw from
building = ensure("Building", {"building_code": "MAIN"}, {"building_name": "Main building", "property": PROPERTY})
FLOORS = {level: ensure("Floor", {"floor_code": f"F{level}"}, {"floor_name": f"Floor {level}", "property": PROPERTY,
                                                                "building": building, "floor_level": level})
          for level in (1, 2, 3)}
ensure("Zone", {"zone_code": "POOL"}, {"zone_name": "Pool and garden", "property": PROPERTY, "zone_purpose": "Housekeeping"})
DEPARTMENTS = {}
for code, name, kind in (("FO", "Front office", "Front Office"), ("HK", "Housekeeping", "Housekeeping"),
                         ("MT", "Maintenance", "Maintenance"), ("FB", "Bar and restaurant", "Food and Beverage"),
                         ("KT", "Kitchen", "Kitchen")):
    DEPARTMENTS[code] = ensure("Property Department", {"department_code": code},
                               {"department_name": name, "property": PROPERTY, "department_type": kind, "is_active": 1})
DAYS = [{"day": d} for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]
for code, name, start, end, dept in (("AM", "Morning shift", "07:00:00", "15:00:00", "FO"), ("PM", "Evening shift", "15:00:00", "23:00:00", "FO"),
                                     ("NIGHT", "Night shift", "23:00:00", "07:00:00", "FO"), ("HK-AM", "Housekeeping day", "08:00:00", "16:00:00", "HK")):
    ensure("Property Shift", {"shift_code": code}, {"shift_name": name, "property": PROPERTY, "start_time": start, "end_time": end,
                                                    "crosses_midnight": 1 if end < start else 0,
                                                    "department": DEPARTMENTS[dept], "shift_days": DAYS})
stores = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "Stores"}, "name") \
    or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
bar_store = ensure("Warehouse", {"warehouse_name": "Bar", "company": company}, {"is_group": 0,
                   "parent_warehouse": frappe.db.get_value("Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ("is", "not set")}, "name")})
ensure_rows("Property", PROPERTY, "warehouses", "purpose", [{"purpose": "General Store", "warehouse": stores, "is_default": 1},
                                                             {"purpose": "Kitchen Store", "warehouse": stores},
                                                             {"purpose": "Minibar", "warehouse": bar_store}])

# 3. Room types, rooms (floor = type), rate plans
TYPES = {  # code: (name, max occupancy, nightly rate, bed configuration, amenities)
    "STD": ("Standard room", 2, 4500, "1 queen bed", ("Air conditioning", "Wi-Fi", "Hot water", "Safe")),
    "DLX": ("Deluxe room", 3, 7000, "1 king bed + sofa", ("Air conditioning", "Wi-Fi", "Hot water", "Safe", "Balcony", "Minibar")),
    "STE": ("Suite", 4, 12000, "1 king bed + living room", ("Air conditioning", "Wi-Fi", "Hot water", "Safe", "Balcony", "Minibar", "Living room", "Bathtub", "Sea view")),
}
for order, (code, (name, max_occ, rate, beds, amenities)) in enumerate(TYPES.items(), start=1):
    ensure("Room Type", {"room_type_code": code}, {
        "room_type_name": name, "property": PROPERTY, "is_active": 1, "display_order": order,
        "base_occupancy": 2, "max_occupancy": max_occ, "max_adults": max_occ, "max_children": 1,
        "bed_configuration": beds, "currency": currency, "base_rate": rate, "extra_adult_charge": 1000,
        "description": f"{name}: {beds}.",
    })
    ensure_rows("Room Type", code, "amenities", "amenity", [{"amenity": a, "quantity": 1} for a in amenities])
ROOMS = {}
for floor, code in ((1, "STD"), (2, "DLX"), (3, "STE")):
    for n in range(1, 5):
        number = f"{floor}0{n}"
        ROOMS[number] = ensure("Hotel Room", {"room_code": number}, {
            "room_number": number, "property": PROPERTY, "room_type": code, "is_active": 1,
            "occupancy_status": "Vacant", "housekeeping_status": "Clean", "maintenance_status": "Operational",
            "inventory_status": "Available", "view_type": "Sea view" if floor == 3 else "Garden",
            "has_balcony": 1 if floor >= 2 else 0, "building": building, "floor": FLOORS[floor],
        })
        if not frappe.db.get_value("Hotel Room", ROOMS[number], "floor"):
            frappe.db.set_value("Hotel Room", ROOMS[number], {"building": building, "floor": FLOORS[floor]}, update_modified=False)
        features = ["Sea view"] if floor == 3 else (["Balcony"] if floor == 2 else [])
        if number == "101":
            features.append("Near the lobby")
        if number in ("104", "204"):
            features.append("Accessible")
        if features:
            ensure_rows("Hotel Room", ROOMS[number], "features", "feature", [{"feature": f} for f in features])
ensure_rows("Hotel Room", ROOMS["201"], "connecting_rooms", "connecting_room", [{"connecting_room": ROOMS["202"]}])
ensure_rows("Hotel Room", ROOMS["202"], "connecting_rooms", "connecting_room", [{"connecting_room": ROOMS["201"]}])
ensure("Rate Plan", {"rate_plan_code": "BAR"}, {
    "rate_plan_name": "Best available rate", "property": PROPERTY, "rate_type": "Standard", "is_active": 1,
    "valid_from": D(-365), "currency": currency, "is_refundable": 1, "includes_breakfast": 1,
    "room_types": [{"room_type": c, "base_rate": t[2], "single_occupancy_rate": round(t[2] * 0.9),
                    "extra_adult_charge": 1000, "is_active": 1} for c, t in TYPES.items()],
})
ensure("Rate Plan", {"rate_plan_code": "CORP"}, {
    "rate_plan_name": "Corporate rate", "property": PROPERTY, "rate_type": "Corporate", "is_active": 1,
    "valid_from": D(-365), "currency": currency, "is_refundable": 1, "includes_breakfast": 1,
    "market_segment": "Corporate",
    "room_types": [{"room_type": c, "base_rate": round(t[2] * 0.85), "extra_adult_charge": 1000, "is_active": 1}
                   for c, t in TYPES.items()],
})

# 3b. Rooms sold for part of a day: a day room (09:00-18:00) and blocks of three hours. The PMS
# knows these kinds once it carries Rate Plan.stay_kind; an older PMS gets the night plans only.
SHORT_STAYS = frappe.db.has_column("Rate Plan", "stay_kind")
if SHORT_STAYS:
    ensure("Rate Plan", {"rate_plan_code": "DAYUSE"}, {
        "rate_plan_name": "Day use (09:00-18:00)", "property": PROPERTY, "rate_type": "Standard", "stay_kind": "Day Use",
        "day_use_from": "09:00:00", "day_use_until": "18:00:00", "is_active": 1, "valid_from": D(-365),
        "currency": currency, "is_refundable": 1,
        "room_types": [{"room_type": c, "base_rate": round(t[2] * 0.55), "is_active": 1} for c, t in TYPES.items()],
    })
    ensure("Rate Plan", {"rate_plan_code": "HOURLY"}, {
        "rate_plan_name": "Hourly (blocks of 3 h)", "property": PROPERTY, "rate_type": "Standard", "stay_kind": "Hourly",
        "block_hours": 3, "is_active": 1, "valid_from": D(-365), "currency": currency, "is_refundable": 0,
        "room_types": [{"room_type": c, "base_rate": round(t[2] * 0.3), "is_active": 1} for c, t in TYPES.items()],
    })

# 3c. Policies, a high season and a closed day: what the revenue manager's screens show
for code, name, kind, values in (
    ("FLEX48", "Free cancellation until 48 h before arrival", "Cancellation",
     {"charge_basis": "First Night", "free_cancellation_hours": 48, "description": "After 48 h the first night is charged."}),
    ("NOSHOW", "No-show: first night charged", "No Show", {"charge_basis": "First Night"}),
    ("DEP50", "Deposit: half the stay, due 7 days before arrival", "Deposit", {"deposit_percentage": 50, "deposit_due_days": 7}),
):
    ensure("Rate Policy", {"policy_code": code}, {"policy_name": name, "property": PROPERTY, "policy_type": kind, "is_active": 1,
                                                  "currency": currency, **values})
year = int(today[:4]) if today[5:] <= "12-28" else int(today[:4]) + 1
NEW_YEAR = [add_days(f"{year}-12-29", i) for i in range(5)]      # 29 Dec - 2 Jan at +25 %, two nights minimum
for date in NEW_YEAR:
    for code, t in TYPES.items():
        ensure("Daily Rate", {"property": PROPERTY, "rate_plan": "BAR", "room_type": code, "rate_date": date},
               {"rate": round(t[2] * 1.25), "currency": currency, "min_length_of_stay": 2})
if not frappe.db.exists("Room Inventory Restriction", {"property": PROPERTY, "restriction_date": f"{year}-12-31", "room_type": ("is", "not set")}):
    frappe.get_doc({"doctype": "Room Inventory Restriction", "property": PROPERTY, "restriction_date": f"{year}-12-31",
                    "closed_to_arrival": 1, "reason": "New Year's Eve dinner: arrivals by 30 December"}).insert(ignore_permissions=True)

# 4. Folio charges become ERPNext sales invoices through this profile
CHARGE_ITEMS = {"Room Charge": "ROOM-NIGHT", "Room Service": "ROOM-SERVICE", "Minibar": "MINIBAR", "Laundry": "LAUNDRY",
                "Day Use": "DAY-USE", "Hourly": "HOURLY", "Transport": "TRANSPORT", "Miscellaneous": "MISC"}
charge_types = set((frappe.get_meta("Folio Charge").get_field("charge_type").options or "").split("\n"))
CHARGE_ITEMS = {ct: it for ct, it in CHARGE_ITEMS.items() if ct in charge_types}
ensure("Posting Profile", {"profile_code": "DEMO-POST"}, {
    "profile_name": "Demo Hotel posting", "property": PROPERTY, "company": company, "is_active": 1,
    "default_item": "ROOM-NIGHT", "default_income_account": income, "default_cost_center": cost_center,
    "default_receivable_account": receivable,
    "charge_items": [{"charge_type": ct, "item": it, "income_account": income, "cost_center": cost_center, "is_active": 1}
                     for ct, it in CHARGE_ITEMS.items()],
})
ensure_rows("Posting Profile", frappe.db.get_value("Posting Profile", {"profile_code": "DEMO-POST"}, "name"), "charge_items", "charge_type",
            [{"charge_type": ct, "item": it, "income_account": income, "cost_center": cost_center, "is_active": 1} for ct, it in CHARGE_ITEMS.items()])
# payment modes with an account, so a folio payment can become an ERPNext payment entry
for mode, field in (("Cash", "default_cash_account"), ("Credit Card", "default_bank_account")):
    account = frappe.get_cached_value("Company", company, field)
    if account and frappe.db.exists("Mode of Payment", mode) \
            and not frappe.db.get_value("Mode of Payment Account", {"parent": mode, "company": company}, "name"):
        mop = frappe.get_doc("Mode of Payment", mode)
        mop.append("accounts", {"company": company, "default_account": account})
        mop.save(ignore_permissions=True)
frappe.db.commit()

# 5. Guests, with what the desk knows about them
GUESTS = {}
for first, last, country, vip in (
    ("Marie-Claire", "Jean", "Haiti", "Repeat Guest"), ("Jean-Baptiste", "Pierre", "Haiti", ""),
    ("Rose", "Delva", "Haiti", "VIP"), ("Patrick", "Louis", "Haiti", ""), ("Carlos", "Mendez", "Dominican Republic", ""),
    ("Emily", "Carter", "United States", "Loyalty Member"), ("Nadège", "Charles", "Haiti", ""),
    ("Wesley", "Desrosiers", "Haiti", ""), ("Lucienne", "Augustin", "Haiti", "Repeat Guest"),
    ("Fritz", "Moïse", "Haiti", ""), ("Rodrigue", "Saint-Fleur", "Haiti", ""), ("Daniel", "Jean-Louis", "Haiti", ""),
):
    GUESTS[first] = ensure("Guest", {"first_name": first, "last_name": last}, {
        "nationality": country, "country": country, "preferred_language": "en", "guest_type": "Individual",
        "vip_status": vip, "email_id": f"{ascii_slug(first)}.{ascii_slug(last)}@example.com",
        "mobile_no": "+509 3700 0000",
    })
ensure_rows("Guest", GUESTS["Rose"], "preferences", "preference", [
    {"preference_category": "Room", "preference": "High floor, sea view"},
    {"preference_category": "Bedding", "preference": "Feather-free pillows"},
    {"preference_category": "Food and Beverage", "preference": "Sparkling water in the room"}])
ensure_rows("Guest", GUESTS["Rose"], "consents", "consent_type", [{"consent_type": "Marketing Email", "is_granted": 1}])
ensure_rows("Guest", GUESTS["Rose"], "identifications", "id_number", [{"id_type": "Passport", "id_number": "HT-DEMO-4471", "issuing_country": "Haiti"}])
ensure_rows("Guest", GUESTS["Carlos"], "identifications", "id_number", [{"id_type": "Passport", "id_number": "DO-DEMO-9032", "issuing_country": "Dominican Republic"}])
ensure_rows("Guest", GUESTS["Emily"], "identifications", "id_number", [{"id_type": "Passport", "id_number": "US-DEMO-2210", "issuing_country": "United States"}])
ensure_rows("Guest", GUESTS["Emily"], "preferences", "preference", [{"preference_category": "Food and Beverage", "preference": "Vegetarian breakfast"}])
# the property asks for an identification document at check-in; the guests who check in have one
for key, kind, number in (("Jean-Baptiste", "National ID", "HT-DEMO-0183"), ("Patrick", "National ID", "HT-DEMO-5520"), ("Wesley", "Driving Licence", "HT-DEMO-7741"),
                          ("Lucienne", "National ID", "HT-DEMO-3307"), ("Rodrigue", "National ID", "HT-DEMO-6615"), ("Marie-Claire", "National ID", "HT-DEMO-1198"),
                          ("Daniel", "National ID", "HT-DEMO-2046")):
    ensure_rows("Guest", GUESTS[key], "identifications", "id_number", [{"id_type": kind, "id_number": number, "issuing_country": "Haiti"}])
ensure_rows("Guest", GUESTS["Patrick"], "alerts", "alert", [{"alert_type": "Allergy", "severity": "Warning", "alert": "Peanut allergy: no peanut oil in room-service meals", "is_active": 1}])
ensure_rows("Guest", GUESTS["Jean-Baptiste"], "alerts", "alert", [{"alert_type": "Payment", "severity": "Info", "alert": "Room and tax to the company (Caribe Trading); extras to the guest", "is_active": 1}])

# 5b. The staff: users with the PMS roles, and their Employee records when HR is installed.
# They work in French (the PMS, the desk and the server's messages follow the user's language);
# the operator login stays in English so the written tour reads as it is shown.
from frappe.utils.password import update_password
USERS = {}
STAFF_LANGUAGE = "fr"


def user(key, email, first, last, roles):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": first, "last_name": last, "enabled": 1,
                        "send_welcome_email": 0, "user_type": "System User", "language": STAFF_LANGUAGE,
                        "roles": [{"role": r} for r in roles if frappe.db.exists("Role", r)]}).insert(ignore_permissions=True)
    else:  # a rerun gives an existing user the roles added since
        existing = frappe.get_doc("User", email)
        missing = [r for r in roles if frappe.db.exists("Role", r) and r not in {x.role for x in existing.roles}]
        if missing:
            existing.add_roles(*missing)
    frappe.db.set_value("User", email, "language", STAFF_LANGUAGE, update_modified=False)
    update_password(email, DEMO_PASSWORD)
    USERS[key] = email
    return email


user("frontdesk", "frontdesk@example.com", "Yolande", "Prosper", ["Front Office Agent", "Reservation Agent", "Guest Relations Officer"])
user("housekeeping", "housekeeping@example.com", "Mirlande", "Joseph", ["Housekeeping Supervisor"])
user("maintenance", "maintenance@example.com", "Ernst", "Bélizaire", ["Maintenance Manager"])
user("auditor", "auditor@example.com", "Guerdy", "Lafontant", ["Night Auditor", "Finance Manager", "Accounts User"])
user("manager", "manager@example.com", "Dominique", "Étienne", ["Hotel Manager", "Front Office Manager", "Reservation Manager",
                                                                "Revenue Manager", "Housekeeping Manager", "Kitchen Manager", "Maintenance Manager",
                                                                # the setup records, the users, the till, the books
                                                                "Hospitality Administrator", "System Manager", "Accounts Manager",
                                                                "Stock Manager", "Sales Manager"])
user("bar", "bar@example.com", "Kervens", "Alexis", ["Kitchen User", "Sales User", "Accounts User", "Stock User"])
frappe.db.commit()
for code, key in (("FO", "frontdesk"), ("HK", "housekeeping"), ("MT", "maintenance"), ("FB", "bar"), ("KT", "bar")):
    if not frappe.db.get_value("Property Department", DEPARTMENTS[code], "manager"):
        frappe.db.set_value("Property Department", DEPARTMENTS[code], "manager", USERS["manager" if code == "FO" else key], update_modified=False)
if frappe.db.exists("DocType", "Employee"):
    for key, designation, gender, born in (("frontdesk", "Front Desk Agent", "Female", "1994-03-12"), ("housekeeping", "Housekeeping Supervisor", "Female", "1986-07-02"),
                                          ("maintenance", "Maintenance Technician", "Male", "1981-11-23"), ("auditor", "Night Auditor", "Male", "1990-05-30"),
                                          ("manager", "Hotel Manager", "Female", "1979-09-15"), ("bar", "Bartender", "Male", "1997-01-08")):
        email = USERS[key]
        if frappe.db.exists("Employee", {"user_id": email}):
            continue
        ensure("Designation", {"designation_name": designation}, {})
        first, last = frappe.db.get_value("User", email, ["first_name", "last_name"])
        frappe.get_doc({"doctype": "Employee", "first_name": first, "last_name": last, "employee_name": f"{first} {last}",
                        "company": company, "status": "Active", "gender": gender, "date_of_birth": born,
                        "date_of_joining": D(-400), "designation": designation, "user_id": email,
                        "create_user_permission": 0}).insert(ignore_permissions=True)
    frappe.db.commit()

# 5c. A company that sends people, and a wedding that blocks rooms
corporate_customer = ensure("Customer", {"customer_name": "Caribe Trading SA"}, {"customer_group": "Commercial", "customer_type": "Company", "territory": "All Territories"})
ensure("Corporate Account", {"account_code": "CTS"}, {
    "account_name": "Caribe Trading SA", "property": PROPERTY, "account_type": "Corporate", "credit_status": "Active",
    "customer": corporate_customer, "country": "Haiti", "currency": currency, "tax_id": "DEMO-000-123-4", "phone": "+509 2800 0000",
    "email": "travel@caribetrading.example.com", "credit_limit": 150000, "payment_terms_days": 30,
    "contract_start": D(-200), "contract_end": D(165), "contract_reference": "CTS-2026-01", "billing_rule": "Company Pays Room Only",
    "contacts": [{"contact_name": "Yvrose Baptiste", "designation": "Travel coordinator", "email": "yvrose@caribetrading.example.com",
                  "phone": "+509 2800 0001", "is_authorised_booker": 1, "is_billing_contact": 1}],
    "negotiated_rates": [{"room_type": "STD", "negotiated_rate": 3800, "rate_plan": "CORP"}, {"room_type": "DLX", "negotiated_rate": 5950, "rate_plan": "CORP"}],
})
if not frappe.db.get_value("Guest", GUESTS["Jean-Baptiste"], "corporate_account"):
    frappe.db.set_value("Guest", GUESTS["Jean-Baptiste"], "corporate_account", "CTS", update_modified=False)
GROUP_NAME = "Augustin family wedding"
if not frappe.db.exists("Group Reservation", {"property": PROPERTY, "group_name": GROUP_NAME}):
    frappe.get_doc({
        "doctype": "Group Reservation", "property": PROPERTY, "group_name": GROUP_NAME, "group_status": "Definite", "group_type": "Wedding",
        "arrival_date": D(21), "departure_date": D(23), "billing_instruction": "Individual Folios", "currency": currency, "total_rooms_blocked": 6,
        "contact_name": "Lucienne Augustin", "contact_email": "lucienne.augustin@example.com", "contact_phone": "+509 3700 0000",
        "notes": "Reception on the terrace on the first evening; late breakfast the next day.",
        "block_lines": [{"room_type": "STD", "rooms_blocked": 4, "arrival_date": D(21), "departure_date": D(23), "rate_plan": "BAR", "negotiated_rate": 4000},
                        {"room_type": "DLX", "rooms_blocked": 2, "arrival_date": D(21), "departure_date": D(23), "rate_plan": "BAR", "negotiated_rate": 6500}],
        "rooming_list": [{"guest_name": "Lucienne Augustin", "guest": GUESTS["Lucienne"], "room_type": "DLX", "arrival_date": D(21), "departure_date": D(23), "adults": 2},
                         {"guest_name": "Pierre and Ketly Augustin", "room_type": "STD", "arrival_date": D(21), "departure_date": D(23), "adults": 2},
                         {"guest_name": "Jacques Augustin", "room_type": "STD", "arrival_date": D(21), "departure_date": D(23), "adults": 1}],
    }).insert(ignore_permissions=True)
    frappe.db.commit()


# 6. Reservations (keyed by an external reference so a rerun finds them instead of booking twice)
def reservation(ref, guest, room_type, arrival, nights, *, rate_plan="BAR", kind="Individual", adults=2, requests=None,
                arrival_time=None, departure_time=None, blocks=1, source="Direct"):
    """nights 0 is a day-use or hourly stay: same-day, with its hours (the rate plan says which kind)."""
    name = frappe.db.get_value("Reservation", {"external_reference": ref}, "name")
    if name:
        return name
    doc = frappe.get_doc({
        "doctype": "Reservation", "property": PROPERTY, "reservation_status": "Draft", "reservation_type": kind,
        "booking_source": source, "external_reference": ref, "guest": GUESTS[guest],
        "arrival_date": arrival, "departure_date": add_days(arrival, nights), "rate_plan": rate_plan,
        "arrival_time": arrival_time, "departure_time": departure_time,
        "cancellation_policy": "FLEX48", "no_show_policy": "NOSHOW",
        "special_requests": requests,
        "rooms": [{"room_type": room_type, "rooms": 1, "adults": adults, "arrival_date": arrival,
                   "departure_date": add_days(arrival, nights), "rate_plan": rate_plan,
                   "arrival_time": arrival_time, "departure_time": departure_time, "blocks": blocks}],
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def status(name):
    return frappe.db.get_value("Reservation", name, "reservation_status")


def first_line(name):
    return frappe.get_doc("Reservation", name).rooms[0].name


def confirmed(ref, *args, room=None, **kwargs):
    """A confirmed reservation, with a room assigned when asked."""
    name = reservation(ref, *args, **kwargs)
    if status(name) == "Draft":
        reservation_service.confirm(name)
        frappe.db.commit()
    if room and not frappe.db.get_value("Reservation Room", first_line(name), "assigned_room"):
        reservation_service.assign_room(name, first_line(name), ROOMS[room])
        frappe.db.commit()
    return name


def stay_of(reservation_name):
    return frappe.db.get_value("Stay", {"reservation": reservation_name}, "name")


def in_house(ref, *args, room, charges=(), payment=None, billing_instructions=None, **kwargs):
    """A guest who has checked in, with a few folio lines. `payment` is an amount (cash) or (amount, method)."""
    name = confirmed(ref, *args, room=room, **kwargs)
    if status(name) != "Checked In":
        stay_service.check_in(name, first_line(name), ROOMS[room], skip_id_check=True, billing_instructions=billing_instructions)
        frappe.db.commit()
    folio = folio_service.get_folio_for_stay(stay_of(name))
    for i, (charge_type, description, amount) in enumerate(charges, start=1):
        folio_service.post_charge(folio, charge_type, description, amount, idempotency_key=f"demo:{ref}:charge{i}")
    if payment:
        amount, method = payment if isinstance(payment, tuple) else (payment, "Cash")
        folio_service.post_payment(folio, amount, method, idempotency_key=f"demo:{ref}:payment")
    frappe.db.commit()
    return name


def checked_out(ref, method="Cash"):
    """Settle what the folio still owes and check the guest out: ERPNext gets the invoice and the payment."""
    stay = stay_of(frappe.db.get_value("Reservation", {"external_reference": ref}, "name"))
    if frappe.db.get_value("Stay", stay, "stay_status") == "Checked Out":
        return stay
    folio = folio_service.get_folio_for_stay(stay)
    balance = folio_service.get_balance(folio)
    owed = balance["balance"] if isinstance(balance, dict) else balance
    if owed and float(owed) > 0:
        folio_service.post_payment(folio, float(owed), method, idempotency_key=f"demo:{ref}:settle")
        frappe.db.commit()
    checkout_service.check_out(stay)
    frappe.db.commit()
    return stay


def audit_and_close(date):
    """Close a business date the way the night auditor does: review, mark the no-shows, review again,
    post the room charges, flag the due-outs, reconcile with ERPNext, close (the date moves on)."""
    if frappe.db.exists("Night Audit", {"property": PROPERTY, "business_date": date, "audit_status": "Closed"}):
        return
    audit = night_audit_service.start(PROPERTY, date)
    night_audit_service.review(audit)
    night_audit_service.mark_no_shows(audit)
    night_audit_service.review(audit)
    night_audit_service.post_room_charges(audit)
    night_audit_service.mark_due_outs(audit)
    night_audit_service.reconcile(audit)
    night_audit_service.close(audit)
    frappe.db.commit()


# The week behind the front desk, one business date at a time, each closed by a real night audit
# (so the folios carry their room charges, ERPNext holds the invoices of the guests who left, and
# the audit list has history). Written once: a rerun finds the last audit closed and skips it.
HISTORY_DONE = bool(frappe.db.exists("Night Audit", {"property": PROPERTY, "business_date": D(-1), "audit_status": "Closed"}))
if not HISTORY_DONE:
    if business_date() > getdate(D(-5)):
        set_business_date(D(-5))
    # D-5: Wesley arrives for two nights, pays a deposit by card
    in_house("DEMO-H1", "Wesley", "DLX", D(-5), 2, room="202", adults=2, requests="Quiet room",
             charges=(("Room Service", "Breakfast for two, in the room", 1300),), payment=(7000, "Credit Card"))
    audit_and_close(D(-5))
    # D-4: Lucienne arrives for two nights
    in_house("DEMO-H2", "Lucienne", "DLX", D(-4), 2, room="203", adults=1, charges=(("Minibar", "Minibar", 300),))
    audit_and_close(D(-4))
    # D-3: Wesley leaves; his invoice and payment go to ERPNext
    checked_out("DEMO-H1", "Credit Card")
    audit_and_close(D(-3))
    # D-2: Lucienne leaves; Rose arrives for two nights
    checked_out("DEMO-H2", "Cash")
    in_house("DEMO-03", "Rose", "STE", D(-2), 2, room="301", requests="Late check-out if possible",
             charges=(("Laundry", "Laundry, 4 pieces", 600),), payment=10000)
    audit_and_close(D(-2))
    # D-1: Jean-Baptiste arrives for the company; Fritz never shows up and the audit marks him
    in_house("DEMO-02", "Jean-Baptiste", "DLX", D(-1), 3, room="201", rate_plan="CORP", kind="Corporate",
             adults=1, charges=(("Minibar", "Minibar", 450),),
             billing_instructions="Room and tax to Caribe Trading SA (account CTS, 30 days); extras paid by the guest")
    # Daniel takes 102 for one night and leaves this morning: today's departure clean, and a
    # third invoice in the books, dated today
    in_house("DEMO-H3", "Daniel", "STD", D(-1), 1, room="102", adults=1)
    confirmed("DEMO-NS", "Fritz", "STD", D(-1), 1, adults=1)
    audit_and_close(D(-1))
if business_date() != getdate(today):
    set_business_date(today)

# Today: Daniel checks out first thing (the check-out raises 102's departure clean)
if frappe.db.exists("Reservation", {"external_reference": "DEMO-H3"}):
    checked_out("DEMO-H3", "Cash")
if SHORT_STAYS:
    # Patrick has a day room for the afternoon (charged at check-in); Nadège comes at 17:00 for two
    # blocks of three hours, booked but not yet in (103 is free again from 16:00).
    in_house("DEMO-DAY", "Patrick", "STD", today, 0, room="103", rate_plan="DAYUSE", adults=1,
             arrival_time="10:00:00", departure_time="16:00:00", requests="Day room, pool afternoon")
    confirmed("DEMO-HOUR", "Nadège", "STD", today, 0, rate_plan="HOURLY", adults=2, arrival_time="17:00:00", blocks=2)
confirmed("DEMO-01", "Marie-Claire", "STD", today, 2, room="101", requests="Ground floor, near the lobby")
confirmed("DEMO-05", "Carlos", "DLX", today, 1, adults=1)
confirmed("DEMO-04", "Patrick", "STD", D(7), 1)
emily = confirmed("DEMO-06", "Emily", "STE", D(3), 4, kind="Direct Website", source="Website")
if status(emily) == "Confirmed":
    try:
        reservation_service.guarantee(emily, "Credit Card")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
nadege = reservation("DEMO-07", "Nadège", "STD", D(10), 2)
if status(nadege) == "Draft":
    try:
        reservation_service.make_tentative(nadege)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
# Carlos also had a second trip booked, and called it off
cancelled = confirmed("DEMO-CXL", "Carlos", "DLX", D(4), 2, adults=1)
if status(cancelled) == "Confirmed":
    reservation_service.cancel(cancelled, "Trip postponed; the guest will rebook")
    frappe.db.commit()
# Rodrigue walked in this morning for one night and paid cash
if not frappe.db.exists("Reservation", {"guest": GUESTS["Rodrigue"], "arrival_date": today, "reservation_type": "Walk In"}):
    result = walk_in_service.create_walk_in(property_name=PROPERTY, guest=GUESTS["Rodrigue"], departure_date=D(1), room_type="STD",
                                            room=ROOMS["104"], adults=1, rate_plan="BAR", special_requests="Paid on arrival")
    frappe.db.commit()
    folio_service.post_payment(folio_service.get_folio_for_stay(result["stay"]), 4500, "Cash", idempotency_key="demo:walkin:payment")
    frappe.db.commit()
# A booking from the channel manager (the sandbox connection the integrations screen shows)
ensure("Booking Channel", {"channel_code": "OTA-DEMO"}, {
    "channel_name": "Demo channel manager", "property": PROPERTY, "channel_type": "Channel Manager", "provider": "Generic Channel Manager",
    "environment": "Sandbox", "is_active": 1, "default_rate_plan": "BAR",
    "room_mappings": [{"room_type": c, "channel_room_code": c, "rate_plan": "BAR"} for c in TYPES],
})
if not frappe.db.exists("Channel Reservation", {"channel": "OTA-DEMO", "channel_reservation_id": "OTA-100245"}):
    from hospitality_pms.services import channel as channel_service
    channel_service.import_reservation(PROPERTY, "OTA-DEMO", {
        "channel_reservation_id": "OTA-100245", "message_type": "New", "guest_name": "Sophie Martin",
        "guest_email": "sophie.martin@example.com", "guest_phone": "+33 6 00 00 00 00",
        "arrival_date": D(5), "departure_date": D(8), "room_type_code": "DLX", "rate_code": "BAR",
        "rooms": 1, "adults": 2, "children": 0, "total_amount": 21000, "currency": currency, "commission_amount": 3150,
    })
    frappe.db.commit()

# 6b. The bar and restaurant: menu items backed by ERPNext items with stock and prices (the bar
# sells them at the POS Next till through the "Bar" profile), and one restaurant order signed to
# a room, which the PMS charges to the folio on delivery.
warehouse = stores
price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
bar_group = ensure("Item Group", {"item_group_name": "Bar and restaurant"}, {"parent_item_group": "All Item Groups", "is_group": 0})
MENU = (  # menu code, name, category, ERPNext item, selling rate, cost, minibar item
    ("PRESTIGE", "Prestige beer", "Alcoholic Beverage", "BAR-PRESTIGE", 250, 120, 1),
    ("RHUM", "Barbancourt rum, 5 cl", "Alcoholic Beverage", "BAR-RHUM", 350, 150, 0),
    ("COLA", "Soft drink", "Beverage", "BAR-COLA", 125, 60, 1),
    ("WATER", "Bottled water", "Beverage", "BAR-WATER", 75, 30, 1),
    ("GRIOT", "Griot with pikliz", "Main Course", "BAR-GRIOT", 950, 400, 0),
    ("CLUB", "Club sandwich", "Snack", "BAR-CLUB", 650, 250, 0),
)
bar_items = []
for code, name, category, item_code, rate, cost, minibar in MENU:
    ensure("Item", {"item_code": item_code}, {"item_name": name, "item_group": bar_group, "stock_uom": "Nos", "is_stock_item": 1,
                                              "is_sales_item": 1, "include_item_in_manufacturing": 0, "valuation_rate": cost})
    ensure("Item Price", {"item_code": item_code, "price_list": price_list}, {"price_list_rate": rate, "currency": currency, "selling": 1})
    ensure("Menu Item", {"menu_item_code": code}, {"menu_item_name": name, "property": PROPERTY, "is_active": 1, "category": category,
                                                   "item": item_code, "selling_rate": rate, "currency": currency,
                                                   "is_available_for_room_service": 1, "is_minibar_item": minibar})
    bar_items.append((item_code, cost))
missing = [(c, r) for c, r in bar_items if not frappe.db.get_value("Bin", {"item_code": c, "warehouse": warehouse}, "actual_qty")]
if missing:
    se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "purpose": "Material Receipt", "company": company,
                         "to_warehouse": warehouse, "posting_date": today,
                         "items": [{"item_code": c, "qty": 48, "basic_rate": r, "t_warehouse": warehouse, "uom": "Nos", "conversion_factor": 1}
                                   for c, r in missing]})
    se.insert(ignore_permissions=True)
    se.submit()
walk_in = ensure("Customer", {"customer_name": "Walk-in Customer"}, {"customer_group": "Individual", "customer_type": "Individual", "territory": "All Territories"})
if not frappe.db.exists("POS Profile", "Bar"):
    frappe.get_doc({"doctype": "POS Profile", "name": "Bar", "company": company, "warehouse": bar_store,
                    "selling_price_list": price_list, "currency": currency, "customer": walk_in,
                    "write_off_account": frappe.get_cached_value("Company", company, "write_off_account"),
                    "write_off_cost_center": cost_center, "write_off_limit": 1, "disable_rounded_total": 1, "disabled": 0,
                    "payments": [{"mode_of_payment": "Cash", "default": 1}],
                    "applicable_for_users": [{"user": "Administrator", "default": 1}]}).insert(ignore_permissions=True)
ensure_rows("POS Profile", "Bar", "applicable_for_users", "user", [{"user": USERS["bar"], "default": 1}, {"user": USERS["manager"]}])
if frappe.db.get_value("POS Profile", "Bar", "warehouse") == warehouse:   # the till sells from the bar fridge, stocked below
    frappe.db.set_value("POS Profile", "Bar", "warehouse", bar_store, update_modified=False)
# The bar till charges rooms: a bill put on the guest's account lands on the folio (the PMS's
# pos bridge, through this POS Profile flag) and POS Next must allow the sale on account.
if frappe.db.has_column("POS Profile", "hpms_charge_to_room"):
    frappe.db.set_value("POS Profile", "Bar", "hpms_charge_to_room", 1, update_modified=False)
# The till sells the bar's menu only: without item groups it lists every sales item, room nights
# and laundry included, at zero.
ensure_rows("POS Profile", "Bar", "item_groups", "item_group", [{"item_group": bar_group}])
if frappe.db.exists("DocType", "POS Settings"):
    ensure("POS Settings", {"pos_profile": "Bar"}, {"enabled": 1, "allow_credit_sale": 1})
    frappe.db.set_value("POS Settings", {"pos_profile": "Bar"}, "allow_credit_sale", 1, update_modified=False)
# Walk-ins at the pool or the bar buy at the same till for cash: a day pass is an ordinary item
ensure("Item", {"item_code": "POOL-DAY"}, {"item_name": "Pool day pass", "item_group": bar_group, "stock_uom": "Nos",
                                            "is_stock_item": 0, "is_sales_item": 1, "is_purchase_item": 0,
                                            "standard_rate": 1500})
ensure("Item Price", {"item_code": "POOL-DAY", "price_list": price_list}, {"price_list_rate": 1500, "selling": 1})
if frappe.db.get_value("Item", "POOL-DAY", "item_group") != bar_group:   # sold at the bar till, so in the bar's group
    frappe.db.set_value("Item", "POOL-DAY", "item_group", bar_group, update_modified=False)
# The bar is stocked from the stores through a kitchen requisition (an ERPNext stock transfer
# behind it; the till sells from the bar's own warehouse), and what spoiled is written off
if not frappe.db.exists("Kitchen Requisition", {"property": PROPERTY, "to_warehouse": bar_store}):
    req = kitchen_service.create_requisition(PROPERTY, [{"item": item_code, "quantity": 24} for item_code, _cost in bar_items],
                                             from_warehouse=stores, to_warehouse=bar_store, department=DEPARTMENTS["FB"],
                                             notes="Weekend restock of the bar fridge")
    kitchen_service.issue_requisition(req)
    frappe.db.commit()
if not frappe.db.exists("Wastage Entry", {"property": PROPERTY, "item": "BAR-GRIOT"}):
    kitchen_service.record_wastage(PROPERTY, "BAR-GRIOT", 2, "Spoilage", "Two portions past their date after the fridge failure",
                                   warehouse=stores, department=DEPARTMENTS["KT"])
    frappe.db.commit()
# Rose had dinner at the restaurant and signed it to her room; Jean-Baptiste's breakfast is being made
rose_stay = stay_of(frappe.db.get_value("Reservation", {"external_reference": "DEMO-03"}, "name"))
if rose_stay and not frappe.db.exists("Room Service Order", {"stay": rose_stay, "order_type": "Restaurant"}):
    order = kitchen_service.create_order(PROPERTY, [{"menu_item": "GRIOT", "quantity": 2}, {"menu_item": "PRESTIGE", "quantity": 2}],
                                         stay=rose_stay, order_type="Restaurant", special_instructions="Table 4, signed to room 301")
    kitchen_service.deliver_order(order)
    frappe.db.commit()
jb_stay = stay_of(frappe.db.get_value("Reservation", {"external_reference": "DEMO-02"}, "name"))
if jb_stay and not frappe.db.exists("Room Service Order", {"stay": jb_stay, "order_type": "Room Service"}):
    order = kitchen_service.create_order(PROPERTY, [{"menu_item": "CLUB", "quantity": 1}, {"menu_item": "COLA", "quantity": 1}],
                                         stay=jb_stay, order_type="Room Service", special_instructions="No mayonnaise")
    kitchen_service.set_order_status(order, "Preparing")
    frappe.db.commit()

# 6c. Jean-Baptiste's lunch at the bar, put on his room: a till sale on account (no payment row,
# the way POS Next's "Pay on Account" sends it), which the PMS bridge charges to his folio.
jb_customer = frappe.db.get_value("Guest", GUESTS["Jean-Baptiste"], "customer")
if jb_stay and jb_customer and frappe.db.exists("POS Profile", "Bar") and not frappe.db.exists("Sales Invoice", {"customer": jb_customer, "is_pos": 1, "docstatus": 1}):
    lunch = frappe.get_doc({"doctype": "Sales Invoice", "company": company, "customer": jb_customer, "is_pos": 1, "pos_profile": "Bar",
                            "update_stock": 1, "set_warehouse": bar_store, "posting_date": today, "set_posting_time": 1,
                            "items": [{"item_code": "BAR-PRESTIGE", "qty": 2, "warehouse": bar_store}, {"item_code": "BAR-CLUB", "qty": 1, "warehouse": bar_store}]})
    lunch.flags.ignore_permissions = True
    lunch.flags.pos_next_credit_sale = 1
    lunch.insert()
    lunch.submit()
    frappe.db.commit()
# 7. What the guests asked for, and a complaint
marie = frappe.db.get_value("Reservation", {"external_reference": "DEMO-01"}, "name")


def request(subject, *, assignee=None, progress="open", resolution=None, **kwargs):
    """A guest request, taken as far as `progress` says: open, assigned, started or completed."""
    name = frappe.db.get_value("Guest Request", {"property": PROPERTY, "subject": subject}, "name") \
        or request_service.create_request(PROPERTY, subject=subject, **kwargs)
    state = frappe.db.get_value("Guest Request", name, "request_status")
    if assignee and state == "Open" and progress != "open":
        request_service.assign(name, assignee)
        state = "Assigned"
    if state == "Assigned" and progress in ("started", "completed"):
        request_service.start(name)
        state = "In Progress"
    if state == "In Progress" and progress == "completed":
        request_service.complete(name, resolution, guest_satisfied=True)
    return name


request("Two extra pillows", description="Feather-free, please", category="Housekeeping", stay=rose_stay, room=ROOMS["301"],
        guest=GUESTS["Rose"], department=DEPARTMENTS["HK"], assignee=USERS["housekeeping"], progress="completed",
        resolution="Two feather-free pillows delivered at 15:20")
request("Wake-up call at 06:00", description="Early meeting in Port-au-Prince", category="Front Office", stay=jb_stay, room=ROOMS["201"],
        guest=GUESTS["Jean-Baptiste"], department=DEPARTMENTS["FO"], assignee=USERS["frontdesk"], progress="assigned")
request("Wi-Fi drops every few minutes in 201", description="The guest works from the room and cannot hold a video call",
        category="Maintenance", request_type="Complaint", priority="High", stay=jb_stay, room=ROOMS["201"],
        guest=GUESTS["Jean-Baptiste"], department=DEPARTMENTS["MT"], assignee=USERS["maintenance"], progress="started")
request("Airport pickup at 15:00", description="Flight from Miami lands at 14:10; two passengers, three bags", category="Transport",
        reservation=marie, guest=GUESTS["Marie-Claire"], department=DEPARTMENTS["FO"])
frappe.db.commit()

# 8. Housekeeping and maintenance, so the boards show every state
if frappe.db.get_value("Hotel Room", ROOMS["102"], "housekeeping_status") == "Clean":   # only if the check-out did not
    room_service.set_status(ROOMS["102"], room_service.HOUSEKEEPING, "Dirty", reason="Daniel Jean-Louis checked out this morning")
    frappe.db.commit()


def open_task(room, task_type="Departure Clean", **kwargs):
    """The room's task of this type, created once (a completed one on a rerun is still that task)."""
    name = frappe.db.get_value("Housekeeping Task", {"room": ROOMS[room], "task_type": task_type, "task_status": ("!=", "Cancelled")}, "name")
    if name:
        return name, False
    return housekeeping_service.create_task(PROPERTY, ROOMS[room], task_type=task_type, **kwargs), True


task, new = open_task("102", task_type="Departure Clean", priority="High")
if frappe.db.get_value("Housekeeping Task", task, "task_status") == "Pending":
    housekeeping_service.assign(task, USERS["housekeeping"])
# the rooms the week's departures left, cleaned and inspected days ago
for room, finish in (("203", True), ("202", True)):
    task, new = open_task(room, task_type="Departure Clean")
    state = frappe.db.get_value("Housekeeping Task", task, "task_status")
    if state == "Pending":
        housekeeping_service.assign(task, USERS["housekeeping"]); state = "Assigned"
    if state == "Assigned":
        housekeeping_service.start(task); state = "In Progress"
    if finish and state == "In Progress":
        housekeeping_service.complete(task, minutes=35, minibar_checked=True)
        state = frappe.db.get_value("Housekeeping Task", task, "task_status")
    if finish and state == "Inspection Pending":
        housekeeping_service.inspect(task, passed=True, notes="Ready for the next guest", score=9)
    frappe.db.commit()
# the stayover clean of 201 is being done now; Rose does not want to be disturbed this morning
task, new = open_task("201", task_type="Stayover Clean", source_stay=jb_stay)
state = frappe.db.get_value("Housekeeping Task", task, "task_status")
if state == "Pending":
    housekeeping_service.assign(task, USERS["housekeeping"]); state = "Assigned"
if state == "Assigned":
    housekeeping_service.start(task)
frappe.db.commit()
task, new = open_task("301", task_type="Stayover Clean", source_stay=rose_stay)
if frappe.db.get_value("Housekeeping Task", task, "task_status") in ("Pending", "Assigned"):
    housekeeping_service.set_do_not_disturb(task)
    frappe.db.commit()
TICKET_TITLE = "Air conditioning not cooling"
ticket = frappe.db.get_value("Maintenance Ticket", {"room": ROOMS["304"], "title": TICKET_TITLE}, "name")
if not ticket:
    ticket = maintenance_service.create_ticket(PROPERTY, title=TICKET_TITLE, room=ROOMS["304"],
                                               description="Guest reported warm air; technician booked for tomorrow.",
                                               category="HVAC", priority="High")
    frappe.db.commit()
if not frappe.db.get_value("Maintenance Ticket", ticket, "takes_room_out_of_service"):
    # a submitted Room Block takes the room out of sale, so availability and the rack agree
    maintenance_service.take_out_of_service(ticket, "Out of Order", "Air conditioning failed", until_date=D(2))
    frappe.db.commit()
if not frappe.db.get_value("Maintenance Ticket", ticket, "assigned_to"):
    maintenance_service.assign(ticket, USERS["maintenance"])
    frappe.db.commit()
if frappe.db.get_value("Maintenance Ticket", ticket, "ticket_status") == "Open":
    maintenance_service.start_work(ticket)
    frappe.db.commit()
if not frappe.get_doc("Maintenance Ticket", ticket).get("work_logs"):
    maintenance_service.log_work(ticket, "Compressor tested, run capacitor replaced; cools for ten minutes then warms again. Condenser fan motor ordered.",
                                 minutes=45, parts="Run capacitor 35 µF")
    frappe.db.commit()
TAP_TITLE = "Dripping tap in the bathroom"
if not frappe.db.exists("Maintenance Ticket", {"room": ROOMS["102"], "title": TAP_TITLE}):
    maintenance_service.create_ticket(PROPERTY, title=TAP_TITLE, room=ROOMS["102"], description="Mentioned by the departing guest at check-out this morning.",
                                      category="Plumbing", priority="Low")
    frappe.db.commit()

# 8b. Times that follow the story. Everything above was written in one minute; the records keep
# the business dates they belong to, but their timestamps all say "now". Guests check in in the
# afternoon, leave in the morning, audits close after midnight, and the logs follow suit, so a
# trainer who opens a stay, an audit or a folio journal reads a real week.
def at(date, hhmm):
    return get_datetime(f"{getdate(date)} {hhmm}:00")
def stamp(doctype, name, values):
    frappe.db.set_value(doctype, name, {k: v for k, v in values.items() if frappe.db.has_column(doctype, k)}, update_modified=False)
for order in frappe.get_all("Room Service Order", filters={"property": PROPERTY}, fields=["name", "order_status"]):
    if order.order_status == "Delivered":
        stamp("Room Service Order", order.name, {"creation": at(D(-1), "19:40"), "ordered_on": at(D(-1), "19:40"), "delivered_on": at(D(-1), "20:25")})
    else:
        stamp("Room Service Order", order.name, {"creation": at(today, "12:05"), "ordered_on": at(today, "12:05")})
for stay in frappe.get_all("Stay", filters={"property": PROPERTY}, fields=["name", "arrival_date", "departure_date", "stay_status", "reservation", "folio"]):
    res = frappe.db.get_value("Reservation", stay.reservation, ["reservation_type", "arrival_time"], as_dict=True)
    kind = res.reservation_type
    if res.arrival_time:
        arrived = get_datetime(f"{getdate(stay.arrival_date)} {str(res.arrival_time)[:5]}:00") + timedelta(minutes=5)
    else:
        arrived = at(stay.arrival_date, "09:40" if kind == "Walk In" else "14:05")
    left = at(stay.departure_date, "11:20") if stay.stay_status == "Checked Out" else None
    booked = arrived if kind == "Walk In" else at(add_days(stay.arrival_date, -3), "10:15")
    stamp("Stay", stay.name, {"checked_in_on": arrived, "checked_out_on": left, "creation": arrived})
    stamp("Reservation", stay.reservation, {"checked_in_on": arrived, "checked_out_on": left, "booked_on": booked, "confirmed_on": booked, "creation": booked})
    if not stay.folio:
        continue
    stamp("Guest Folio", stay.folio, {"opened_on": arrived, "closed_on": left, "creation": arrived})
    posted = {}      # charge row -> when it was posted
    for row in frappe.get_all("Folio Charge", filters={"parent": stay.folio}, fields=["name", "business_date", "charge_type", "total_amount", "description"], order_by="idx asc"):
        business = row.business_date
        if row.charge_type == "Room Charge":
            when = at(business, "23:35")
        elif row.charge_type in ("Day Use", "Hourly"):
            when = arrived
        elif row.charge_type == "Point of Sale":
            when = at(business, "13:20")
        elif row.charge_type == "Room Service" and "HPMS-RSO" in (row.description or ""):
            order = re.search(r"HPMS-RSO-[0-9-]+", row.description).group(0)
            when = frappe.db.get_value("Room Service Order", order, "delivered_on") or at(business, "15:10")
            business = getdate(when)
        else:
            when = arrived + timedelta(minutes=65)
        posted[row.name] = (when, flt(row.total_amount))
        stamp("Folio Charge", row.name, {"posted_on": when, "charge_date": business, "business_date": business})
    received = {}
    for row in frappe.get_all("Folio Payment", filters={"parent": stay.folio}, fields=["name", "business_date", "amount"], order_by="idx asc"):
        if left and getdate(row.business_date) == getdate(stay.departure_date):
            when = left
        elif getdate(row.business_date) == getdate(stay.arrival_date):
            when = arrived + timedelta(minutes=25)
        else:
            when = at(row.business_date, "14:30")
        received[row.name] = (when, flt(row.amount))
        stamp("Folio Payment", row.name, {"received_on": when, "payment_date": row.business_date})
    charges_left, payments_left = list(posted.values()), list(received.values())
    for log in frappe.get_all("Folio Log", filters={"folio": stay.folio}, fields=["name", "action", "amount"], order_by="changed_at asc, name asc"):
        when = None
        if log.action.startswith("Charge posted"):
            hit = next((c for c in charges_left if abs(c[1] - flt(log.amount)) < 0.01), None)
            if hit:
                charges_left.remove(hit); when = hit[0]
        elif log.action.startswith("Point of sale"):
            when = next((c[0] for c in posted.values() if abs(c[1] - flt(log.amount)) < 0.01), None)
        elif log.action.startswith("Payment recorded"):
            hit = next((p for p in payments_left if abs(p[1] - flt(log.amount)) < 0.01), None)
            if hit:
                payments_left.remove(hit); when = hit[0]
        elif log.action in ("Status changed", "Checked out with open balance"):
            when = left or arrived
        else:
            when = arrived
        if when:
            stamp("Folio Log", log.name, {"changed_at": when, "creation": when})
for res in frappe.get_all("Reservation", filters={"property": PROPERTY, "reservation_status": ("in", ("Confirmed", "Guaranteed", "Tentative", "Cancelled", "No Show", "Draft"))},
                          fields=["name", "arrival_date", "reservation_status"]):
    booked = at(add_days(res.arrival_date, -6), "10:15")
    values = {"booked_on": booked, "creation": booked, "confirmed_on": booked if res.reservation_status not in ("Tentative", "Draft") else None}
    if res.reservation_status == "No Show":
        values["no_show_on"] = at(add_days(res.arrival_date, 1), "00:16")
    if res.reservation_status == "Cancelled":
        values["cancelled_on"] = at(today, "09:05")
    stamp("Reservation", res.name, values)
for audit in frappe.get_all("Night Audit", filters={"property": PROPERTY, "audit_status": "Closed"}, fields=["name", "business_date"]):
    night = add_days(audit.business_date, 1)
    stamp("Night Audit", audit.name, {"creation": at(night, "00:05"), "started_on": at(night, "00:05"), "review_completed_on": at(night, "00:12"),
                                       "no_shows_completed_on": at(night, "00:16"), "posting_completed_on": at(night, "00:22"),
                                       "due_outs_completed_on": at(night, "00:25"), "reconciliation_completed_on": at(night, "00:31"), "closed_on": at(night, "00:38")})
for task in frappe.get_all("Housekeeping Task", filters={"property": PROPERTY}, fields=["name", "scheduled_date", "task_status"]):
    d = task.scheduled_date or today
    values = {"creation": at(d, "07:45"), "assigned_on": at(d, "07:50") if task.task_status != "Pending" else None}
    if task.task_status in ("In Progress", "Inspection Pending", "Completed"):
        values["started_on"] = at(d, "09:05")
    if task.task_status in ("Inspection Pending", "Completed"):
        values["completed_on"] = at(d, "09:40")
    if task.task_status == "Completed":
        values["inspected_on"] = at(d, "10:15")
    stamp("Housekeeping Task", task.name, values)
for ticket in frappe.get_all("Maintenance Ticket", filters={"property": PROPERTY}, fields=["name", "ticket_status"]):
    reported = at(D(-1), "16:40") if ticket.ticket_status == "In Progress" else at(today, "11:25")
    values = {"creation": reported, "reported_on": reported}
    if ticket.ticket_status == "In Progress":
        values.update({"assigned_on": at(D(-1), "17:00"), "started_on": at(today, "08:30")})
    stamp("Maintenance Ticket", ticket.name, values)
frappe.db.commit()
# 9. Keys, registrations, notes: the small things a desk does all day
ensure("Hardware Device", {"device_code": "FD-ENC-1"}, {"device_name": "Front desk key encoder", "property": PROPERTY, "device_type": "Key Card Encoder",
                                                        "is_active": 1, "location": "Front desk", "device_status": "Online",
                                                        "notes": "No vendor configured: the PMS's built-in simulator answers"})
from hospitality_pms.services import hardware as hardware_service
for stay in (rose_stay, jb_stay):
    if stay and not frappe.db.exists("Key Card", {"stay": stay, "card_status": ("in", ("Issued", "Active"))}):
        hardware_service.issue_key(stay)
        frappe.db.commit()
ensure("Regulatory Profile", {"profile_code": "HT-REG"}, {"profile_name": "Guest registration (Haiti)", "property": PROPERTY, "country": "Haiti",
                                                          "is_active": 1, "requires_guest_registration": 1, "submission_frequency": "Daily",
                                                          "retention_years": 5, "notes": "Registration cards kept for the authorities; no electronic submission configured"})
from hospitality_pms.services import regulatory as regulatory_service
for stay in frappe.get_all("Stay", filters={"property": PROPERTY, "stay_status": ("in", ("In House", "Due Out"))}, pluck="name"):
    regulatory_service.register_guest(stay)
frappe.db.commit()
if rose_stay:
    if not frappe.db.exists("Stay Companion", {"parent": rose_stay, "guest_name": "Marc Delva"}):
        stay_service.add_companion(rose_stay, {"guest_name": "Marc Delva", "relationship": "Spouse", "is_adult": 1})
        frappe.db.commit()
    if not frappe.db.exists("Stay Note", {"parent": rose_stay, "note_type": "Shift Handover"}):
        stay_service.add_note(rose_stay, "VIP: fruit basket and sparkling water delivered; asked about a late check-out, manager to confirm.", "Shift Handover")
        frappe.db.commit()

# 10. The fixed demo login
update_password("Administrator", DEMO_PASSWORD)
frappe.db.commit()

summary = {
    "rooms": frappe.db.count("Hotel Room", {"property": PROPERTY}),
    "reservations": frappe.db.count("Reservation", {"property": PROPERTY}),
    "in_house": frappe.db.count("Stay", {"property": PROPERTY, "stay_status": ("in", ("In House", "Due Out"))}),
    "checked_out": frappe.db.count("Stay", {"property": PROPERTY, "stay_status": "Checked Out"}),
    "folios": frappe.db.count("Guest Folio", {"property": PROPERTY}),
    "audits_closed": frappe.db.count("Night Audit", {"property": PROPERTY, "audit_status": "Closed"}),
    "invoices": frappe.db.count("Sales Invoice", {"company": company, "docstatus": 1}),
    "payments": frappe.db.count("Payment Entry", {"company": company, "docstatus": 1}),
    "requests": frappe.db.count("Guest Request", {"property": PROPERTY}),
    "tasks": frappe.db.count("Housekeeping Task", {"property": PROPERTY}),
    "tickets": frappe.db.count("Maintenance Ticket", {"property": PROPERTY}),
    "menu_items": frappe.db.count("Menu Item", {"property": PROPERTY}),
    "orders": frappe.db.count("Room Service Order", {"property": PROPERTY}),
    "users": len(USERS),
    "business_date": str(business_date()),
}
print(f"DEMO-HOTEL-SEED-OK {summary}")
