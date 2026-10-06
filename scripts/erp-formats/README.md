# ERP print formats and accounting defaults

Site-level ERPNext objects the fleet's benches need but that no app ships. Each loader is
idempotent and runs with the bench's Python from the bench's `sites` directory:

```bash
cd /home/frappe/frappe-bench/sites
sudo -u frappe ../env/bin/python /path/to/load_print_format.py --site site.local \
    --name "Demande de matériel" --doctype "Material Request" \
    --html demande_de_materiel.html --css demande_de_materiel.css --module Stock --language fr --default
sudo -u frappe ../env/bin/python /path/to/setup_payment_terms.py --site site.local --days 30 --apply
```

| File | What |
|---|---|
| `demande_de_materiel.html` / `.css` | Material Request on a normal Letter/A4 printer, French labels, signature block; `@page` size Letter (honoured by the chrome PDF generator; wkhtmltopdf follows Print Settings). |
| `load_print_format.py` | Creates or updates a Jinja Print Format from the files; `--default` makes it the doctype's default. |
| `setup_payment_terms.py` | Payment Term + Payment Terms Template "N jours" set as a company's default, so credit sales get a real due date instead of falling due the day they are posted. Dry run without `--apply`. |
