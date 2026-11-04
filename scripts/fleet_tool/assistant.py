"""The retail line's identities: an assistant user on a box, the numbers bound to it, and the
registry the gateway reads (``data/tenants.json`` on the box that runs the WhatsApp assistant).

The box answers only to numbers bound to it, and the binding is the only place a number meets
a box: the gateway resolves the shop from it, never from a message. Key pairs are made on the
box, kept on the hub under the fleet state (mode 0600), and travel to the gateway in the
registry; they are never printed."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from . import config
from .config import label, log_action, save_inventory
from .transport import bench, run_on, scp_to

KEYS_DIR = Path(os.environ.get("FLEET_ASSISTANT_KEYS", str(config.STATE / "assistant")))
BOX_SCRIPT = "/home/frappe/fleet-assistant-user.py"   # readable by the bench user, removed after the run
GATEWAY_REGISTRY = "/opt/iiab/ai-gateway/data/tenants.json"
GATEWAY_USER = "iiab-admin"
ROLES = ("owner", "manager", "cashier")
LANGS = ("ht", "fr", "en")


def digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")


def find_box(inv: dict, ref: str) -> dict:
    for b in inv["boxes"]:
        if ref in (b["id"], b.get("alias")):
            return b
    sys.exit(f"fleet: no box {ref}")


def gateway_box(inv: dict) -> dict:
    for b in inv["boxes"]:
        if "whatsapp-assistant" in (b.get("services") or []):
            return b
    sys.exit("fleet: no box carries the whatsapp-assistant service in the inventory")


def keys_for(box_id: str) -> dict | None:
    p = KEYS_DIR / f"{box_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def setup(box: dict, writer: bool) -> dict:
    """Create or re-key the assistant user on the box; keep the pair on the hub."""
    script = config.SCRIPTS / "fleet-tasks" / "assistant-user.py"
    scp_to(box, script, BOX_SCRIPT, "0644")
    cmd = bench(box, f"--site {box['site']} execute \"exec(open('{BOX_SCRIPT}').read(), {{'WRITER': {writer}}})\"")
    rc, out = run_on(box, cmd + f"; rm -f {BOX_SCRIPT}", timeout=300)
    line = next((ln for ln in out.splitlines() if ln.startswith("ASSISTANT-USER ")), "")
    if rc != 0 or not line:
        sys.exit(f"fleet: the assistant user could not be set up on {label(box)} (rc={rc}): {out.strip()[-300:]}")
    info = json.loads(line[len("ASSISTANT-USER "):])
    KEYS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = KEYS_DIR / f"{box['id']}.json"
    p.write_text(json.dumps(info, indent=1))
    p.chmod(0o600)
    return info


def box_url(box: dict) -> str:
    if box.get("local"):
        return "http://127.0.0.1/erp"
    return (box.get("reach") or {}).get("http", f"https://{box['host']}").rstrip("/") + "/erp"


def registry(inv: dict) -> dict:
    """The gateway's registry from the inventory and the hub's key store: tenants with a key pair, bindings merged by number."""
    tenants: dict = {}
    bindings: dict = {}
    for b in inv["boxes"]:
        rows = (b.get("assistant") or {}).get("bindings") or []
        keys = keys_for(b["id"])
        if not rows or not keys:
            continue
        tenants[b["id"]] = {"alias": b.get("alias") or b["id"], "name": b.get("business") or b.get("alias") or b["id"],
                            "url": box_url(b), "key": keys["api_key"], "secret": keys["api_secret"], "writer": bool(keys.get("writer")),
                            "currency": b.get("currency") or inv.get("defaults", {}).get("currency") or "HTG",
                            "tz": b.get("timezone") or inv.get("defaults", {}).get("timezone") or "America/Port-au-Prince",
                            "lang": b.get("language") or "ht", "low_stock": int((b.get("assistant") or {}).get("low_stock") or 5)}
        for r in rows:
            d = digits(r["number"])
            entry = bindings.setdefault(d, {"tenants": [], "default": None, "role": r.get("role") or "owner", "lang": r.get("lang") or ""})
            entry["tenants"].append(b["id"])
            if r.get("default") or entry["default"] is None:
                entry["default"] = b["id"]
    return {"tenants": tenants, "bindings": bindings}


def push(inv: dict) -> str:
    reg = registry(inv)
    gw = gateway_box(inv)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(reg, f, indent=1)
        tmp = Path(f.name)
    tmp.chmod(0o600)
    try:
        scp_to(gw, tmp, GATEWAY_REGISTRY, "0600")
        run_on(gw, f"chown {GATEWAY_USER}:{GATEWAY_USER} {GATEWAY_REGISTRY}")
    finally:
        tmp.unlink(missing_ok=True)
    return f"registry on {label(gw)}: {len(reg['tenants'])} shop(s), {len(reg['bindings'])} number(s)"


def cmd_assistant(a, inv):
    if a.action == "setup":
        box = find_box(inv, a.id)
        info = setup(box, bool(a.writer))
        log_action(f"assistant setup {box['id']} writer={bool(a.writer)}")
        print(f"assistant user on {label(box)}: {info['email']} (reader{', writer' if info.get('writer') else ''}); key pair kept on the hub")
        print(f"Next: fleet assistant bind {box['id']} <number> --role owner; fleet assistant push")
        return 0
    if a.action in ("bind", "unbind"):
        box = find_box(inv, a.id)
        d = digits(a.number)
        if len(d) < 8:
            sys.exit(f"fleet: {a.number!r} is not a phone number")
        rows = [r for r in ((box.get("assistant") or {}).get("bindings") or []) if digits(r["number"]) != d]
        if a.action == "bind":
            if a.role not in ROLES:
                sys.exit(f"fleet: role must be one of {', '.join(ROLES)}")
            if a.lang and a.lang not in LANGS:
                sys.exit(f"fleet: lang must be one of {', '.join(LANGS)}")
            rows.append({k: v for k, v in {"number": d, "role": a.role, "lang": a.lang or "", "default": bool(a.default)}.items() if v not in ("", False)})
        for b in inv["boxes"]:
            if b["id"] == box["id"]:
                b.setdefault("assistant", {})["bindings"] = rows
                if not rows:
                    b["assistant"].pop("bindings", None)
                    if not b["assistant"]:
                        b.pop("assistant")
        save_inventory(inv)
        log_action(f"assistant {a.action} {box['id']} number=…{d[-4:]} role={getattr(a, 'role', '-')}")
        print(f"{a.action}: …{d[-4:]} {'→' if a.action == 'bind' else '×'} {label(box)}" + (f" as {a.role}" if a.action == "bind" else ""))
        if not a.no_push:
            print(push(inv))
        return 0
    if a.action == "list":
        print(f"{'box':<8} {'alias':<16} {'number':<16} {'role':<8} {'lang':<5} default  keys")
        for b in inv["boxes"]:
            rows = (b.get("assistant") or {}).get("bindings") or []
            keys = keys_for(b["id"])
            for r in rows:
                d = digits(r["number"])
                print(f"{b['id']:<8} {b.get('alias') or '':<16} {'…' + d[-6:]:<16} {r.get('role') or 'owner':<8} {r.get('lang') or '-':<5} {'yes' if r.get('default') else '':<8} {'yes' if keys else 'NONE (fleet assistant setup)'}")
        return 0
    if a.action == "push":
        print(push(inv))
        return 0
    if a.action == "rotate":
        box = find_box(inv, a.id)
        old = keys_for(box["id"]) or {}
        setup(box, bool(a.writer or old.get("writer")))
        log_action(f"assistant rotate {box['id']}")
        print(f"key pair renewed on {label(box)}; " + push(inv))
        return 0
    sys.exit(f"fleet: unknown assistant action {a.action}")
