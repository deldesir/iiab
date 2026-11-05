# run.py — execute a script inside a connected Frappe site as Administrator.
# usage (as frappe): /home/frappe/frappe-bench/env/bin/python run.py <site> <script.py>
import os
import sys
import frappe
site, path = sys.argv[1], sys.argv[2]
os.chdir(os.path.join(os.path.dirname(os.path.abspath(frappe.__file__)), "..", "..", "..", "sites"))
frappe.init(site=site, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
try:
    exec(compile(open(path).read(), path, "exec"), {"__name__": "__main__"})
finally:
    frappe.destroy()
