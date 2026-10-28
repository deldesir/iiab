"""Outbound integrations: the fleet site (mirror, receivables, reports, mail) and the monitor's push URLs; the agent tokens derived from the hub's secret."""
from __future__ import annotations

import hashlib
import hmac
import json

from .config import PUSH_CONF, RELEASES, load_catalog, read_conf, set_conf
from .releases import manifest_apps, read_manifest
from .state import latest_checks


def latest_report(box: dict) -> dict | None:
    """Push-mode boxes: the newest report fleet-agent stored in the fleet site."""
    conf = load_push_conf()
    if not conf:
        return None
    import requests
    try:
        r = requests.get(f"{conf['FLEET_SITE_URL'].rstrip('/')}/api/method/fleet_ops.api.latest_report", params={"box_id": box["id"]},
                         headers={"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}", "Host": conf["FLEET_SITE_NAME"],
                                  "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]}, timeout=30)
        r.raise_for_status()
        return r.json().get("message") or None
    except Exception as e:
        return {"error": str(e)}


def push_token_for(box_id: str, master: str) -> str:
    return hmac.new(master.encode(), f"box:{box_id}".encode(), hashlib.sha256).hexdigest()[:32]


def kuma_push(url: str, token: str, status: str, msg: str) -> None:
    try:
        import requests
        requests.get(f"{url.rstrip('/')}/api/push/{token}", params={"status": status, "msg": msg[:250]}, timeout=10)
    except Exception as e:
        print(f"kuma push: {e}")


def kuma_heartbeats(results: dict[str, dict], all_checks: dict[str, dict], whole_fleet: bool) -> None:
    conf = read_conf(PUSH_CONF)
    url, master = conf.get("KUMA_URL"), conf.get("KUMA_PUSH_TOKEN")
    if not (url and master):
        return
    for bid, c in results.items():
        down = c["status"] in ("CRIT", "STALE")
        first = next((x["message"] for x in c.get("findings", []) if x["level"] in ("CRIT", "STALE")), None)
        kuma_push(url, push_token_for(bid, master), "down" if down else "up", f"{c['status']}: {first}" if first else c["status"])
    if whole_fleet:
        bad = [i for i, c in all_checks.items() if c.get("status") in ("CRIT", "STALE")]
        kuma_push(url, master, "up", ("CRIT/STALE: " + ", ".join(bad)) if bad else "CHECK-OK")


def agent_secret() -> str:
    conf = read_conf(PUSH_CONF)
    s = conf.get("FLEET_AGENT_SECRET")
    if not s:
        import secrets
        s = secrets.token_hex(32)
        set_conf(PUSH_CONF, "FLEET_AGENT_SECRET", s)
    return s


def agent_token_for(box_id: str) -> str:
    return hmac.new(agent_secret().encode(), f"agent:{box_id}".encode(), hashlib.sha256).hexdigest()[:40]


def load_push_conf() -> dict | None:
    conf = read_conf(PUSH_CONF)
    need = ("FLEET_SITE_URL", "FLEET_SITE_NAME", "FLEET_API_KEY", "FLEET_API_SECRET")
    return conf if all(conf.get(k) for k in need) else None


def push(inv: dict, quiet: bool = False) -> int:
    conf = load_push_conf()
    if not conf:
        if not quiet:
            print(f"push: {PUSH_CONF} missing or incomplete; nothing mirrored")
        return 0
    import requests
    s = requests.Session()
    s.headers.update({"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}",
                      "Host": conf["FLEET_SITE_NAME"], "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]})
    base = conf["FLEET_SITE_URL"].rstrip("/")
    checks = latest_checks(inv)
    catalog = load_catalog()
    releases = []
    for p in sorted(RELEASES.glob("*.manifest")):
        m = read_manifest(p.stem)
        releases.append({"release": p.stem, "captured": m.get("captured"), "notes": m.get("notes", ""), "apps": manifest_apps(m)})
    payload = {"current_release": inv.get("current_release") or "", "releases": releases, "boxes": [],
               "catalog": [{"service_id": k, **{kk: vv for kk, vv in v.items() if kk != "probes"}, "probes": json.dumps(v.get("probes") or v.get("hub_probes") or [])}
                           for k, v in catalog.get("services", {}).items()]}
    secret_present = bool(read_conf(PUSH_CONF).get("FLEET_AGENT_SECRET"))
    for b in inv["boxes"]:
        c = checks.get(b["id"])
        payload["boxes"].append({
            "box_id": b["id"], "alias": b["alias"], "host": b["host"], "business": b["business"], "role": b["role"], "owner": b["owner"],
            "profile": b.get("profile", ""), "plan": b.get("plan", ""), "status": b["status"], "canary_rank": b["canary_rank"],
            "timezone": b.get("timezone", ""), "maintenance_window": b.get("maintenance_window", ""),
            "connectivity": b["connectivity"], "services": b["services"],
            "billing_contact": (b.get("contacts") or {}).get("billing", ""), "technical_contact": (b.get("contacts") or {}).get("technical", ""),
            "agent_token_hash": hashlib.sha256(agent_token_for(b["id"]).encode()).hexdigest() if secret_present else "",
            "check": c})
    try:
        r = s.post(f"{base}/api/method/fleet_ops.api.sync", json=payload, timeout=90)
        r.raise_for_status()
        msg = r.json().get("message", {})
    except Exception as e:
        print(f"push: failed: {e}")
        return 1
    if not quiet:
        print(f"push: {msg}")
    return 0


def cmd_push(a, inv):
    return push(inv)


def site_call(method: str, payload: dict, timeout: int = 60) -> tuple[bool, object]:
    """POST a whitelisted fleet_ops method on the fleet site; (ok, message)."""
    conf = load_push_conf()
    if not conf:
        return False, f"{PUSH_CONF} missing or incomplete"
    import requests
    try:
        r = requests.post(f"{conf['FLEET_SITE_URL'].rstrip('/')}/api/method/{method}", json=payload, timeout=timeout,
                          headers={"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}",
                                   "Host": conf["FLEET_SITE_NAME"], "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]})
        r.raise_for_status()
        return True, r.json().get("message")
    except Exception as e:
        return False, str(e)


def site_get(method: str, params: dict | None = None, timeout: int = 60):
    """GET a whitelisted fleet_ops method on the fleet site; the message, or None when unreachable."""
    conf = load_push_conf()
    if not conf:
        return None
    import requests
    try:
        r = requests.get(f"{conf['FLEET_SITE_URL'].rstrip('/')}/api/method/{method}", params=params or {}, timeout=timeout,
                         headers={"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}",
                                  "Host": conf["FLEET_SITE_NAME"], "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]})
        r.raise_for_status()
        return r.json().get("message")
    except Exception as e:
        print(f"fleet site: {method} failed: {e}")
        return None
