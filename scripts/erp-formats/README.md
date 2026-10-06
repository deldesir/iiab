# ERP print formats and accounting defaults

Site-level ERPNext objects the fleet's benches need but that no app ships. Each loader is
idempotent and runs with the bench's Python from the bench's `sites` directory:

```bash
cd /home/frappe/frappe-bench/sites
sudo -u frappe ../env/bin/python /path/to/load_print_format.py --site site.local \
    --name "Demande de matériel" --doctype "Material Request" \
    --html demande_de_materiel.html --css erp_letter.css --module Stock --language fr --default
sudo -u frappe ../env/bin/python /path/to/setup_payment_terms.py --site site.local --days 30 --apply
```

| File | What |
|---|---|
| `erp_letter.css` | Shared stylesheet of the Letter/A4 formats below: `@page` size Letter (honoured by the chrome PDF generator; wkhtmltopdf follows Print Settings), ink-friendly tables, signature block. |
| `demande_de_materiel.html` | Material Request: type and status in French, transfer source/destination columns, signatures demandeur / approbateur / magasinier. |
| `bon_de_transfert.html` | Stock Entry: title follows the purpose (bon de transfert / de sortie / de réception...), linked Material Requests, per-line source and destination, lot / serial column when present, signatures préparé / remis / reçu. |
| `recu_de_paiement.html` | Payment Entry: reçu de paiement (encaissement) or bon de décaissement, amount in figures and words, allocated documents with what remains due, unallocated advance, deductions. |
| `load_print_format.py` | Creates or updates a Jinja Print Format from an html + css file; `--default` makes it the doctype's default. |
| `setup_payment_terms.py` | Payment Term + Payment Terms Template "N jours" set as a company's default, so credit sales get a real due date instead of falling due the day they are posted. Dry run without `--apply`. |
| `setup_print_settings.py` | Print Settings for office documents: PDF page size Letter (receipts print through the browser/QZ, never through PDF), optional base font size. |

Load all three formats on a site:

```bash
cd /home/frappe/frappe-bench/sites; D=/path/to/erp-formats; P=../env/bin/python
sudo -u frappe $P $D/load_print_format.py --site site.local --name "Demande de matériel" --doctype "Material Request" --html $D/demande_de_materiel.html --css $D/erp_letter.css --module Stock --language fr --default
sudo -u frappe $P $D/load_print_format.py --site site.local --name "Bon de transfert"    --doctype "Stock Entry"      --html $D/bon_de_transfert.html    --css $D/erp_letter.css --module Stock --language fr --default
sudo -u frappe $P $D/load_print_format.py --site site.local --name "Reçu de paiement"    --doctype "Payment Entry"    --html $D/recu_de_paiement.html    --css $D/erp_letter.css --module Accounts --language fr --default
```
