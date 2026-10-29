"""Commands that run something on a box and follow it: upgrade, rollback, reboot, backup-pull, runrole."""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shlex
import sys
import time
from pathlib import Path

from .checks import _BACKUP_BOX, _set_backup_box, check_box
from .config import HUB_BACKUPS, ROLLBACK_SCRIPT, UPGRADE_SCRIPT, frozen, in_window, label, load_catalog, log_action, require_targets, select_boxes
from .releases import manifest_path, read_manifest
from .site import push
from .state import write_check
from .transport import http_code, rsync, run_on, scp_to


def box_log(box: dict, prefix: str) -> tuple[str, int]:
    """The log a bench script will write on this box today, and how long it already is.

    The scripts name their log by the box's own date, which is not the hub's date for part of
    every day (Haiti and Berlin differ by six hours), and a second run on the same day appends to
    the same file. Following the hub's guess once read yesterday's UPGRADE-OK off the wrong file
    and declared a box done while its services were still stopped. So: ask the box for the date,
    and read only the lines written after the run starts."""
    rc, out = run_on(box, "date +%F")
    today = out.strip() if rc == 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", out.strip()) else f"{dt.date.today():%Y-%m-%d}"
    log = f"/root/{prefix}-{today}.log"
    rc, out = run_on(box, f"wc -l < {log} 2>/dev/null || echo 0")
    start = int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
    return log, start


def upgrade_box(box: dict, rid: str | None, preflight: bool, allow_discard: str | None) -> bool:
    if not UPGRADE_SCRIPT.exists():
        sys.exit(f"fleet: {UPGRADE_SCRIPT} missing")
    scp_to(box, UPGRADE_SCRIPT, "/root/bench-upgrade.sh")
    env = []
    if rid:
        scp_to(box, manifest_path(rid), "/root/bench-upgrade.manifest", "0644")
        env.append("--setenv=MANIFEST=/root/bench-upgrade.manifest")
    if preflight:
        env.append("--setenv=PREFLIGHT_ONLY=1")
    if allow_discard:
        if not re.fullmatch(r"[A-Za-z0-9_.|()^$*+?\[\]{}/-]+", allow_discard):
            sys.exit("fleet: --allow-discard must be a plain regular expression (letters, digits, and . _ | ( ) ^ $ * + ? [ ] { } / -)")
        env.append(shlex.quote(f"--setenv=ALLOW_DISCARD={allow_discard}"))
    unit = f"bench-upgrade-{int(time.time())}"
    log, start = box_log(box, "bench-upgrade")
    rc, out = run_on(box, f"systemd-run --unit={unit} --collect --setenv=HOME=/root {' '.join(env)} /bin/bash /root/bench-upgrade.sh")
    if rc != 0:
        print(f"   could not start the upgrade unit: {out}")
        return False
    print(f"   started {unit}; log {log}")
    deadline, last = time.time() + 3600, ""
    while time.time() < deadline:
        time.sleep(20)
        rc, out = run_on(box, f"tail -n +{start + 1} {log} 2>/dev/null | tail -n 3")
        tail = out.splitlines()[-1] if out else ""
        if tail != last:
            print(f"   {tail}")
            last = tail
        if any(l.startswith(("UPGRADE-OK", "UPGRADE-FAILED", "PREFLIGHT-OK")) for l in out.splitlines()):
            return any(l.startswith(("UPGRADE-OK", "PREFLIGHT-OK")) for l in out.splitlines())
    print("   timed out after 60 minutes")
    return False


def cmd_upgrade(a, inv):
    catalog = load_catalog()
    require_targets(a)
    boxes = select_boxes(inv, a.ids, a.all)
    rid = a.release or inv.get("current_release") or None
    if rid:
        read_manifest(rid)
    print(f"upgrade {'(preflight) ' if a.preflight else ''}order: {', '.join(b['id'] for b in boxes)}; release: {rid or 'fork heads'}")
    frozen_boxes = []
    for b in boxes:
        if b["mode"] == "push":
            print(f"== {b['id']}: offline box; fleet-agent applies release {rid or '(none)'} itself inside its window")
            continue
        if b["status"] in ("suspended", "offboarded") and not a.now:
            print(f"== {b['id']}: skipped ({b['status']})")
            continue
        if not a.preflight and not a.now and (why := frozen(b)):
            print(f"== {b['id']}: skipped, {why}; the rollout goes on without it and it catches up after the freeze (or --now)")
            frozen_boxes.append(b["id"])
            continue
        if not a.preflight and not a.now and not in_window(b):
            print(f"== {b['id']}: outside its maintenance window {b.get('maintenance_window')} ({b.get('timezone')}); use --now")
            return 1
        print(f"== {b['id']} {b['host']}")
        ok = upgrade_box(b, rid, a.preflight, a.allow_discard)
        log_action(f"upgrade {b['id']} release={rid} preflight={a.preflight} result={'ok' if ok else 'FAILED'}")
        if not ok:
            print(f"UPGRADE-FAILED {b['id']} — stopping the rollout")
            return 1
        if not a.preflight:
            r = check_box(b, inv, catalog)
            write_check(r)
            if r["status"] in ("CRIT", "STALE"):
                print(f"   post-upgrade check {r['status']}: {'; '.join(r['problems'])} — stopping the rollout")
                return 1
            print(f"   post-upgrade check {r['status']}")
    print(("UPGRADE-OK" if not a.preflight else "PREFLIGHT-OK") + (f" (frozen, to catch up: {', '.join(frozen_boxes)})" if frozen_boxes else ""))
    return 0


def cmd_rollback(a, inv):
    """Undo the box's last upgrade (bench-rollback.sh on the box, followed like an upgrade)."""
    box = select_boxes(inv, [a.id], False)[0]
    if box["mode"] == "push":
        sys.exit("fleet: an offline box rolls back from its own console (bench-rollback.sh on the box)")
    if not (a.dry_run or a.now or in_window(box)):
        sys.exit(f"fleet: {label(box)} is outside its maintenance window {box.get('maintenance_window')} ({box.get('timezone')}); use --now")
    if not a.now and (why := frozen(box)):
        sys.exit(f"fleet: {label(box)} is {why}; use --now")
    if not ROLLBACK_SCRIPT.exists():
        sys.exit(f"fleet: {ROLLBACK_SCRIPT} missing")
    scp_to(box, ROLLBACK_SCRIPT, "/root/bench-rollback.sh")
    env = []
    if a.release:
        read_manifest(a.release)
        scp_to(box, manifest_path(a.release), "/root/bench-rollback.manifest", "0644")
        env.append("--setenv=MANIFEST=/root/bench-rollback.manifest")
    if a.no_restore:
        env.append("--setenv=RESTORE=0")
    if a.dry_run:
        rc, out = run_on(box, f"DRY_RUN=1 {' '.join(e.replace('--setenv=', '') for e in env)} /bin/bash /root/bench-rollback.sh", timeout=120)
        print(out[-1500:])
        return 0 if "ROLLBACK-PLAN-OK" in out else 1
    print(f"== {label(box)} {box['host']}: rollback {'to release ' + a.release if a.release else 'of the last upgrade'}{' (code only)' if a.no_restore else ''}")
    unit = f"bench-rollback-{int(time.time())}"
    log, start = box_log(box, "bench-rollback")
    rc, out = run_on(box, f"systemd-run --unit={unit} --collect --setenv=HOME=/root {' '.join(env)} /bin/bash /root/bench-rollback.sh")
    if rc != 0:
        print(f"   could not start the rollback unit: {out}")
        return 1
    print(f"   started {unit}; log {log}")
    deadline, last, ok = time.time() + 3600, "", None
    while time.time() < deadline:
        time.sleep(20)
        rc, out = run_on(box, f"tail -n +{start + 1} {log} 2>/dev/null | tail -n 3")
        tail = out.splitlines()[-1] if out else ""
        if tail != last:
            print(f"   {tail}")
            last = tail
        if any(l.startswith(("ROLLBACK-OK", "ROLLBACK-FAILED")) for l in out.splitlines()):
            ok = any(l.startswith("ROLLBACK-OK") for l in out.splitlines())
            break
    if ok is None:
        print("   timed out after 60 minutes")
        return 1
    log_action(f"rollback {box['id']} release={a.release or 'last-upgrade'} restore={not a.no_restore} result={'ok' if ok else 'FAILED'}")
    if ok:
        r = check_box(box, inv, load_catalog())
        write_check(r)
        print(f"   post-rollback check {r['status']}: " + ("; ".join(x["message"] for x in r["findings"] if x["level"] != "INFO") or "clean"))
        push(inv, quiet=True)
    return 0 if ok else 1


def cmd_backup_pull(a, inv):
    """Mirror /library/backups/fleet from the box that runs fleet-backup-mail onto this hub,
    so the fleet's backups also sit on hardware we own. Keeps what the source keeps."""
    _set_backup_box(inv)
    if not _BACKUP_BOX:
        sys.exit("fleet: set backup_mail_box in the inventory to the box running fleet-backup-mail (not this machine)")
    dest = Path(a.dest or str(HUB_BACKUPS))
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    src = f"{_BACKUP_BOX['ssh_user']}@{_BACKUP_BOX['reach']['ssh']}:{HUB_BACKUPS}/"
    rc, err = rsync(src, str(dest) + "/", delete=True)
    print(err[-300:] if rc else f"backup copies mirrored from {_BACKUP_BOX['id']} to {dest}")
    log_action(f"backup-pull from={_BACKUP_BOX['id']} rc={rc}")
    if rc == 0:
        hub = next((b for b in inv["boxes"] if b.get("local")), None)
        print(f"Check: ls {dest}" + (f"; fleet check {hub['id']}  (its 'bak hub' age reads this mirror)" if hub else ""))
    return rc


def cmd_reboot(a, inv):
    """Reboot a box inside its maintenance window (or --now), then wait for it and re-check.
    Tills: the POS keeps selling offline in the browser during the two minutes a reboot takes.
    --all: every remote pull-mode box in canary order, stopping at the first failure."""
    catalog = load_catalog()
    if getattr(a, "all", False):
        rc = 0
        for b in select_boxes(inv, [], True):
            if b.get("local") or b["mode"] == "push" or b["status"] in ("suspended", "offboarded"):
                continue
            sub = argparse.Namespace(id=b["id"], now=a.now, all=False)
            rc = cmd_reboot(sub, inv)
            if rc:
                print(f"stopping after {b['id']}")
                return rc
        return rc
    box = select_boxes(inv, [a.id], False)[0]
    if box["mode"] == "push":
        sys.exit("fleet: offline box; reboot it on site")
    if not a.now and not in_window(box):
        sys.exit(f"fleet: outside the maintenance window {box.get('maintenance_window')} ({box.get('timezone')}); use --now")
    if not a.now and (why := frozen(box)):
        sys.exit(f"fleet: {label(box)} is {why}; use --now")
    print(f"== {box['id']}: rebooting")
    log_action(f"reboot {box['id']} now={a.now}")
    run_on(box, "systemd-run --on-active=2 --collect /bin/systemctl reboot", timeout=30)
    time.sleep(30)
    for i in range(40):
        code = http_code(f"{box['reach']['http']}/erp/api/method/ping", timeout=10)
        if code == "200":
            print(f"   back after ~{30 + i * 10}s")
            break
        time.sleep(10)
    else:
        print("   no answer after 7 minutes")
        return 1
    r = check_box(box, inv, catalog)
    write_check(r)
    print(f"   post-reboot check {r['status']}: " + ("; ".join(x["message"] for x in r["findings"] if x["level"] != "INFO") or "clean"))
    push(inv, quiet=True)
    return 0 if r["status"] not in ("CRIT", "STALE") else 1


def cmd_runrole(a, inv):
    """IIAB roles run on the box itself (`runrole` reads the box's /etc/iiab/local_vars.yml);
    the hub only starts the run and follows its log."""
    box = select_boxes(inv, [a.id], False)[0]
    if box["mode"] == "push":
        sys.exit("fleet: offline box; run the role on the box itself")
    if not re.fullmatch(r"[a-z0-9_-]+", a.role):
        sys.exit("fleet: role name must be a plain role directory name")
    log = f"/opt/iiab/iiab/fleet-runrole-{a.role}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    if a.tags:
        cmd = (f"cd /opt/iiab/iiab && ANSIBLE_LOG_PATH={log} ansible-playbook -i ansible_hosts run-one-role.yml --connection=local "
               f"--extra-vars '{{\"role_to_run\":\"{a.role}\"}}' --tags {shlex.quote(a.tags)}")
    else:
        cmd = f"/opt/iiab/iiab/runrole {a.role} {log}"
    unit = f"fleet-runrole-{int(time.time())}"
    rc, out = run_on(box, f"systemd-run --unit={unit} --collect --setenv=HOME=/root /bin/bash -lc {shlex.quote(cmd)}")
    if rc != 0:
        print(out)
        return 1
    print(f"== {box['id']}: role {a.role}{' tags ' + a.tags if a.tags else ''} started ({unit}); log {log}")
    log_action(f"runrole {box['id']} role={a.role} tags={a.tags or '-'} log={log}")
    seen = 0
    for _ in range(360):                       # follow for up to 2 h
        time.sleep(20)
        rc, out = run_on(box, f"wc -l < {log} 2>/dev/null; systemctl is-active {unit} 2>/dev/null")
        parts = out.split()
        n = int(parts[0]) if parts and parts[0].isdigit() else 0
        active = parts[-1] if parts else "unknown"
        if n > seen:
            rc, chunk = run_on(box, f"sed -n '{seen + 1},{n}p' {log}")
            print(chunk)
            seen = n
        if active not in ("active", "activating"):
            rc, tail = run_on(box, f"tail -n 25 {log}")
            print(tail)
            ok = "failed=0" in tail and "unreachable=0" in tail
            print(f"RUNROLE-{'OK' if ok else 'FAILED'} {box['id']} {a.role}")
            return 0 if ok else 1
    print("still running after 2 h; follow the log on the box")
    return 1
