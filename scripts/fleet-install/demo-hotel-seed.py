# demo-hotel-seed.py — turn a fresh ERPNext + Hospitality PMS site into a clickable demo hotel
# (idempotent). The hotel profile of the demo box; the shop seed (demo-seed.py) is not needed.
#
# Run inside the bench, on the site to seed:
#   bench --site site.local execute "exec(open('/opt/iiab/iiab/scripts/fleet-install/demo-hotel-seed.py').read(), {})"
#
# What it does: completes the setup wizard as "Demo Hotel" (Haiti, HTG) if it has not run, then
# builds a 12-room hotel in the PMS: the property, three room types, two rate plans, the posting
# profile that turns folio charges into ERPNext invoices (with the service items it needs),
# seven guests, and a front desk that has something to show on the business date:
# two guests in house (with folio charges and a payment), one of them departing today, two
# arrivals today (one with a room assigned), bookings for later, a tentative one, rooms in
# housekeeping, one out of order with a maintenance ticket. Administrator's password becomes
# DEMO_PASSWORD. Never run it on a real site.
import frappe
from frappe.utils import add_days, getdate, nowdate

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

from hospitality_pms.services import folio as folio_service
from hospitality_pms.services import housekeeping as housekeeping_service
from hospitality_pms.services import maintenance as maintenance_service
from hospitality_pms.services import reservations as reservation_service
from hospitality_pms.services import rooms as room_service
from hospitality_pms.services import stays as stay_service
from hospitality_pms.services.property import BUSINESS_DATE_FLAG


def ensure(doctype, filters, values):
    """Insert a record once; return its name."""
    name = frappe.db.get_value(doctype, filters, "name")
    if name:
        return name
    doc = frappe.get_doc({"doctype": doctype, **filters, **values})
    doc.insert(ignore_permissions=True)
    return doc.name


def set_business_date(value):
    """Move the property's operating day the way the Night Audit does (the controller refuses
    any other writer), so the in-house guests can be checked in on their own arrival days."""
    frappe.flags[BUSINESS_DATE_FLAG] = True
    try:
        frappe.db.set_value("Property", PROPERTY, "business_date", getdate(value))
    finally:
        frappe.flags[BUSINESS_DATE_FLAG] = False
    frappe.db.commit()


# 1. The service items folio charges post to (non-stock)
item_group = ensure("Item Group", {"item_group_name": "Hotel Services"}, {"parent_item_group": "All Item Groups", "is_group": 0})
for code, name in (("ROOM-NIGHT", "Room night"), ("ROOM-SERVICE", "Room service"), ("MINIBAR", "Minibar"),
                   ("LAUNDRY", "Laundry"), ("BREAKFAST", "Breakfast")):
    ensure("Item", {"item_code": code}, {"item_name": name, "item_group": item_group, "stock_uom": "Nos",
                                         "is_stock_item": 0, "is_sales_item": 1, "is_purchase_item": 0})

# 2. The property, opened two days before today so the stays below can be checked in on time
ensure("Property", {"property_code": PROPERTY}, {
    "property_name": COMPANY, "property_type": "Hotel", "star_rating": "3", "is_active": 1,
    "company": company, "currency": currency, "country": "Haiti", "time_zone": "America/Port-au-Prince",
    "default_language": "en", "business_date": add_days(today, -2),
    "check_in_time": "14:00:00", "check_out_time": "12:00:00", "total_rooms": 12,
    "receivable_account": receivable, "room_revenue_account": income, "default_price_list": "Standard Selling",
    "email": "frontdesk@example.com", "cost_center": cost_center,
})
frappe.db.set_single_value("PMS Settings", "default_property", PROPERTY)

# 3. Room types, rooms (floor = type), rate plans
TYPES = {  # code: (name, max occupancy, nightly rate, bed configuration)
    "STD": ("Standard room", 2, 4500, "1 queen bed"),
    "DLX": ("Deluxe room", 3, 7000, "1 king bed + sofa"),
    "STE": ("Suite", 4, 12000, "1 king bed + living room"),
}
for order, (code, (name, max_occ, rate, beds)) in enumerate(TYPES.items(), start=1):
    ensure("Room Type", {"room_type_code": code}, {
        "room_type_name": name, "property": PROPERTY, "is_active": 1, "display_order": order,
        "base_occupancy": 2, "max_occupancy": max_occ, "max_adults": max_occ, "max_children": 1,
        "bed_configuration": beds, "currency": currency, "base_rate": rate, "extra_adult_charge": 1000,
    })
ROOMS = {}
for floor, code in ((1, "STD"), (2, "DLX"), (3, "STE")):
    for n in range(1, 5):
        number = f"{floor}0{n}"
        ROOMS[number] = ensure("Hotel Room", {"room_code": number}, {
            "room_number": number, "property": PROPERTY, "room_type": code, "is_active": 1,
            "occupancy_status": "Vacant", "housekeeping_status": "Clean", "maintenance_status": "Operational",
            "inventory_status": "Available", "view_type": "Sea view" if floor == 3 else "Garden",
            "has_balcony": 1 if floor >= 2 else 0,
        })
ensure("Rate Plan", {"rate_plan_code": "BAR"}, {
    "rate_plan_name": "Best available rate", "property": PROPERTY, "rate_type": "Standard", "is_active": 1,
    "valid_from": add_days(today, -365), "currency": currency, "is_refundable": 1, "includes_breakfast": 1,
    "room_types": [{"room_type": c, "base_rate": t[2], "single_occupancy_rate": round(t[2] * 0.9),
                    "extra_adult_charge": 1000, "is_active": 1} for c, t in TYPES.items()],
})
ensure("Rate Plan", {"rate_plan_code": "CORP"}, {
    "rate_plan_name": "Corporate rate", "property": PROPERTY, "rate_type": "Corporate", "is_active": 1,
    "valid_from": add_days(today, -365), "currency": currency, "is_refundable": 1, "includes_breakfast": 1,
    "market_segment": "Corporate",
    "room_types": [{"room_type": c, "base_rate": round(t[2] * 0.85), "extra_adult_charge": 1000, "is_active": 1}
                   for c, t in TYPES.items()],
})

# 4. Folio charges become ERPNext sales invoices through this profile
ensure("Posting Profile", {"profile_code": "DEMO-POST"}, {
    "profile_name": "Demo Hotel posting", "property": PROPERTY, "company": company, "is_active": 1,
    "default_item": "ROOM-NIGHT", "default_income_account": income, "default_cost_center": cost_center,
    "default_receivable_account": receivable,
    "charge_items": [{"charge_type": ct, "item": it, "income_account": income, "cost_center": cost_center, "is_active": 1}
                     for ct, it in (("Room Charge", "ROOM-NIGHT"), ("Room Service", "ROOM-SERVICE"),
                                    ("Minibar", "MINIBAR"), ("Laundry", "LAUNDRY"))],
})

# 5. Guests
GUESTS = {}
for first, last, country, vip in (
    ("Marie-Claire", "Jean", "Haiti", "Repeat Guest"), ("Jean-Baptiste", "Pierre", "Haiti", ""),
    ("Rose", "Delva", "Haiti", "VIP"), ("Patrick", "Louis", "Haiti", ""), ("Carlos", "Mendez", "Dominican Republic", ""),
    ("Emily", "Carter", "United States", "Loyalty Member"), ("Nadège", "Charles", "Haiti", ""),
):
    GUESTS[first] = ensure("Guest", {"first_name": first, "last_name": last}, {
        "nationality": country, "country": country, "preferred_language": "en", "guest_type": "Individual",
        "vip_status": vip, "email_id": f"{first.lower().replace('è', 'e')}.{last.lower()}@example.com",
        "mobile_no": "+509 3700 0000",
    })


# 6. Reservations (keyed by an external reference so a rerun finds them instead of booking twice)
def reservation(ref, guest, room_type, arrival, nights, *, rate_plan="BAR", kind="Individual", adults=2, requests=None):
    name = frappe.db.get_value("Reservation", {"external_reference": ref}, "name")
    if name:
        return name
    doc = frappe.get_doc({
        "doctype": "Reservation", "property": PROPERTY, "reservation_status": "Draft", "reservation_type": kind,
        "booking_source": "Direct", "external_reference": ref, "guest": GUESTS[guest],
        "arrival_date": arrival, "departure_date": add_days(arrival, nights), "rate_plan": rate_plan,
        "special_requests": requests,
        "rooms": [{"room_type": room_type, "rooms": 1, "adults": adults, "arrival_date": arrival,
                   "departure_date": add_days(arrival, nights), "rate_plan": rate_plan}],
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


def in_house(ref, *args, room, charges=(), payment=None, **kwargs):
    """A guest who has checked in, with a few folio lines."""
    name = confirmed(ref, *args, room=room, **kwargs)
    if status(name) != "Checked In":
        stay_service.check_in(name, first_line(name), ROOMS[room], skip_id_check=True)
        frappe.db.commit()
    stay = frappe.db.get_value("Stay", {"reservation": name}, "name")
    folio = folio_service.get_folio_for_stay(stay)
    for i, (charge_type, description, amount) in enumerate(charges, start=1):
        folio_service.post_charge(folio, charge_type, description, amount, idempotency_key=f"demo:{ref}:charge{i}")
    if payment:
        folio_service.post_payment(folio, payment, "Cash", idempotency_key=f"demo:{ref}:payment")
    frappe.db.commit()
    return name


# The front desk's timeline: two guests arrived on earlier business dates, then today's business
set_business_date(add_days(today, -2))
in_house("DEMO-03", "Rose", "STE", add_days(today, -2), 2, room="301", requests="Late check-out if possible",
         charges=(("Room Service", "Dinner, room service", 1850), ("Laundry", "Laundry, 4 pieces", 600)), payment=10000)
set_business_date(add_days(today, -1))
in_house("DEMO-02", "Jean-Baptiste", "DLX", add_days(today, -1), 3, room="201", rate_plan="CORP", kind="Corporate",
         adults=1, charges=(("Minibar", "Minibar", 450),))
set_business_date(today)
confirmed("DEMO-01", "Marie-Claire", "STD", today, 2, room="101", requests="Ground floor, near the lobby")
confirmed("DEMO-05", "Carlos", "DLX", today, 1, adults=1)
confirmed("DEMO-04", "Patrick", "STD", add_days(today, 7), 1)
emily = confirmed("DEMO-06", "Emily", "STE", add_days(today, 3), 4, kind="Direct Website")
if status(emily) == "Confirmed":
    try:
        reservation_service.guarantee(emily, "Credit Card")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
nadege = reservation("DEMO-07", "Nadège", "STD", add_days(today, 10), 2)
if status(nadege) == "Draft":
    try:
        reservation_service.make_tentative(nadege)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()

# 7. Housekeeping and maintenance, so the boards are not empty
if frappe.db.get_value("Hotel Room", ROOMS["102"], "housekeeping_status") == "Clean":
    room_service.set_status(ROOMS["102"], "housekeeping_status", "Dirty", reason="Demo: checked out this morning")
    frappe.db.commit()
if not frappe.db.exists("Housekeeping Task", {"room": ROOMS["102"], "status": ("not in", ("Completed", "Cancelled"))}):
    try:
        housekeeping_service.create_task(PROPERTY, ROOMS["102"], task_type="Departure Clean", priority="High")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
if not frappe.db.exists("Maintenance Ticket", {"room": ROOMS["304"], "status": ("not in", ("Closed", "Cancelled", "Completed"))}):
    try:
        maintenance_service.create_ticket(PROPERTY, title="Air conditioning not cooling", room=ROOMS["304"],
                                          description="Guest reported warm air; technician booked for tomorrow.",
                                          category="HVAC", priority="High")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        try:
            maintenance_service.create_ticket(PROPERTY, title="Air conditioning not cooling", room=ROOMS["304"],
                                              description="Guest reported warm air; technician booked for tomorrow.",
                                              priority="High")
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

# 8. The fixed demo login
from frappe.utils.password import update_password
update_password("Administrator", DEMO_PASSWORD)
frappe.db.commit()

summary = {
    "rooms": frappe.db.count("Hotel Room", {"property": PROPERTY}),
    "reservations": frappe.db.count("Reservation", {"property": PROPERTY}),
    "in_house": frappe.db.count("Stay", {"property": PROPERTY, "stay_status": ("in", ("In House", "Due Out"))}),
    "folios": frappe.db.count("Guest Folio", {"property": PROPERTY}),
    "business_date": str(frappe.db.get_value("Property", PROPERTY, "business_date")),
}
print(f"DEMO-HOTEL-SEED-OK {summary}")
