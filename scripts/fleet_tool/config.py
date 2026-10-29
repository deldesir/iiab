"""Paths, the inventory and the catalog, the lock, selection and labels, colours, the action log."""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("fleet: python3-yaml is required")

INVENTORY = Path(os.environ.get("FLEET_INVENTORY", "/etc/iiab/fleet.yml"))


RELEASES = Path(os.environ.get("FLEET_RELEASES", "/etc/iiab/fleet-releases"))


STATE = Path(os.environ.get("FLEET_STATE", "/var/lib/fleet"))


PUSH_CONF = Path(os.environ.get("FLEET_PUSH_CONF", "/etc/iiab/fleet.conf"))


_here = Path(__file__).resolve().parent.parent   # scripts/ in the repository; /usr/local/lib for an installed copy (no scripts there: the repository path is used)


CATALOG = Path(os.environ.get("FLEET_CATALOG") or (
    "/etc/iiab/fleet-catalog.yml" if Path("/etc/iiab/fleet-catalog.yml").exists()
    else _here / "fleet-catalog.yml" if (_here / "fleet-catalog.yml").exists() else "/opt/iiab/iiab/scripts/fleet-catalog.yml"))


SCRIPTS = _here if (_here / "bench-upgrade.sh").exists() else Path("/opt/iiab/iiab/scripts")


UPGRADE_SCRIPT = Path(os.environ.get("FLEET_UPGRADE_SCRIPT") or SCRIPTS / "bench-upgrade.sh")


ROLLBACK_SCRIPT = Path(os.environ.get("FLEET_ROLLBACK_SCRIPT") or SCRIPTS / "bench-rollback.sh")


TARGETS = ["frappe-bench-frappe-schedule", "frappe-bench-web.target", "frappe-bench-workers.target"]


KEEP_CHECKS = 90


HUB_BACKUPS = Path(os.environ.get("FLEET_HUB_BACKUPS", "/library/backups/fleet"))

# where `fleet offboard` keeps a box's final backup (FLEET_OFFBOARD_DIR for the tests)
OFFBOARD_DIR = Path(os.environ.get("FLEET_OFFBOARD_DIR", "/library/backups/offboard"))


LEVELS = {"CRIT": 3, "STALE": 2, "WARN": 1, "INFO": 0}


ID_RE = re.compile(r"^box-\d{3}$")               # box-001 … box-999: given once, never reused


ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")   # short slug people type; may change


def parse_conf(text: str) -> dict:
    conf = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            conf[k.strip()] = v.strip().strip('"')
    return conf


def read_conf(p: Path) -> dict:
    return parse_conf(p.read_text()) if p.exists() else {}


def write_private(p: Path, text: str) -> None:
    """Write a file that holds a secret: created 0600, never world-readable for a moment."""
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(p, 0o600)


def set_conf(p: Path, key: str, value: str) -> None:
    lines = p.read_text().splitlines() if p.exists() else []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    write_private(p, "\n".join(lines) + "\n")


def load_catalog() -> dict:
    if not Path(CATALOG).exists():
        return {"services": {}}
    return yaml.safe_load(Path(CATALOG).read_text()) or {"services": {}}


def load_inventory() -> dict:
    if not INVENTORY.exists():
        sys.exit(f"fleet: inventory {INVENTORY} missing")
    inv = yaml.safe_load(INVENTORY.read_text()) or {}
    inv.setdefault("defaults", {})
    inv.setdefault("boxes", [])
    for b in inv["boxes"]:
        for k, v in inv["defaults"].items():
            b.setdefault(k, v)
        b.setdefault("local", False)
        b.setdefault("status", "active")
        b.setdefault("canary_rank", 99)
        b.setdefault("connectivity", "public")
        b.setdefault("services", [])
        b.setdefault("alias", b["id"])
        reach = dict(b.get("reach") or {})
        reach.setdefault("ssh", b["host"])
        reach.setdefault("http", f"https://{b['host']}" if b["connectivity"] == "public" else f"http://{reach['ssh']}")
        b["reach"] = reach
        b["mode"] = "push" if b["connectivity"] == "offline" else "pull"
    return inv


def save_inventory(inv: dict) -> None:
    """Write the inventory back without the derived/defaulted keys. Works on a deep copy so the
    records in memory keep their resolved form for the rest of the run."""
    import copy
    text = INVENTORY.read_text()
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    body = copy.deepcopy(inv)
    for b in body["boxes"]:
        b.pop("mode", None)
        if b.get("reach") == {"ssh": b["host"], "http": f"https://{b['host']}" if b.get("connectivity", "public") == "public" else f"http://{b['host']}"}:
            b.pop("reach", None)
        for k in list(b):
            if k in inv["defaults"] and b[k] == inv["defaults"][k] and k not in ("plan", "status"):
                del b[k]
        if b.get("local") is False:
            del b["local"]
        if b.get("alias") == b["id"]:
            del b["alias"]
    tmp = INVENTORY.with_suffix(".tmp")
    tmp.write_text(header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100))
    os.chmod(tmp, 0o640)
    tmp.replace(INVENTORY)


def require_targets(a) -> None:
    """A command that changes boxes names them or says --all; nothing mutating runs fleet-wide by omission."""
    if not a.ids and not a.all:
        sys.exit("fleet: name the box(es) or pass --all (this command changes boxes)")


MUTATING = {"check", "dunning", "lease", "upgrade", "rollback", "suspend", "resume", "offboard", "onboard", "rename", "hostname",
            "authorize-key", "manifest", "deploy-agent", "deploy-watchdog", "agent-token", "reboot", "push", "vars"}


_LOCKS: dict = {}   # lock handle per lock file, kept open for the process's life


def acquire_lock():
    """One fleet command that writes at a time. Blocks (with a note) until the other one is done.
    Taken once per process: a second request for the same lock returns the handle already held
    (two flocks on one file would block each other even inside one process)."""
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / ".lock"
    if path in _LOCKS:
        return _LOCKS[path]
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("waiting: another fleet command holds the inventory", file=sys.stderr)
        fcntl.flock(fh, fcntl.LOCK_EX)
    _LOCKS[path] = fh
    return fh


def select_boxes(inv: dict, ids: list[str], all_: bool) -> list[dict]:
    boxes = sorted(inv["boxes"], key=lambda b: (b["canary_rank"], b["id"]))
    if all_ or not ids:
        return boxes
    by_id = {b["id"]: b for b in boxes}
    by_alias = {b["alias"]: b for b in boxes}
    by_host = {b["host"]: b for b in boxes}
    out = []
    for i in ids:
        b = by_id.get(i) or by_alias.get(i) or by_host.get(i)
        if not b:
            sys.exit(f"fleet: unknown box {i!r} (known: {', '.join(label(b) for b in boxes)})")
        out.append(b)
    return out


def label(b: dict) -> str:
    """How a box is named in output: 'box-003 malianova' (id, then alias when it differs)."""
    return f"{b['id']} {b['alias']}" if b.get("alias") and b["alias"] != b["id"] else b["id"]


_COLORS = {"green": "32", "yellow": "33", "red": "31", "magenta": "35", "bold": "1", "dim": "2"}


_STATE_COLOR = {"OK": "green", "WARN": "yellow", "CRIT": "red", "STALE": "magenta", "FAILED": "red", "INFO": "dim"}


def paint(text: str, color: str) -> str:
    """Colour on a terminal only: logs, Semaphore runs and mail get plain text (NO_COLOR respected)."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{_COLORS[color]}m{text}\033[0m"


def state(word: str, width: int = 0) -> str:
    """A check state (OK / WARN / CRIT / STALE) coloured, padded to width before colouring."""
    return paint(f"{word:<{width}}", _STATE_COLOR.get(word, "bold"))


def in_window(box: dict) -> bool:
    m = re.fullmatch(r"(\d\d):(\d\d)-(\d\d):(\d\d)", box.get("maintenance_window") or "")
    if not m:
        return True
    now = dt.datetime.now(ZoneInfo(box.get("timezone") or "UTC")).time()
    start, end = dt.time(int(m[1]), int(m[2])), dt.time(int(m[3]), int(m[4]))
    return start <= now <= end if start <= end else (now >= start or now <= end)


def log_action(text: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "actions.log", "a") as fh:
        fh.write(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} {text}\n")
