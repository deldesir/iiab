"""Status follows the money and the operator: suspend, resume, dunning, offboard."""
from __future__ import annotations

import datetime as dt
import sys

from .checks import judge_receivables
from .config import OFFBOARD_DIR, label, log_action, save_inventory, select_boxes
from .site import push, site_get
from .transport import bench, rsync, run_on


def set_status(inv: dict, box: dict, status: str) -> None:
    for b in inv["boxes"]:
        if b["id"] == box["id"]:
            b["status"] = status
    save_inventory(inv)


def set_field(inv: dict, box: dict, key: str, value) -> None:
    for b in inv["boxes"]:
        if b["id"] == box["id"]:
            if value is None:
                b.pop(key, None)
            else:
                b[key] = value
    save_inventory(inv)


def maintenance_reload(box: dict) -> str:
    """gunicorn workers cache the site config: after flipping maintenance_mode, clear the cache
    and restart the web service (about ten seconds) or some workers keep answering the old way."""
    return bench(box, f"--site {box['site']} clear-cache") + " >/dev/null; systemctl restart frappe-bench-frappe-web.service"


def suspend_box(inv: dict, box: dict, reason: str, cause: str | None = None) -> tuple[bool, str]:
    """Maintenance page on, status suspended. cause='unpaid' marks an automatic suspension so a payment can lift it."""
    if box["owner"] == "self":
        return False, "refusing to suspend one of our own boxes"
    if box["mode"] == "push":
        return False, "an offline box cannot be suspended from the hub (the agent does not apply status yet)"
    rc, out = run_on(box, bench(box, f"--site {box['site']} set-maintenance-mode on") + " && " + maintenance_reload(box))
    if rc != 0:
        return False, out[-300:]
    set_status(inv, box, "suspended")
    set_field(inv, box, "suspended_for", cause)
    log_action(f"suspend {box['id']} reason={reason!r} cause={cause or 'operator'}")
    return True, f"{label(box)} suspended (maintenance page on). Reason: {reason or '-'}"


def resume_box(inv: dict, box: dict, why: str = "") -> tuple[bool, str]:
    rc, out = run_on(box, bench(box, f"--site {box['site']} set-maintenance-mode off") + " && " + maintenance_reload(box))
    if rc != 0:
        return False, out[-300:]
    set_status(inv, box, "active")
    set_field(inv, box, "suspended_for", None)
    log_action(f"resume {box['id']} {why}".rstrip())
    return True, f"{label(box)} resumed"


def cmd_suspend(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    ok, msg = suspend_box(inv, box, a.reason)
    print(msg)
    if ok:
        push(inv, quiet=True)
        print(f"Check: {box['reach']['http']}/ shows the maintenance page\nUndo:  fleet resume {box['id']}")
    return 0 if ok else 1


def cmd_resume(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    ok, msg = resume_box(inv, box)
    print(msg)
    if ok:
        push(inv, quiet=True)
        print(f"Check: fleet check {box['id']}")
    return 0 if ok else 1


def apply_dunning(inv: dict, results: dict[str, dict], apply: bool, quiet: bool = True) -> list[str]:
    """Status follows the money: active -> grace while an invoice is overdue, back to active when
    paid; past the grace, suspend when apply is on (auto_suspend in the inventory, or --apply),
    and lift an automatic suspension once paid. Returns the human lines."""
    lines = []
    for b in list(inv["boxes"]):
        r = results.get(b["id"])
        if not r or b["owner"] == "self" or b["status"] == "offboarded":
            continue
        days = int(r.get("overdue_days") or 0)
        grace = int(b.get("grace_days") or 0)
        if days > 0 and b["status"] == "active":
            set_status(inv, b, "grace"); log_action(f"dunning {b['id']} -> grace ({days} d overdue)")
            lines.append(f"{label(b)}: overdue {days} d -> grace")
        elif days == 0 and b["status"] == "grace":
            set_status(inv, b, "active"); log_action(f"dunning {b['id']} -> active (paid)")
            lines.append(f"{label(b)}: paid -> active")
        elif days == 0 and b["status"] == "suspended" and b.get("suspended_for") == "unpaid":
            if apply:
                ok, msg = resume_box(inv, b, "(paid; automatic)")
                lines.append(f"{label(b)}: paid -> {msg if ok else 'resume FAILED ' + msg}")
            else:
                lines.append(f"{label(b)}: paid; was suspended for non-payment: fleet resume {b['id']}")
        if days > grace and b["status"] in ("active", "grace"):
            if apply:
                ok, msg = suspend_box(inv, b, f"unpaid {days} d past due, grace {grace} d", cause="unpaid")
                lines.append(f"{label(b)}: {msg if ok else 'suspend FAILED ' + msg}")
            else:
                lines.append(f"{label(b)}: {days} d past due, grace {grace} d over -> would suspend (fleet dunning --apply)")
    if not quiet:
        print("\n".join(lines) if lines else "nothing overdue")
    return lines


def cmd_dunning(a, inv):
    catalog = {"_receivables": site_get("fleet_ops.api.receivables") or {}}
    if not catalog["_receivables"]:
        print("no receivables (fleet site unreachable, or no invoices)")
    results = {}
    for b in inv["boxes"]:
        res = {"findings": []}
        judge_receivables(b, res, catalog, lambda level, code, msg, _r=res: _r["findings"].append({"level": level, "code": code, "message": msg}))
        results[b["id"]] = res
        if res["findings"]:
            print(f"{label(b)}: {res['findings'][0]['message']}")
    apply = a.apply or (a.auto and bool(inv.get("auto_suspend")))   # --auto: the timer's run, bound by the inventory's switch
    lines = apply_dunning(inv, results, apply=apply, quiet=True)
    print("\n".join(lines) if lines else "nothing to change")
    if lines and (a.apply or a.auto):
        push(inv, quiet=True)
    return 0


def cmd_offboard(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    if box["owner"] == "self":
        sys.exit("fleet: refusing to offboard one of our own boxes")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = OFFBOARD_DIR / box["id"] / stamp
    dest.mkdir(parents=True, exist_ok=True)
    print(f"== {box['id']}: final backup with files")
    rc, out = run_on(box, bench(box, f"--site {box['site']} backup --with-files"), timeout=1800)
    if rc != 0:
        print(out)
        return 1
    bdir = f"{box['bench']}/sites/{box['site']}/private/backups/"
    src = bdir if box.get("local") else f"{box['ssh_user']}@{box['reach']['ssh']}:{bdir}"
    rc, err = rsync(src, str(dest))
    if rc:
        sys.exit(f"fleet: rsync of the final backup failed: {err[-200:]}")
    (dest / "HANDOVER.txt").write_text(
        f"Offboarding backup for {box['business']} ({box['host']}) taken {stamp}.\n"
        "Contents: the newest *-database.sql.gz, *-files.tar, *-private-files.tar and the site_config backup.\n"
        "Restore on any Frappe bench with the same app set:\n"
        "  bench new-site <site> ; bench --site <site> restore <db>.sql.gz --with-public-files <files.tar> --with-private-files <private.tar>\n"
        "Hand this folder to the customer on the medium agreed in the contract.\n")
    set_status(inv, box, "offboarded")
    log_action(f"offboard {box['id']} backup={dest}")
    print(f"backup at {dest}\n{box['id']} marked offboarded in the inventory.")
    print("Wipe is a manual step after the customer confirms receipt in writing:\n"
          f"  ssh {box['ssh_user']}@{box['reach']['ssh']} \"sudo -u frappe -H bash -lc 'cd {box['bench']} && bench drop-site {box['site']} --force'\"\n"
          "  then remove the box from /etc/iiab/fleet.yml and /etc/iiab/fleet-backup.conf, and its monitors.")
    push(inv, quiet=True)
    return 0
