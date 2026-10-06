"""Create a Payment Term + Payment Terms Template (e.g. "30 jours") and make it a company's default.

Credit sales fall due on the posting date when no terms exist, so every one of them shows
"Overdue" from day one. Run from the bench's sites directory with the bench Python:
  python setup_payment_terms.py --site site.local --days 30 [--company "<name>" ...] [--apply]
Without --apply it only prints what it would do. Idempotent."""
import argparse
import frappe


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--site", required=True)
	ap.add_argument("--sites-path", default=".")
	ap.add_argument("--days", type=int, default=30)
	ap.add_argument("--name", help="term/template name; default '<days> jours'")
	ap.add_argument("--company", action="append", help="company to set as default; repeatable; default = all")
	ap.add_argument("--apply", action="store_true")
	a = ap.parse_args()
	name = a.name or f"{a.days} jours"

	frappe.init(site=a.site, sites_path=a.sites_path)
	frappe.connect()
	frappe.set_user("Administrator")
	companies = a.company or frappe.get_all("Company", pluck="name")
	print(("APPLY" if a.apply else "DRY RUN"), f"| term + template '{name}' ({a.days} days after invoice date, 100%) | companies: {companies}")
	for c in companies:
		print(f"  {c}: default payment terms {frappe.db.get_value('Company', c, 'payment_terms') or '(none)'} -> {name}")
	if not a.apply:
		return
	if not frappe.db.exists("Payment Term", name):
		frappe.get_doc(
			{
				"doctype": "Payment Term",
				"payment_term_name": name,
				"invoice_portion": 100,
				"due_date_based_on": "Day(s) after invoice date",
				"credit_days": a.days,
				"description": f"Paiement intégral à {a.days} jours de la date de facture",
			}
		).insert()
	if not frappe.db.exists("Payment Terms Template", name):
		frappe.get_doc(
			{
				"doctype": "Payment Terms Template",
				"template_name": name,
				"terms": [{"payment_term": name, "invoice_portion": 100, "due_date_based_on": "Day(s) after invoice date", "credit_days": a.days}],
			}
		).insert()
	for c in companies:
		frappe.db.set_value("Company", c, "payment_terms", name)
	frappe.db.commit()
	print("done")


if __name__ == "__main__":
	main()
