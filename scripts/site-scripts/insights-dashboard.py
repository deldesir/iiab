# insights-dashboard.py — the owner dashboard in Frappe Insights: five native SQL queries on the
# site database (live), five charts, one dashboard "Tableau de bord" in a workbook "Owner dashboard".
# Idempotent: existing records (by title in the workbook) are updated in place.
# Run as frappe:  bench --site <site> execute "exec(open('/path/insights-dashboard.py').read(), {})"
import json
import frappe

WORKBOOK = "Owner dashboard"
DASHBOARD = "Tableau de bord"
DS = frappe.db.get_value("Insights Data Source v3", {"is_site_db": 1}, "name") or "Site DB"

SQL = {
    "Ventes du jour": """
select count(*) as factures, coalesce(sum(grand_total), 0) as ventes
from `tabSales Invoice`
where docstatus = 1 and is_return = 0 and posting_date = curdate()""",
    "Ventes par jour (30 jours)": """
select posting_date as jour, sum(grand_total) as ventes, count(*) as factures
from `tabSales Invoice`
where docstatus = 1 and is_return = 0 and posting_date >= date_sub(curdate(), interval 30 day)
group by posting_date order by posting_date""",
    "Ventes par caissier (30 jours)": """
select coalesce(u.full_name, si.owner) as caissier, sum(si.grand_total) as ventes, count(*) as factures
from `tabSales Invoice` si left join `tabUser` u on u.name = si.owner
where si.docstatus = 1 and si.is_return = 0 and si.posting_date >= date_sub(curdate(), interval 30 day)
group by caissier order by ventes desc""",
    "Créances par ancienneté": """
select case
  when datediff(curdate(), due_date) <= 0 then '1. Non échu'
  when datediff(curdate(), due_date) <= 30 then '2. 1-30 jours'
  when datediff(curdate(), due_date) <= 60 then '3. 31-60 jours'
  when datediff(curdate(), due_date) <= 90 then '4. 61-90 jours'
  else '5. Plus de 90 jours' end as tranche,
  count(*) as factures, sum(outstanding_amount) as montant
from `tabSales Invoice`
where docstatus = 1 and outstanding_amount > 0
group by tranche order by tranche""",
    "Couverture de stock (jours)": """
select s.item_code as article, i.item_name as designation, coalesce(b.qty, 0) as stock,
  round(s.qty30 / 30, 2) as ventes_par_jour,
  case when s.qty30 > 0 then round(coalesce(b.qty, 0) / (s.qty30 / 30), 1) else null end as jours_de_couverture
from (
  select sii.item_code, sum(sii.stock_qty) as qty30
  from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
  where si.docstatus = 1 and si.is_return = 0 and si.update_stock = 1
    and si.posting_date >= date_sub(curdate(), interval 30 day)
  group by sii.item_code
) s
join `tabItem` i on i.name = s.item_code and i.is_stock_item = 1
left join (select item_code, sum(actual_qty) as qty from `tabBin` group by item_code) b on b.item_code = s.item_code
where s.qty30 > 0
order by jours_de_couverture asc
limit 25""",
}

def measure(name, agg="sum", fmt=None, dtype="Decimal"):
    m = {"measure_name": name, "column_name": name, "data_type": dtype, "aggregation": agg}
    if fmt:
        m["format"] = fmt
    return m

def dimension(name, dtype="String", granularity=None):
    d = {"dimension_name": name, "column_name": name, "data_type": dtype}
    if granularity:
        d["granularity"] = granularity
    return d

CHARTS = [
    ("Ventes du jour", "Ventes du jour", "Number",
     {"number_columns": [dict(measure("ventes", fmt="currency"), id="ventes"), dict(measure("factures", dtype="Integer"), id="factures")],
      "number_column_options": [], "sparkline": False}),
    ("Ventes par jour", "Ventes par jour (30 jours)", "Bar",
     {"x_axis": {"dimension": dimension("jour", "Date", "day")},
      "y_axis": {"series": [{"measure": measure("ventes", fmt="currency")}], "show_data_labels": False}}),
    ("Ventes par caissier", "Ventes par caissier (30 jours)", "Bar",
     {"x_axis": {"dimension": dimension("caissier")},
      "y_axis": {"series": [{"measure": measure("ventes", fmt="currency")}], "show_data_labels": True}}),
    ("Créances par ancienneté", "Créances par ancienneté", "Table",
     {"rows": [dimension("tranche")], "columns": [], "values": [measure("factures", dtype="Integer"), measure("montant", fmt="currency")],
      "show_row_totals": False, "show_column_totals": True}),
    ("Couverture de stock", "Couverture de stock (jours)", "Table",
     {"rows": [dimension("article"), dimension("designation")], "columns": [],
      "values": [measure("stock"), measure("ventes_par_jour"), measure("jours_de_couverture", agg="min")],
      "show_row_totals": False, "show_column_totals": False}),
]
# grid placement (20 columns wide)
LAYOUT = {"Ventes du jour": (0, 0, 6, 5), "Ventes par jour": (6, 0, 14, 8), "Ventes par caissier": (0, 5, 6, 8),
          "Créances par ancienneté": (6, 8, 7, 8), "Couverture de stock": (13, 8, 7, 8)}

log = []
wb = frappe.db.get_value("Insights Workbook", {"title": WORKBOOK}, "name")
if not wb:
    wb = frappe.get_doc({"doctype": "Insights Workbook", "title": WORKBOOK}).insert(ignore_permissions=True).name
    log.append("workbook")

queries = {}
for i, (title, sql) in enumerate(SQL.items()):
    name = frappe.db.get_value("Insights Query v3", {"workbook": wb, "title": title}, "name")
    q = frappe.get_doc("Insights Query v3", name) if name else frappe.new_doc("Insights Query v3")
    q.update({"title": title, "workbook": wb, "is_native_query": 1, "is_builder_query": 0, "is_script_query": 0,
              "use_live_connection": 1, "sort_order": i,
              "operations": json.dumps([{"type": "sql", "raw_sql": sql.strip(), "data_source": DS}])})
    q.save(ignore_permissions=True)
    queries[title] = q.name
    log.append(f"query {title}")

charts = {}
for i, (title, qtitle, ctype, config) in enumerate(CHARTS):
    name = frappe.db.get_value("Insights Chart v3", {"workbook": wb, "title": title}, "name")
    c = frappe.get_doc("Insights Chart v3", name) if name else frappe.new_doc("Insights Chart v3")
    c.update({"title": title, "workbook": wb, "query": queries[qtitle], "chart_type": ctype, "config": json.dumps(config),
              "sort_order": i, "visibility": "Roles", "visible_to_roles": [{"role": "Accounts Manager"}, {"role": "Sales Manager"}]})
    c.save(ignore_permissions=True)
    charts[title] = c.name
    log.append(f"chart {title}")

items = []
for title, (x, y, w, h) in LAYOUT.items():
    items.append({"type": "chart", "chart": charts[title], "layout": {"i": charts[title], "x": x, "y": y, "w": w, "h": h}})
name = frappe.db.get_value("Insights Dashboard v3", {"workbook": wb, "title": DASHBOARD}, "name")
d = frappe.get_doc("Insights Dashboard v3", name) if name else frappe.new_doc("Insights Dashboard v3")
d.update({"title": DASHBOARD, "workbook": wb, "items": json.dumps(items), "visibility": "Roles",
          "visible_to_roles": [{"role": "Accounts Manager"}, {"role": "Sales Manager"}],
          "linked_charts": [{"chart": n} for n in charts.values()]})
d.save(ignore_permissions=True)
log.append(f"dashboard {DASHBOARD}")

# prove every query runs against the live site database
results = {}
for title, qname in queries.items():
    q = frappe.get_doc("Insights Query v3", qname)
    res = q.execute(page_size=5)
    rows = res.get("rows") if isinstance(res, dict) else res
    results[title] = len(rows) if isinstance(rows, list) else "?"
frappe.db.commit()
print("INSIGHTS-DASHBOARD-OK", wb, "|", "; ".join(log))
print("  rows per query:", results)
print("  open: /erp/insights/workbook/%s" % wb)
