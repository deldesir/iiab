"""The lease: one lifecycle for every box. The hub signs a small dated document per box; the
box's agent verifies it daily and applies the ladder itself (notice, then the desk and the
storefront paused with the till still selling, then maintenance mode after the grace). The
hub only issues, renews and revokes. Signing and verification go through the openssl binary
(Ed25519), so the agent on a box needs nothing beyond the standard library."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .config import label, log_action, select_boxes
from .state import latest_checks
from .transport import run_on, scp_to

LEASE_KEY = Path(os.environ.get("FLEET_LEASE_KEY", "/etc/iiab/fleet-lease.key"))
LOCAL_ETC = Path(os.environ.get("FLEET_LEASE_LOCAL_ETC", "/etc/iiab"))     # where a lease lands on the hub itself
BOX_LEASE, BOX_PUB = "/etc/iiab/fleet-lease.json", "/etc/iiab/fleet-lease.pub"
NOTICE_DAYS = 14
DAYS = {"offline": (90, 30)}           # connectivity -> (lease days, grace days); anything else: 30 and the box's grace_days
GATE = {"unpaid", "offsite"}           # CRIT findings in the last check that refuse a renewal (without --anyway)


def pub_path() -> Path:
    return LEASE_KEY.with_suffix(".pub")


def canonical(lease: dict) -> bytes:
    return json.dumps(lease, sort_keys=True, separators=(",", ":")).encode()


def sign(lease: dict) -> dict:
    if not LEASE_KEY.exists():
        sys.exit(f"fleet: no signing key at {LEASE_KEY}: run `fleet lease keygen` once on the hub")
    with tempfile.TemporaryDirectory() as d:
        Path(d, "p").write_bytes(canonical(lease))
        r = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(LEASE_KEY), "-rawin", "-in", f"{d}/p"], capture_output=True)
    if r.returncode:
        sys.exit(f"fleet: signing failed: {r.stderr.decode(errors='replace')[-200:]}")
    return {"lease": lease, "sig": base64.b64encode(r.stdout).decode()}


def verify(doc: dict, pub: Path) -> bool:
    try:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p").write_bytes(canonical(doc["lease"]))
            Path(d, "s").write_bytes(base64.b64decode(doc["sig"]))
            return subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin", "-in", f"{d}/p", "-sigfile", f"{d}/s"],
                                  capture_output=True).returncode == 0
    except (KeyError, TypeError, ValueError, OSError):
        return False


def lease_state(lease: dict, today: dt.date) -> tuple[str, int]:
    """valid > notice (last fortnight) > restricted (expired, inside the grace) > readonly; and the days left."""
    expires = dt.date.fromisoformat(lease["expires"])
    left = (expires - today).days
    if left > NOTICE_DAYS:
        return "valid", left
    if left >= 0:
        return "notice", left
    if today <= expires + dt.timedelta(days=int(lease.get("grace_days") or 0)):
        return "restricted", left
    return "readonly", left


def today_for(box: dict) -> dt.date:
    return dt.datetime.now(ZoneInfo(box.get("timezone") or "UTC")).date()


def stored_path(box_id: str) -> Path:
    return config.STATE / "leases" / f"{box_id}.json"


def stored(box_id: str) -> dict | None:
    p = stored_path(box_id)
    return json.loads(p.read_text()) if p.exists() else None


def handover_line(doc: dict) -> str:
    """The lease as one line to paste or show as a QR: what reaches an offline box by hand."""
    return base64.b64encode(canonical(doc["lease"]) + b"." + doc["sig"].encode()).decode()


def deliver(box: dict, doc: dict) -> str:
    p = stored_path(box["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1))
    if box["mode"] == "push":
        return f"offline box: hand the lease over on site (door two or three)\n  file: {p}\n  paste line: {handover_line(doc)}"
    if box.get("local"):
        LOCAL_ETC.mkdir(parents=True, exist_ok=True)
        (LOCAL_ETC / "fleet-lease.json").write_text(json.dumps(doc, indent=1))
        (LOCAL_ETC / "fleet-lease.pub").write_bytes(pub_path().read_bytes())
        return "installed on this hub"
    scp_to(box, p, BOX_LEASE, "0644")
    scp_to(box, pub_path(), BOX_PUB, "0644")
    rc, out = run_on(box, "test -x /usr/local/sbin/fleet-agent && /usr/local/sbin/fleet-agent lease || echo 'no agent on this box: fleet lease deploy'", timeout=120)
    return out.strip()[-300:] or f"rc={rc}"


def gate(box: dict, inv: dict) -> list[str]:
    last = latest_checks(inv).get(box["id"]) or {}
    return [f["message"] for f in last.get("findings", []) if f["level"] == "CRIT" and f["code"] in GATE]


def issue(box: dict, inv: dict, days: int | None, grace: int | None, ladder: bool | None, anyway: bool, expires: dt.date | None = None) -> dict:
    blocked = [] if anyway else gate(box, inv)
    if blocked:
        sys.exit(f"fleet: {label(box)} does not meet its obligations: " + "; ".join(blocked) + "\n  (renew anyway with --anyway)")
    d_days, d_grace = DAYS.get(box["connectivity"], (30, int(box.get("grace_days") or 15)))
    today = today_for(box)
    lease = {"v": 1, "box": box["id"], "issued": today.isoformat(), "expires": (expires or today + dt.timedelta(days=days or d_days)).isoformat(),
             "grace_days": d_grace if grace is None else grace, "ladder": (box["owner"] != "self") if ladder is None else ladder}
    return sign(lease)


def cmd_lease(a, inv):
    if a.action == "keygen":
        if LEASE_KEY.exists():
            sys.exit(f"fleet: {LEASE_KEY} exists; a new key invalidates every lease out there, so this refuses to replace it")
        LEASE_KEY.parent.mkdir(parents=True, exist_ok=True)
        for argv in (["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(LEASE_KEY)], ["openssl", "pkey", "-in", str(LEASE_KEY), "-pubout", "-out", str(pub_path())]):
            r = subprocess.run(argv, capture_output=True)
            if r.returncode:
                sys.exit(f"fleet: {argv[1]} failed: {r.stderr.decode(errors='replace')[-200:]}")
        os.chmod(LEASE_KEY, 0o600)
        print(f"signing key {LEASE_KEY} (0600; back it up with the control plane) and {pub_path()} (goes to every box)")
        return 0
    if a.action == "status":
        print(f"{'id':<8} {'alias':<17} {'issued':<11} {'expires':<11} {'grace':<6} {'ladder':<7} state")
        for b in sorted(inv["boxes"], key=lambda b: (b["canary_rank"], b["id"])):
            doc = stored(b["id"])
            if not doc:
                print(f"{b['id']:<8} {b['alias']:<17} {'-':<11} {'-':<11} {'-':<6} {'-':<7} no lease issued")
                continue
            le = doc["lease"]
            st, left = lease_state(le, today_for(b))
            print(f"{b['id']:<8} {b['alias']:<17} {le['issued']:<11} {le['expires']:<11} {le['grace_days']:<6} {str(le['ladder']).lower():<7} {st} ({left:+d} d)")
        return 0
    box = select_boxes(inv, [a.id], False)[0]
    if a.action == "show":
        doc = stored(box["id"])
        if not doc:
            sys.exit(f"fleet: no lease issued to {label(box)}")
        print(json.dumps(doc["lease"], indent=1))
        print(f"state today: {lease_state(doc['lease'], today_for(box))[0]}\npaste line: {handover_line(doc)}")
        return 0
    if a.action == "issue":
        doc = issue(box, inv, a.days, a.grace, None if a.ladder is None else a.ladder == "on", a.anyway)
        le = doc["lease"]
        log_action(f"lease issue {box['id']} expires={le['expires']} grace={le['grace_days']} ladder={le['ladder']} anyway={a.anyway}")
        print(f"lease for {label(box)}: {le['issued']} to {le['expires']}, grace {le['grace_days']} d, ladder {'on' if le['ladder'] else 'off'}")
        print(deliver(box, doc))
        return 0
    if a.action == "revoke":
        grace = int(DAYS.get(box["connectivity"], (0, int(box.get("grace_days") or 15)))[1])
        doc = issue(box, inv, None, grace, None, True, expires=today_for(box) - dt.timedelta(days=grace + 1))
        log_action(f"lease revoke {box['id']} reason={a.reason!r}")
        print(f"lease for {label(box)} revoked (expired, past its grace: the ladder's last step applies at once). Reason: {a.reason or '-'}")
        print(deliver(box, doc))
        print(f"Undo: fleet lease issue {box['id']} --anyway")
        return 0
    if a.action == "deploy":
        if box["mode"] == "push":
            sys.exit("fleet: offline box: install the agent on site (fleet deploy-agent over the LAN) and hand the lease over")
        if not pub_path().exists():
            sys.exit("fleet: no key yet: fleet lease keygen")
        units = config.SCRIPTS / "fleet-units"
        scp_to(box, config.SCRIPTS / "fleet-agent", "/usr/local/sbin/fleet-agent")
        scp_to(box, config.SCRIPTS / "fleet-probe", "/usr/local/sbin/fleet-probe")
        if not (box.get("local") and str(pub_path()) == BOX_PUB):   # the hub itself: the key already sits where the agent reads it
            scp_to(box, pub_path(), BOX_PUB, "0644")
        for u in ("fleet-lease.service", "fleet-lease.timer"):
            scp_to(box, units / u, f"/etc/systemd/system/{u}", "0644")
        rc, out = run_on(box, "mkdir -p /var/lib/fleet && systemctl daemon-reload && systemctl enable --now fleet-lease.timer && /usr/local/sbin/fleet-agent lease", timeout=120)
        log_action(f"lease deploy {box['id']} rc={rc}")
        print(out.strip()[-400:])
        if rc == 0:
            print(f"Check: fleet lease status; the box re-reads its lease daily (fleet-lease.timer). Next: fleet lease issue {box['id']}")
        return 0 if rc == 0 else 1
    sys.exit(f"fleet: unknown lease action {a.action}")
