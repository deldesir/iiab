# ERPNext README

[ERPNext](https://erpnext.com) is the world's best free and open-source Enterprise Resource Planning (ERP) system, designed for manufacturing, retail, distribution, education, and healthcare businesses. Installing it on [Internet-in-a-Box](https://internet-in-a-box.org) brings an entire suite of business and school administration tools natively offline.

This Ansible playbook integrates the powerful [Frappe framework](https://frappeframework.com) directly into the IIAB service ecosystem.

## What's Included

The ERPNext suite contains numerous core modules covering comprehensive business domains:

- **Accounting & Financials**
- **Inventory & Order Management** 
- **Human Resources (HRMS) & Payroll**
- **Education/School Management** (Student records, assessments, scheduling)
- **Healthcare Management**

## Install It

(1) Set these two variables in [/etc/iiab/local_vars.yml](http://FAQ.IIAB.IO#What_is_local_vars.yml_and_how_do_I_customize_it%3F) prior to installing Internet-in-a-Box (or before running `./runrole erpnext`):

    erpnext_install: True
    erpnext_enabled: True

(2) Important Note on Hardware: 
ERPNext runs on the Python-based Frappe Framework and requires significant backend resources (MariaDB, Node.js, Redis, and Python workers).
* **RAM:** We strongly recommend a **minimum of 2GB RAM** (4GB preferred) for your IIAB server.
* **Storage:** The installation downloads a large number of dependencies. Ensure you have at least **3GB+ of free disk space** available on your root drive before installing. 

(3) Check Advanced Variables (Optional):
You can review sizing profiles and default passwords in `/opt/iiab/iiab/roles/default_vars.yml` (e.g. `erpnext_admin_password`).

## Using It

Log in to ERPNext at http://box/erp, http://box.lan/erp, http://10.10.10.10/erp (or similar) using:

    Username: Administrator
    Password: changeme

*(The default Administrator password is automatically configured. It is strongly advised to change this as soon as you log in via the web interface).*

## SSL / Domain Configuration

When running ERPNext behind a public domain with SSL (e.g. `https://example.com`), you **must** set `erpnext_host_name` in `/etc/iiab/local_vars.yml` so that Frappe knows its full public URL including the `/erp` subpath:

    erpnext_host_name: "https://example.com/erp"

Without this, Frappe cannot resolve the subpath prefix and the `/erp` route will return 404 errors. The setup wizard will also be unreachable.

**Why is this necessary?** The ERPNext role runs before the SSL/certbot role in the IIAB playbook. At install time, the domain and certificate may not exist yet. Setting `erpnext_host_name` explicitly ensures the site config is correct regardless of role ordering.

If you forget to set it before install, you can fix it manually:

    sudo -u frappe bash -c 'cd /home/frappe/frappe-bench && \
      ~/.local/bin/bench --site site.local set-config host_name "https://example.com/erp"'
    systemctl restart frappe-bench-frappe-web.service

## Technical Details

To remain deeply integrated with IIAB:
* ERPNext runs cleanly over IIAB's Nginx proxy via a dedicated socket listener at `127.0.0.1:8000` (`/etc/nginx/conf.d/erpnext-nginx.conf`).
* All background schedulers, queues, and web workers are converted from supervisor into native IIAB **Systemd** targets (e.g., `frappe-bench.target`). You can reboot them safely at any time using `systemctl start frappe-bench.target`.

