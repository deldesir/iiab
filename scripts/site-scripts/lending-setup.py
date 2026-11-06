# lending-setup.py — idempotent Lending setup for one company: the accounts a Loan Product posts to,
# the demand offset orders, the company's loan settings, an "Installment Clearing" bank-type account with its
# Mode of Payment (the sale-plus-loan pattern), and one Loan Product.
# Run as frappe:  bench --site <site> execute "exec(open('/path/lending-setup.py').read(), {})"
# Edit CONFIG first. Every create is skipped when the record exists; the product is updated in place.
import frappe
from frappe.utils import flt

CONFIG = {
    "company": None,            # None = the first (or only) company on the site
    "product_code": "PLAN6",
    "product_name": "Vente à crédit",          # length is chosen per loan; 6 months is the standard offer
    "rate_of_interest": 36.0,   # % per YEAR on the remaining balance, accrued daily = 3 % per month
    "penalty_interest_rate": 36.0,  # % per year on an overdue installment after the grace days = 3 % per month
    "grace_period_in_days": 5,
    "maximum_loan_amount": 100000,
    # the number of installments is set per Loan (repayment_periods), not on the product
    "disabled": 0,              # 1 = created but not offered yet
    "clearing_account": "Installment Clearing",
    "clearing_mode_of_payment": "Installment Plan",
}

company = CONFIG["company"] or frappe.get_all("Company", pluck="name", order_by="creation")[0]
co = frappe.get_doc("Company", company)
abbr = co.abbr
log = []

def acc(account_name):
    """Full account name for this company by its account_name (number prefixes allowed)."""
    name = frappe.db.get_value("Account", {"account_name": account_name, "company": company}, "name")
    if not name:
        frappe.throw(f"account '{account_name}' not found for {company}")
    return name

def ensure_account(account_name, parent_name, root_type, account_type, report_type, is_group=0):
    existing = frappe.db.get_value("Account", {"account_name": account_name, "company": company}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Account", "account_name": account_name, "parent_account": acc(parent_name),
        "company": company, "root_type": root_type, "report_type": report_type,
        "account_type": account_type or None, "is_group": is_group, "account_currency": co.default_currency,
    }).insert(ignore_permissions=True)
    log.append(f"account {doc.name}")
    return doc.name

# 1. company settings
wanted = {"enable_loan_accounting": 1,
          "min_days_bw_disbursement_first_repayment": co.min_days_bw_disbursement_first_repayment or 15,
          "loan_accrual_frequency": co.loan_accrual_frequency or "Daily",            # schedules and demands need it
          "interest_day_count_convention": co.interest_day_count_convention or "Actual/365"}
changes = {k: v for k, v in wanted.items() if co.get(k) != v}
if changes:
    frappe.db.set_value("Company", company, dict(changes))
    log.append("company loan settings " + ", ".join(changes))

# 2. accounts (the names Lending's own setup uses, under the standard chart's groups)
ensure_account("Loans and Advances (Assets)", "Current Assets", "Asset", "", "Balance Sheet", is_group=1)
A = {}
A["loan_account"] = ensure_account("Loan Account", "Loans and Advances (Assets)", "Asset", "", "Balance Sheet")
A["interest_accrued_account"] = ensure_account("Interest Accrued Account", "Current Assets", "Asset", "", "Balance Sheet")
A["penalty_accrued_account"] = ensure_account("Penalty Accrued Account", "Current Assets", "Asset", "", "Balance Sheet")
A["additional_interest_accrued"] = ensure_account("Additional Interest Accrued Account", "Current Assets", "Asset", "", "Balance Sheet")
A["interest_receivable_account"] = ensure_account("Interest Receivable", "Accounts Receivable", "Asset", "Receivable", "Balance Sheet")
A["penalty_receivable_account"] = ensure_account("Penalty Receivable", "Accounts Receivable", "Asset", "Receivable", "Balance Sheet")
A["additional_interest_receivable"] = ensure_account("Additional Interest Receivable", "Accounts Receivable", "Asset", "Receivable", "Balance Sheet")
A["broken_period_interest_recovery_account"] = ensure_account("Broken Period Interest", "Accounts Receivable", "Asset", "Receivable", "Balance Sheet")
A["interest_income_account"] = ensure_account("Interest Income Account", "Direct Income", "Income", "Income Account", "Profit and Loss")
A["penalty_income_account"] = ensure_account("Penalty Income Account", "Direct Income", "Income", "Income Account", "Profit and Loss")
A["additional_interest_income"] = ensure_account("Additional Interest Income Account", "Direct Income", "Income", "Income Account", "Profit and Loss")
A["suspense_interest_income"] = ensure_account("Suspense Income Account", "Direct Income", "Income", "Income Account", "Profit and Loss")
A["write_off_recovery_account"] = ensure_account("Write Off Recovery", "Direct Income", "Income", "Income Account", "Profit and Loss")
A["interest_waiver_account"] = ensure_account("Interest Waiver Account", "Direct Expenses", "Expense", "Expense Account", "Profit and Loss")
A["penalty_waiver_account"] = ensure_account("Penalty Waiver Account", "Direct Expenses", "Expense", "Expense Account", "Profit and Loss")
A["additional_interest_waiver"] = ensure_account("Additional Interest Waiver", "Direct Expenses", "Expense", "Expense Account", "Profit and Loss")
A["write_off_account"] = ensure_account("Write Off Account", "Direct Expenses", "Expense", "Expense Account", "Profit and Loss")
A["customer_refund_account"] = ensure_account("Customer Refund Account", "Current Liabilities", "Liability", "Payable", "Balance Sheet")
A["security_deposit_account"] = ensure_account("Security Deposit Account", "Current Liabilities", "Liability", "", "Balance Sheet")
# the clearing account is a Bank-type current asset: a Mode of Payment and a Payment Entry only accept
# Bank, Cash or Receivable accounts, and Lending disburses to Bank or Cash accounts; it zeroes out per sale
clearing = frappe.db.get_value("Account", {"account_name": CONFIG["clearing_account"], "company": company}, "name")
if clearing and frappe.db.get_value("Account", clearing, "account_type") != "Bank":
    if frappe.db.exists("GL Entry", {"account": clearing, "is_cancelled": 0}):
        frappe.throw(f"{clearing} has postings but is not a Bank-type account; fix it by hand")
    frappe.delete_doc("Account", clearing, ignore_permissions=True); log.append(f"replaced {clearing}")
clearing = ensure_account(CONFIG["clearing_account"], "Current Assets", "Asset", "Bank", "Balance Sheet")

# money accounts: the default cash account for repayments, the clearing account for disbursements
cash = co.default_cash_account or frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name")
if not cash:
    frappe.throw("no cash account on the company")
A["payment_account"] = cash
A["disbursement_account"] = clearing

# 3. mode of payment that pays an invoice from the clearing account
mop = CONFIG["clearing_mode_of_payment"]
if not frappe.db.exists("Mode of Payment", mop):
    frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": mop, "type": "General", "enabled": 1,
                    "accounts": [{"company": company, "default_account": clearing}]}).insert(ignore_permissions=True)
    log.append(f"mode of payment {mop}")
else:
    m = frappe.get_doc("Mode of Payment", mop)
    if not any(r.company == company for r in m.accounts):
        m.append("accounts", {"company": company, "default_account": clearing}); m.save(ignore_permissions=True)
        log.append(f"mode of payment {mop}: account for {company}")

# 4. demand offset orders + company collection sequences
def offset_order(title, components):
    name = frappe.db.get_value("Loan Demand Offset Order", {"title": title}, "name")
    if name:
        return name
    doc = frappe.new_doc("Loan Demand Offset Order"); doc.title = title
    for c in components:
        doc.append("components", {"demand_type": c})
    doc.insert(ignore_permissions=True); log.append(f"offset order {title}")
    return doc.name
emi_order = offset_order("Standard EMI Loan Demand Offset Order", ["EMI (Principal + Interest)", "Penalty", "Additional Interest", "Charges"])
demand_order = offset_order("Demand Loan Demand Offset Order", ["Penalty", "Interest", "Principal"])
co.reload()
changed = False
for f, v in [("collection_offset_sequence_for_standard_asset", emi_order),
             ("collection_offset_sequence_for_sub_standard_asset", emi_order),
             ("collection_offset_sequence_for_written_off_asset", demand_order),
             ("collection_offset_sequence_for_settlement_collection", demand_order)]:
    if not co.get(f):
        co.set(f, v); changed = True
if changed:
    co.save(ignore_permissions=True); log.append("company collection sequences")

# 5. the loan product
existing = frappe.db.get_value("Loan Product", {"product_code": CONFIG["product_code"], "company": company}, "name")
p = frappe.get_doc("Loan Product", existing) if existing else frappe.new_doc("Loan Product")
p.update({
    "company": company, "product_code": CONFIG["product_code"], "product_name": CONFIG["product_name"],
    "is_term_loan": 1, "repayment_schedule_type": "Monthly as per repayment start date",
    "maximum_loan_amount": CONFIG["maximum_loan_amount"], "rate_of_interest": CONFIG["rate_of_interest"],
    "penalty_interest_rate": CONFIG["penalty_interest_rate"], "grace_period_in_days": CONFIG["grace_period_in_days"],
    "emi_rounding_method": "Round Up", "disabled": CONFIG["disabled"],
    "collection_offset_sequence_for_standard_asset": emi_order,
    "collection_offset_sequence_for_sub_standard_asset": emi_order,
    "collection_offset_sequence_for_written_off_asset": demand_order,
    "collection_offset_sequence_for_settlement_collection": demand_order,
    **A,
})
p.save(ignore_permissions=True)
log.append(f"loan product {p.name} ({'updated' if existing else 'created'})")

frappe.db.commit()
print("LENDING-SETUP-OK", company, "|", "; ".join(log) or "nothing to do")
