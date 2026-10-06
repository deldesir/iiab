"""Create or update a Jinja Print Format on the connected site from an .html (+ optional .css) file.

Run from the bench's sites directory with the bench Python, e.g.
  python load_print_format.py --site site.local --name "Demande de matériel" --doctype "Material Request" \
      --html demande_de_materiel.html --css demande_de_materiel.css --module Stock --language fr --default
Idempotent: re-running updates the html/css in place."""
import argparse
import frappe


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--site", required=True)
	ap.add_argument("--sites-path", default=".")
	ap.add_argument("--name", required=True)
	ap.add_argument("--doctype", required=True)
	ap.add_argument("--html", required=True)
	ap.add_argument("--css")
	ap.add_argument("--module", default="Stock")
	ap.add_argument("--language", default="fr")
	ap.add_argument("--pdf-generator", default="chrome", help="chrome honours @page size; wkhtmltopdf follows Print Settings")
	ap.add_argument("--default", action="store_true", help="make it the doctype's default print format")
	a = ap.parse_args()

	frappe.init(site=a.site, sites_path=a.sites_path)
	frappe.connect()
	frappe.set_user("Administrator")
	html = open(a.html, encoding="utf-8").read()
	css = open(a.css, encoding="utf-8").read() if a.css else ""
	values = {
		"doc_type": a.doctype,
		"module": a.module,
		"print_format_type": "Jinja",
		"standard": "No",
		"custom_format": 1,
		"disabled": 0,
		"html": html,
		"css": css,
		"default_print_language": a.language,
		"font_size": 11,
		"margin_top": 12,
		"margin_bottom": 12,
		"margin_left": 14,
		"margin_right": 14,
	}
	if a.pdf_generator in (frappe.get_meta("Print Format").get_options("pdf_generator") or "").split("\n"):
		values["pdf_generator"] = a.pdf_generator
	if frappe.db.exists("Print Format", a.name):
		doc = frappe.get_doc("Print Format", a.name)
		doc.update(values)
		doc.save()
		action = "updated"
	else:
		doc = frappe.get_doc({"doctype": "Print Format", "name": a.name, **values})
		doc.insert()
		action = "created"
	if a.default:
		frappe.make_property_setter(
			{"doctype": a.doctype, "property": "default_print_format", "value": a.name, "property_type": "Data"},
			validate_fields_for_doctype=False,
		)
	frappe.db.commit()
	print(f"{action} Print Format '{a.name}' for {a.doctype}" + (" (default)" if a.default else ""))


if __name__ == "__main__":
	main()
