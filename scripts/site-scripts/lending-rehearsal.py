# lending-rehearsal.py — the installment-sale pattern end to end on a NON-production site, after
# lending-setup.py: sell an item on a Sales Invoice, finance it with a Loan disbursed to the clearing
# account, settle the invoice from the clearing account, raise the first month's demand, collect the
# first installment in cash, and check the books. Refuses to run on a site that is not marked demo
# or whose company has real invoices.
# Run as frappe:  bench --site <site> execute "exec(open('/path/lending-rehearsal.py').read(), {})"
import frappe
from frappe.utils import add_months, nowdate, flt, getdate

PRODUCT_CODE = "PLAN6"
PERIODS = 6              # installments; a per-loan choice, the product only caps amount and sets the rate
PRICE = 30000.0          # the phone
DOWN_PAYMENT = 6000.0    # paid in cash on the day
FINANCED = PRICE - DOWN_PAYMENT

company = frappe.get_all("Company", pluck="name", order_by="creation")[0]
co = frappe.get_doc("Company", company)
real_invoices = frappe.db.count("Sales Invoice", {"company": company, "docstatus": 1, "customer": ["not like", "Rehearsal%"]})
if real_invoices and not frappe.conf.get("demo"):
    frappe.throw(f"refusing: {company} has {real_invoices} real invoices and the site is not marked demo")

def acc(account_name):
    return frappe.db.get_value("Account", {"account_name": account_name, "company": company}, "name")

product = frappe.get_doc("Loan Product", {"product_code": PRODUCT_CODE, "company": company})
clearing = product.disbursement_account
cash = product.payment_account
income = frappe.db.get_value("Account", {"company": company, "account_name": "Sales"}, "name") or co.default_income_account
cost_center = co.cost_center or frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")

# masters
if not frappe.db.exists("Customer", "Rehearsal Customer"):
    frappe.get_doc({"doctype": "Customer", "customer_name": "Rehearsal Customer", "customer_type": "Individual",
                    "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                    "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name")}).insert(ignore_permissions=True)
if not frappe.db.exists("Item", "REHEARSAL-PHONE"):
    frappe.get_doc({"doctype": "Item", "item_code": "REHEARSAL-PHONE", "item_name": "Rehearsal phone", "is_stock_item": 0,
                    "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"), "stock_uom": "Nos"}).insert(ignore_permissions=True)

a_month_ago = add_months(nowdate(), -1)

# 1. the sale
si = frappe.get_doc({"doctype": "Sales Invoice", "company": company, "customer": "Rehearsal Customer",
                     "set_posting_time": 1, "posting_date": a_month_ago, "due_date": a_month_ago,
                     "items": [{"item_code": "REHEARSAL-PHONE", "qty": 1, "rate": PRICE, "income_account": income, "cost_center": cost_center}]})
si.insert(ignore_permissions=True); si.submit()

# 2. the down payment in cash
def pay(amount, paid_from_or_mode, mode=None):
    pe = frappe.get_doc({"doctype": "Payment Entry", "payment_type": "Receive", "company": company, "party_type": "Customer",
                         "party": "Rehearsal Customer", "posting_date": a_month_ago, "paid_amount": amount, "received_amount": amount,
                         "paid_to": paid_from_or_mode, "paid_from": frappe.db.get_value("Company", company, "default_receivable_account"),
                         "mode_of_payment": mode, "reference_no": "rehearsal", "reference_date": a_month_ago,
                         "references": [{"reference_doctype": "Sales Invoice", "reference_name": si.name, "allocated_amount": amount}]})
    pe.insert(ignore_permissions=True); pe.submit(); return pe
pe_cash = pay(DOWN_PAYMENT, cash, "Cash")

# 3. the loan for the rest, disbursed a month ago into the clearing account, first installment due today
loan = frappe.get_doc({"doctype": "Loan", "company": company, "applicant_type": "Customer", "applicant": "Rehearsal Customer",
                       "loan_product": product.name, "loan_amount": FINANCED, "maximum_limit_amount": FINANCED,
                       "repayment_method": "Repay Over Number of Periods", "repayment_periods": PERIODS,
                       "repayment_start_date": nowdate(), "posting_date": a_month_ago, "repayment_frequency": "Monthly",
                       "repayment_schedule_type": product.repayment_schedule_type})
loan.insert(ignore_permissions=True); loan.submit()
disb = frappe.get_doc({"doctype": "Loan Disbursement", "against_loan": loan.name, "company": company,
                       "disbursement_date": a_month_ago, "posting_date": a_month_ago, "repayment_start_date": nowdate(),
                       "disbursed_amount": FINANCED, "cost_center": cost_center})
disb.insert(ignore_permissions=True); disb.submit()

# 4. the loan money settles the invoice (paid from the clearing account)
pe_clear = pay(FINANCED, clearing, frappe.db.get_value("Mode of Payment Account", {"default_account": clearing}, "parent"))

# 5. the first month: demands, then the installment in cash
from lending.loan_management.doctype.process_loan_demand.process_loan_demand import process_daily_loan_demands
process_daily_loan_demands(loan=loan.name, posting_date=nowdate())
schedule = frappe.get_doc("Loan Repayment Schedule", {"loan": loan.name, "docstatus": 1, "status": "Active"})
emi = flt(schedule.monthly_repayment_amount, 2)
lr = frappe.get_doc({"doctype": "Loan Repayment", "against_loan": loan.name, "company": company, "posting_date": nowdate(),
                     "value_date": nowdate(), "amount_paid": emi, "repayment_type": "Normal Repayment", "payment_account": cash})
lr.insert(ignore_permissions=True); lr.submit()
loan.reload(); si.reload()

# 6. the books
def bal(account):
    r = frappe.db.sql("select sum(debit)-sum(credit) from `tabGL Entry` where account=%s and is_cancelled=0", account)
    return flt(r[0][0] if r and r[0][0] is not None else 0, 2)
frappe.db.commit()
print("REHEARSAL-OK")
print(f"  invoice {si.name}: grand total {si.grand_total}, outstanding {si.outstanding_amount} (down payment {pe_cash.name}, clearing payment {pe_clear.name})")
print(f"  loan {loan.name}: status {loan.status}, amount {loan.loan_amount}, total interest {flt(loan.total_interest_payable,2)}, EMI {emi} x {len(schedule.repayment_schedule)}")
print(f"  first installment {lr.name}: principal {flt(lr.principal_amount_paid,2)}, interest {flt(lr.total_interest_paid,2)}, penalty {flt(lr.total_penalty_paid,2)}")
print(f"  balances: clearing {bal(clearing)} (expect 0), loan account {bal(product.loan_account)}, interest income {bal(product.interest_income_account)}, cash {bal(cash)}")
