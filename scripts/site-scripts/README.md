# Site scripts

Small idempotent scripts run inside a Frappe site on a box, as the `frappe` user, through
`run.py`, which connects to the site as Administrator and executes the script:

```bash
cd /home/frappe/frappe-bench
sudo -u frappe -H env/bin/python scripts/site-scripts/run.py site.local scripts/site-scripts/lending-setup.py
```

| Script | What it does | Where |
|---|---|---|
| `lending-setup.py` | Lending per company: the accounts a Loan Product posts to, demand offset orders, the company's loan settings (accrual frequency, day-count convention), an Installment Clearing bank-type account with its Mode of Payment, one Loan Product. Edit `CONFIG` first; set `disabled: 1` until the terms are confirmed. | any box with `lending` |
| `lending-rehearsal.py` | The installment-sale pattern end to end (invoice, down payment, loan disbursed to the clearing account, invoice settled from it, first demand and installment) and prints the balances. Refuses a site with real invoices unless `demo` is set in site_config. | demo box, hub rehearsal site |
| `insights-dashboard.py` | An owner dashboard in Insights: five live SQL queries (today's sales, 30-day sales by day and by cashier, receivables ageing, stock cover), charts and the "Tableau de bord" dashboard, visible to Accounts and Sales Managers. | any box with `insights` |

Each script ends with an `...-OK` line; anything else is a failure and nothing is committed.
