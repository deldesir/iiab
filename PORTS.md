# IIAB Ports Inventory

**Auto-generated — do not hand-edit.** Re-run `scripts/gen_ports_md.py`
after adding/changing a `*_port` default. Source of truth:
`vars/default_vars.yml` (the `*_port` vars) cross-checked against the
firewall template `roles/network/templates/gateway/iiab-gen-iptables`.

**Firewall (external):** `DROP` = the firewall blocks this port from the
WAN side (loopback/LAN still reach it — defense-in-depth for internal-only
services); `—` = not explicitly dropped (LAN-served, e.g. via Nginx/HTTP).

| Port | Variable | Firewall (external) |
|-----:|----------|:-------------------:|
| 22 | `sshd_port` | — |
| 80 | `gui_port` | — |
| 83 | `pbx_http_port` | — |
| 631 | `cups_port` | — |
| 1880 | `nodered_port` | — |
| 1883 | `mosquitto_port` | — |
| 3000 | `kiwix_port` | — |
| 3030 | `openclaw_port` | — |
| 4244 | `internetarchive_port` | — |
| 6806 | `siyuan_port` | DROP |
| 8000 | `jupyterhub_port` | — |
| 8008 | `kalite_server_port` | — |
| 8009 | `kolibri_http_port` | — |
| 8080 | `calibre_port` | — |
| 8083 | `calibreweb_port` | DROP |
| 8086 | `ai_gateway_port` | DROP |
| 8087 | `rivebot_port` | DROP |
| 8089 | `sugarizer_port` | — |
| 8095 | `wuzapi_port` | DROP |
| 8096 | `email_bridge_port` | DROP |
| 9090 | `captiveportal_port` | — |
| 9091 | `transmission_http_port` | — |
| 12080 | `azuracast_http_port` | — |
| 12443 | `azuracast_https_port` | — |
| 27017 | `mongodb_port` | — |
| 27701 | `anki_port` | — |
| 30000 | `luanti_port` | — |
| 51413 | `transmission_peer_port` | — |
| 61734 | `gitea_port` | — |

_30 role-configured ports; 7 firewalled from WAN._
