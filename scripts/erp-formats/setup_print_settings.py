"""Print Settings sane for office documents: PDF page size Letter (receipts print through the
browser/QZ, never through PDF), optional base font size. Prints before/after; idempotent.
  python setup_print_settings.py --site site.local [--page-size Letter] [--font-size 0]"""
import argparse
import frappe


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--site", required=True)
	ap.add_argument("--sites-path", default=".")
	ap.add_argument("--page-size", default="Letter")
	ap.add_argument("--font-size", type=float, default=None, help="base font size in pt; 0 = Frappe default")
	a = ap.parse_args()
	frappe.init(site=a.site, sites_path=a.sites_path)
	frappe.connect()
	frappe.set_user("Administrator")
	ps = frappe.get_single("Print Settings")
	before = {"pdf_page_size": ps.pdf_page_size, "pdf_page_width": ps.pdf_page_width, "pdf_page_height": ps.pdf_page_height, "font_size": ps.font_size}
	ps.pdf_page_size = a.page_size
	if a.font_size is not None:
		ps.font_size = a.font_size
	ps.save()
	frappe.db.commit()
	after = {"pdf_page_size": ps.pdf_page_size, "pdf_page_width": ps.pdf_page_width, "pdf_page_height": ps.pdf_page_height, "font_size": ps.font_size}
	print("before:", before)
	print("after: ", after)


if __name__ == "__main__":
	main()
