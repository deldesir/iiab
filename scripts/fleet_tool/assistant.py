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
from .transport import bench, refresh_checkout, run_on, scp_to

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


def _deliver(box: dict, reg: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(reg, f, indent=1)
        tmp = Path(f.name)
    tmp.chmod(0o600)
    try:
        scp_to(box, tmp, GATEWAY_REGISTRY, "0600")
        run_on(box, f"chown {GATEWAY_USER}:{GATEWAY_USER} {GATEWAY_REGISTRY}")
    finally:
        tmp.unlink(missing_ok=True)


def local_registry(inv: dict, box: dict) -> dict:
    """A box with the retail line keeps its own registry: itself as the one tenant, reached on its
    loopback, and only the numbers bound to it. Nothing of another box ever lands there."""
    full = registry(inv)
    me = dict(full["tenants"].get(box["id"]) or {})
    if not me:
        return {"tenants": {}, "bindings": {}}
    me["url"] = "http://127.0.0.1/erp"
    bindings = {d: {"tenants": [box["id"]], "default": box["id"], "role": b["role"], "lang": b["lang"]}
                for d, b in full["bindings"].items() if box["id"] in b["tenants"]}
    return {"tenants": {box["id"]: me}, "bindings": bindings}


def push(inv: dict) -> str:
    """The operator's registry to the box that runs the assistant; a box with the retail line gets its own."""
    lines = []
    reg = registry(inv)
    gw = next((b for b in inv["boxes"] if "whatsapp-assistant" in (b.get("services") or [])), None)
    if gw:
        _deliver(gw, reg)
        lines.append(f"registry on {label(gw)}: {len(reg['tenants'])} shop(s), {len(reg['bindings'])} number(s)")
    for b in inv["boxes"]:
        if "retail-line" in (b.get("services") or []) and b["id"] in reg["tenants"]:
            local = local_registry(inv, b)
            _deliver(b, local)
            lines.append(f"own registry on {label(b)}: {len(local['bindings'])} number(s)")
    if not lines:
        sys.exit("fleet: no box carries the whatsapp-assistant or the retail-line service in the inventory")
    return "\n".join(lines)


LINE_TOKEN_FILE = "/etc/iiab/wuzapi_line_token"


def qr(box: dict) -> Path:
    """The bridge's pairing code for the shop's number: connect the line's user and fetch the QR as a PNG
    kept on the hub (the shop's phone scans it from a screen; the kit's page shows how)."""
    cmd = (f"t=$(cat {LINE_TOKEN_FILE}); curl -s -X POST -H \"Authorization: $t\" -H 'Content-Type: application/json' "
           f"-d '{{\"Subscribe\":[\"Message\"],\"Immediate\":true}}' http://127.0.0.1:8095/session/connect >/dev/null; sleep 3; "
           f"curl -s -H \"Authorization: $t\" http://127.0.0.1:8095/session/qr")
    rc, out = run_on(box, cmd, timeout=60)
    if rc != 0 or '"QRCode"' not in out:
        sys.exit(f"fleet: no pairing code from {label(box)}: {out.strip()[-200:] or f'rc={rc}'}")
    data = json.loads(out).get("data") or {}
    code = (data.get("QRCode") or "").split(",", 1)[-1]
    if not code:
        sys.exit(f"fleet: {label(box)} is already paired, or the bridge gave no code")
    import base64
    KEYS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = KEYS_DIR / f"{box['id']}-qr.png"
    p.write_bytes(base64.b64decode(code))
    p.chmod(0o600)
    return p


RETAIL_BLOCK_MARK = "# --- retail line (fleet assistant line) ---"


def line_vars(box: dict) -> str:
    """The retail line's variables appended to a box's local vars (the preset carries the same block)."""
    return "\n".join([
        RETAIL_BLOCK_MARK,
        # The line's units come from the public release mirrors (releases only, no history): a box holds no credential.
        "ai_gateway_repo: \"https://github.com/deldesir/gateway-release.git\"",
        "rivebot_repo: \"https://github.com/deldesir/rivebot-release.git\"",
        "ai_update_skip: []",
        "rapidpro_install: True", "rapidpro_enabled: True", "rapidpro_retail_line: True",
        "wuzapi_install: True", "wuzapi_enabled: True",
        "wuzapi_admintoken: \"{{ lookup('password', iiab_etc_path ~ '/wuzapi_admintoken chars=ascii_letters,digits length=40') }}\"",
        "ai_gateway_install: True", "ai_gateway_enabled: True",
        "ai_gateway_profile: retail", "ai_gateway_postgres_uri: \"\"", "ai_gateway_default_persona: shop", "ai_gateway_admin_phone: \"\"",
        "ai_gateway_authorized_users: \"\"", "ai_gateway_jobs: {}", f"ai_gateway_retail_line_name: \"{box.get('business') or box['id']}\"",
        "ai_gateway_api_key: \"{{ lookup('password', iiab_etc_path ~ '/ai_gateway_api_key chars=ascii_letters,digits length=40') }}\"",
        "ai_gateway_wuzapi_token: \"{{ lookup('password', iiab_etc_path ~ '/wuzapi_line_token chars=ascii_letters,digits length=32') }}\"",
        "rivebot_install: True", "rivebot_enabled: True",
        "rivebot_gateway_internal_key: \"{{ lookup('password', iiab_etc_path ~ '/gateway_internal_key chars=ascii_letters,digits length=40') }}\"",
        "# --- end retail line ---", ""])


LOCAL_VARS = "/etc/iiab/local_vars.yml"


def supersede_command(box: dict) -> str:
    """Comment out, above the block, every earlier definition of a key the block sets. Ansible keeps
    the last of duplicate keys, but a role that installed once keeps what it rendered then: a till
    carried a bridge block from an older preset, with another admin token, and the gateway role
    was refused by the bridge. The address spares the block itself, present or not."""
    keys = "|".join(sorted(set(re.findall(r"^([a-z_]+):", line_vars(box), re.M))))
    mark = RETAIL_BLOCK_MARK.replace("(", "\\(").replace(")", "\\)")
    return f"sed -i -E '/^{mark}/,$!{{s/^({keys}):(.*)$/# \\1:\\2   # superseded by the retail line block/}}' {LOCAL_VARS}"


def enable_line(box: dict) -> str:
    """Bring the box's IIAB checkout to master, supersede the older definitions, append the block
    once and run the four roles on the box; the inventory gains the service."""
    ok, note = refresh_checkout(box)
    if not ok:
        return f"the line did not start on {label(box)}: {note}"
    run_on(box, f"cp -a {LOCAL_VARS} {LOCAL_VARS}.bak-$(date +%Y%m%d-%H%M%S) && " + supersede_command(box))
    rc, out = run_on(box, f"grep -q '{RETAIL_BLOCK_MARK}' {LOCAL_VARS} && echo present || echo absent")
    if "absent" in out:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("\n" + line_vars(box))
            tmp = Path(f.name)
        try:
            scp_to(box, tmp, "/root/fleet-retail-line.yml", "0600")
        finally:
            tmp.unlink(missing_ok=True)
        run_on(box, f"cat /root/fleet-retail-line.yml >> {LOCAL_VARS} && rm -f /root/fleet-retail-line.yml")
    rc, out = run_on(box, "cd /opt/iiab/iiab && for r in wuzapi ai-gateway rivebot rapidpro; do ./runrole $r > /root/fleet-retail-line-$r.log 2>&1 || { echo \"ROLE-FAILED $r\"; tail -5 /root/fleet-retail-line-$r.log; exit 1; }; done; echo ROLES-OK", timeout=3600)
    if rc != 0 or "ROLES-OK" not in out:
        return f"the line's roles did not finish on {label(box)}: {out.strip()[-400:]}"
    return f"the line's roles ran on {label(box)} ({note}; logs /root/fleet-retail-line-*.log there)"


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
    if a.action == "qr":
        box = find_box(inv, a.id)
        p = qr(box)
        log_action(f"assistant qr {box['id']}")
        print(f"pairing code for {label(box)} saved at {p} (0600); open it on a screen and scan it from the shop's phone, WhatsApp › Linked devices")
        return 0
    if a.action == "line":
        box = find_box(inv, a.id)
        result = enable_line(box)
        print(result)
        if "roles ran" not in result:
            log_action(f"assistant line {box['id']} result=FAILED")
            return 1
        for b in inv["boxes"]:
            if b["id"] == box["id"]:
                b["services"] = list(b.get("services") or []) + [x for x in ("retail-line", "rapidpro") if x not in (b.get("services") or [])]
        save_inventory(inv)
        log_action(f"assistant line {box['id']}")
        print(f"{label(box)} carries the retail-line and rapidpro services. Next: fleet assistant setup {box['id']}; bind; push; then the shop's "
              f"number is claimed in RapidPro on the box (Add channel, WhatsApp bridge) and its code scanned from the shop's phone")
        return 0
    if a.action == "rotate":
        box = find_box(inv, a.id)
        old = keys_for(box["id"]) or {}
        setup(box, bool(a.writer or old.get("writer")))
        log_action(f"assistant rotate {box['id']}")
        print(f"key pair renewed on {label(box)}; " + push(inv))
        return 0
    sys.exit(f"fleet: unknown assistant action {a.action}")
