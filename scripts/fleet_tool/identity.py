"""A box's identity in every store at once: onboard and rename."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import ALIAS_RE, ID_RE, PUSH_CONF, SCRIPTS, STATE, label, load_inventory, log_action, read_conf, save_inventory, select_boxes
from .deploy import cmd_vars
from .site import load_push_conf, push, site_call
from .transport import run_local, run_on


def kuma_seed_path() -> Path:
    for p in (Path("/usr/local/sbin/fleet-kuma-seed"), SCRIPTS / "fleet-kuma-seed"):
        if p.exists():
            return p
    sys.exit("fleet: fleet-kuma-seed not found")


def next_box_id(inv: dict) -> str:
    """Next free id: one above every id ever used, in the inventory and in the fleet site's registry
    (offboarded boxes keep their record, so a number is never handed out twice)."""
    nums = [int(b["id"][4:]) for b in inv["boxes"] if ID_RE.match(b["id"])]
    conf = load_push_conf()
    if conf:
        import requests
        try:
            r = requests.get(f"{conf['FLEET_SITE_URL'].rstrip('/')}/api/resource/Managed Box", timeout=30,
                             params={"fields": '["name"]', "limit_page_length": 1000},
                             headers={"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}",
                                      "Host": conf["FLEET_SITE_NAME"], "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]})
            r.raise_for_status()
            nums += [int(d["name"][4:]) for d in r.json().get("data", []) if ID_RE.match(d["name"])]
        except Exception as e:
            print(f"warning: fleet site unreachable ({e}); numbering from the inventory only")
    return f"box-{(max(nums) + 1) if nums else 1:03d}"


def cmd_onboard(a, inv):
    if not ALIAS_RE.match(a.alias):
        sys.exit("fleet: alias must be a short lowercase slug (letters, digits, dashes), e.g. malianova")
    if a.id and not ID_RE.match(a.id):
        sys.exit("fleet: ids look like box-007")
    for b in inv["boxes"]:
        if b["host"] == a.host:
            sys.exit(f"fleet: host {a.host} is already {label(b)}")
        if b["alias"] == a.alias:
            sys.exit(f"fleet: alias {a.alias} is already {label(b)}")
        if a.id and b["id"] == a.id:
            sys.exit(f"fleet: id {a.id} is already {label(b)}")
    new = a.id or next_box_id(inv)
    rank = a.rank if a.rank is not None else max([b["canary_rank"] for b in inv["boxes"]] + [0]) + 1
    box = {"id": new, "alias": a.alias, "host": a.host, "business": a.business, "role": a.role, "owner": a.owner,
           "profile": a.profile, "plan": a.plan or inv["defaults"].get("plan", "standard"), "status": "active",
           "canary_rank": rank, "contacts": {"billing": a.billing or "", "technical": a.technical or ""}}
    if a.connectivity:
        box["connectivity"] = a.connectivity
    if a.services:
        box["services"] = [x.strip() for x in a.services.split(",") if x.strip()]
    if a.timezone:
        box["timezone"] = a.timezone
    if a.window:
        box["maintenance_window"] = a.window
    inv["boxes"].append(box)
    save_inventory(inv)
    inv = load_inventory()
    box = select_boxes(inv, [new], False)[0]
    log_action(f"onboard {new} alias={a.alias} host={a.host} business={a.business!r} role={a.role} owner={a.owner}")
    print(f"registered {label(box)}: {a.business} at {a.host} (role {a.role}, owner {a.owner}, canary rank {rank})")
    push(inv, quiet=True)
    rc, out, err = run_local([str(kuma_seed_path()), "--boxes", new, "--no-notification"])
    print("monitors: " + ("; ".join(l for l in out.splitlines() if l.startswith(("monitor", "KUMA"))) or err.strip()[-300:]))
    rc, _ = run_on(box, "test -r /etc/iiab/local_vars.yml", timeout=30)
    if rc == 0:
        import types
        cmd_vars(types.SimpleNamespace(action="pull", ids=[new], all=False), inv)
    else:
        print(f"not reachable yet over ssh ({box['ssh_user']}@{box['reach']['ssh']}): install this machine's key there, then `fleet vars pull {new}`")
    print(f"\nlocal_vars.yml on the box (before or after its install):\n  iiab_hostname: {new}\n  erpnext_business_name: \"{a.business}\"\n"
          f"next: fleet check {new}; fleet hostname {new} (inside its window); add {a.host} to BOXES in /etc/iiab/fleet-backup.conf"
          f" on the box that runs fleet-backup-mail" + (f"; fleet deploy-agent {new} (push mode)" if box["mode"] == "push" else ""))
    print(f"Check: fleet check {new} once the box is installed; its monitors are in Uptime Kuma ({read_conf(PUSH_CONF).get('KUMA_URL') or 'KUMA_URL unset'})")
    return 0


def cmd_rename(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    old, new = box["id"], a.new_id or box["id"]
    alias = a.alias or (box["alias"] if box["alias"] != old else new)
    if not ID_RE.match(new):
        sys.exit(f"fleet: ids look like box-007 (got {new!r})")
    if not ALIAS_RE.match(alias):
        sys.exit("fleet: alias must be a short lowercase slug (letters, digits, dashes)")
    for b in inv["boxes"]:
        if b is box:
            continue
        if b["id"] == new:
            sys.exit(f"fleet: id {new} is already {label(b)}")
        if b["alias"] == alias:
            sys.exit(f"fleet: alias {alias} is already {label(b)}")
    if (old, box["alias"]) == (new, alias):
        print("nothing to change")
        return 0
    old_alias = box["alias"]
    # 1. inventory (and the top-level pointers to a box)
    box["id"], box["alias"] = new, alias
    for k in ("hub_watchdog_box", "backup_mail_box"):
        if inv.get(k) == old:
            inv[k] = new
    save_inventory(inv)
    # 2. local state keyed by id: check history (with the id inside each record), vars copy, temp confs
    if old != new:
        src, dst = STATE / "checks" / old, STATE / "checks" / new
        if src.exists():
            src.rename(dst)
            for p in dst.glob("*.json"):
                try:
                    d = json.loads(p.read_text())
                    d["id"] = new
                    p.write_text(json.dumps(d, indent=1))
                except Exception as e:
                    print(f"   could not rewrite {p.name}: {e.__class__.__name__}")
        v = Path("/etc/iiab/fleet-vars") / f"{old}.local_vars.yml"
        if v.exists():
            v.rename(v.with_name(f"{new}.local_vars.yml"))
        for p in (STATE / f"agent-{old}.conf", STATE / f"watchdog-{old}.conf"):
            p.unlink(missing_ok=True)
    # 3. the registry (links from checks and subscriptions follow the rename)
    ok, msg = site_call("fleet_ops.api.rename_box", {"old": old, "new": new, "alias": alias})
    print(f"fleet site: {msg if ok else 'FAILED: ' + str(msg)}")
    push(inv, quiet=True)
    # 4. monitors: names carry id + alias; the per-box push token derives from the id
    kuma_rc, out, err = run_local([str(kuma_seed_path()), "--rename", old, new, "--alias", alias])
    print("monitors: " + ("; ".join(l for l in out.splitlines() if l.startswith(("monitor", "KUMA"))) or err.strip()[-300:]))
    log_action(f"rename {old} ({old_alias}) -> {new} ({alias}) site={'ok' if ok else 'failed'} kuma_rc={kuma_rc}")
    print(f"{old} ({old_alias}) is now {new} ({alias})")
    if box["mode"] == "push":
        print(f"push-mode box: run `fleet deploy-agent {new}` (its agent token and push URL changed)")
    if old != new:
        print(f"inside its window: `fleet hostname {new}` renames the machine itself")
    return 0 if ok and kuma_rc == 0 else 1
