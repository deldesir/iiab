#!/usr/bin/env python3
"""Generate PORTS.md — the IIAB ports inventory — kept in sync with the source.

Reads the `*_port` defaults from vars/default_vars.yml and cross-checks each
against the firewall template roles/network/templates/gateway/iiab-gen-iptables
(which DROPs internal-only ports from external/WAN access). Writes PORTS.md.

Re-run after adding or changing any `*_port` default:
    python3 scripts/gen_ports_md.py
"""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
dv = (ROOT / "vars" / "default_vars.yml").read_text()
ipt = (ROOT / "roles" / "network" / "templates" / "gateway" / "iiab-gen-iptables").read_text()

# Role-configured ports: "<name>_port: <N>"
ports = {}
for m in re.finditer(r"^([a-z0-9_]+)_port:\s*(\d+)\b", dv, re.M):
    ports[int(m.group(2))] = m.group(1)

# Ports DROPped from external access by the firewall (defense-in-depth).
dropped = {int(p) for p in re.findall(r"--dport\s+(\d+)\s+-j DROP", ipt)}

rows = sorted(ports.items())
out = [
    "# IIAB Ports Inventory",
    "",
    "**Auto-generated — do not hand-edit.** Re-run `scripts/gen_ports_md.py`",
    "after adding/changing a `*_port` default. Source of truth:",
    "`vars/default_vars.yml` (the `*_port` vars) cross-checked against the",
    "firewall template `roles/network/templates/gateway/iiab-gen-iptables`.",
    "",
    "**Firewall (external):** `DROP` = the firewall blocks this port from the",
    "WAN side (loopback/LAN still reach it — defense-in-depth for internal-only",
    "services); `—` = not explicitly dropped (LAN-served, e.g. via Nginx/HTTP).",
    "",
    "| Port | Variable | Firewall (external) |",
    "|-----:|----------|:-------------------:|",
]
for port, name in rows:
    fw = "DROP" if port in dropped else "—"
    out.append(f"| {port} | `{name}_port` | {fw} |")
out += [
    "",
    f"_{len(rows)} role-configured ports; {len([p for p in ports if p in dropped])} "
    "firewalled from WAN._",
    "",
]
(ROOT / "PORTS.md").write_text("\n".join(out))
print(f"Wrote PORTS.md: {len(rows)} ports, {len([p for p in ports if p in dropped])} firewalled")
