"""The check: raw metrics of a box, their evaluation into levelled findings, the digest, and the read-only commands (check, digest, status, list, tailnet)."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CATALOG, HUB_BACKUPS, INVENTORY, LEVELS, SCRIPTS, STATE, TARGETS, label, load_catalog, paint, parse_conf, select_boxes, state
from .releases import heads_of, manifest_apps, read_manifest
from .site import kuma_heartbeats, latest_report, load_push_conf, push, site_get
from .state import latest_checks, write_check
from .transport import box_http_code, http_code, local_addresses, rdap_fetch, run_local, run_on, scp_to, tailscale_status, tls_days_left


def domain_info(host: str) -> dict:
    """Registrar and expiry via RDAP, cached a day (failures cache too)."""
    cache = STATE / "rdap"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{host}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    tld = host.rsplit(".", 1)[-1]
    out = {"registrar": None, "expiry": None, "days_left": None}
    for url in (f"https://rdap.org/domain/{host}", f"https://rdap.nic.{tld}/domain/{host}"):
        j = rdap_fetch(url)
        if j is None:   # one registry server failing is normal; the next one is tried
            continue
        exp = next((e.get("eventDate") for e in j.get("events", []) if e.get("eventAction") == "expiration"), None)
        reg = None
        for ent in j.get("entities", []):
            if "registrar" in (ent.get("roles") or []):
                reg = next((v[3] for v in (ent.get("vcardArray") or [None, []])[1] if v and v[0] == "fn"), None)
                break
        if exp:
            d = dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
            out = {"registrar": reg, "expiry": d.date().isoformat(), "days_left": (d - dt.datetime.now(dt.timezone.utc)).days}
            break
    f.write_text(json.dumps(out))
    return out


_BACKUP_BOX: dict | None = None   # the box running fleet-backup-mail when it is not this machine


def hub_backup_age_hours(host: str) -> float | None:
    d = HUB_BACKUPS / host
    if not d.is_dir() and _BACKUP_BOX:
        rc, out = run_on(_BACKUP_BOX, f"f=$(ls -t {HUB_BACKUPS}/{host}/*/*-database*.sql.gz {HUB_BACKUPS}/{host}/*/control-plane-export-*.tar.gpg 2>/dev/null | head -1); [ -n \"$f\" ] && echo $(( ($(date +%s) - $(stat -c %Y \"$f\")) / 3600 )) || echo none")
        return float(out.strip()) if rc == 0 and out.strip().replace(".", "").isdigit() else None
    if not d.is_dir():
        return None
    newest = None
    for sub in d.iterdir():
        if sub.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sub.name):
            for f in [*sub.glob("*-database*.sql.gz"), *sub.glob("control-plane-export-*.tar.gpg")]:
                m = f.stat().st_mtime
                newest = m if newest is None or m > newest else newest
    return None if newest is None else round((time.time() - newest) / 3600, 1)


REMOTE_PROBE = r"""
B={bench}; S={site}
db=$(python3 -c "import json;print(json.load(open('$B/sites/$S/site_config.json'))['db_name'])" 2>/dev/null)
echo "targets=$(systemctl is-active {targets} 2>/dev/null | tr '\n' ' ')"
echo "failed_units=$(systemctl --failed --no-legend | wc -l)"
echo "failed_list=$(systemctl --failed --no-legend --plain | awk '{{print $1}}' | tr '\n' ',')"
LOGDB=$B/sites/$S/private/files/sqlite.db   # Frappe keeps Error Log in a per-site SQLite file now; the MariaDB table is a leftover on older sites
echo "errorlog=$( ( [ -r "$LOGDB" ] && python3 -c "import sqlite3; c=sqlite3.connect('file:$LOGDB?mode=ro', uri=True); print(c.execute('select count(*) from \"tabError Log\" where creation > ?', ('{since}',)).fetchone()[0])" 2>/dev/null ) || mysql -N -e "select count(*) from \`tabError Log\` where creation > '{since}'" $db 2>/dev/null || echo '?')"
echo "open_shifts=$(mysql -N -e "select count(*) from \`tabPOS Opening Shift\` where status='Open' and docstatus=1" $db 2>/dev/null || echo '?')"
echo "hotel_business_date=$(mysql -N -e "select max(business_date) from \`tabProperty\` where is_active=1" $db 2>/dev/null)"   # empty where there is no PMS
echo "disk_free_gb=$(df -BG --output=avail / | awk 'NR==2{{print $1+0}}')"
echo "ram_avail_mb=$(awk '/MemAvailable/{{print int($2/1024)}}' /proc/meminfo)"
echo "load1=$(cut -d' ' -f1 /proc/loadavg)"
echo "box_backup_age_h=$(f=$(ls -t $B/sites/$S/private/backups/*-database*.sql.gz 2>/dev/null | head -1); [ -n "$f" ] && echo $(( ($(date +%s) - $(stat -c %Y "$f")) / 3600 )) || echo '?')"
echo "maintenance=$(python3 -c "import json;print(json.load(open('$B/sites/$S/site_config.json')).get('maintenance_mode',0))" 2>/dev/null)"
echo "uptime_s=$(cut -d. -f1 /proc/uptime)"
echo "reboot_required=$([ -f /var/run/reboot-required ] && echo 1 || echo 0)"
echo "kernel_running=$(uname -r)"
echo "kernel_newest=$(ls /boot/vmlinuz-* 2>/dev/null | sed 's/.*vmlinuz-//' | sort -V | tail -1)"
echo "units={units_out}"
"""


def collect_pull(box: dict, catalog: dict) -> dict:
    """Raw metrics of a box the hub can reach. Severity is decided in evaluate()."""
    tz = ZoneInfo(box.get("timezone") or "UTC")
    since = dt.datetime.now(tz).strftime("%Y-%m-%d 00:00:00")
    raw: dict = {"heads": heads_of(box)}
    if not raw["heads"]:
        raw["unreachable"] = True
        return raw
    if box["role"] == "hub" and catalog.get("_watchdog_box"):
        raw["_inv_watchdog_box"] = catalog["_watchdog_box"]
    units = list(box.get("monitor_units") or [])
    for sid in box["services"]:
        svc = catalog["services"].get(sid) or {}
        if svc.get("kind") == "box":
            units += svc.get("units") or []
        elif svc.get("kind") == "hub" and box["role"] in ("hub", "observer"):
            units += svc.get("units") or []     # hub-kind services run on the hub/observer; other boxes only receive them
    units = sorted(set(units))
    units_out = ("$(systemctl is-active " + " ".join(shlex.quote(u) for u in units) + " 2>/dev/null | tr '\\n' ',')") if units else ""
    probe = REMOTE_PROBE.format(bench=box["bench"], site=box["site"], targets=" ".join(TARGETS), since=since, units_out=units_out)
    rc, out = run_on(box, probe)
    kv = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        kv[k.strip()] = v.strip()
    raw.update({
        "targets": kv.get("targets", ""), "failed_units": kv.get("failed_units", "?"), "failed_list": kv.get("failed_list", ""),
        "errorlog_rows_today": kv.get("errorlog", "?"), "open_shifts": kv.get("open_shifts", "?"),
        "hotel_business_date": kv.get("hotel_business_date", "") or "",
        "disk_free_gb": kv.get("disk_free_gb", "?"), "ram_avail_mb": kv.get("ram_avail_mb", "?"), "load1": kv.get("load1", "?"),
        "box_backup_age_h": kv.get("box_backup_age_h", "?"), "maintenance_mode": kv.get("maintenance", "?"), "uptime_s": kv.get("uptime_s", "?"),
        "monitor_units": dict(zip(units, [s for s in kv.get("units", "").split(",") if s])) if units else {},
        "reboot_required": kv.get("reboot_required", "0"), "kernel_running": kv.get("kernel_running", ""), "kernel_newest": kv.get("kernel_newest", ""),
    })
    # the pipeline's off-box snapshot copy: second-brain-offsite.conf (OFFSITE_HOST, OFFSITE_DIR) lives on
    # the box that runs the assistant; the copy may sit on this machine or on another box
    if "whatsapp-assistant" in box["services"]:
        rc, out = run_on(box, "cat /etc/iiab/second-brain-offsite.conf 2>/dev/null")
        oc = parse_conf(out) if rc == 0 else {}
        if oc.get("OFFSITE_HOST"):
            # values read from a box's file: validated and quoted before they become a command elsewhere
            host, odir = oc["OFFSITE_HOST"], oc.get("OFFSITE_DIR", "/library/backups/hub-offsite")
            if not re.fullmatch(r"[A-Za-z0-9.:-]+", host) or not re.fullmatch(r"/[\w./-]+", odir):
                raw["offsite_copy_age_h"] = "invalid offsite configuration"
                raw["offsite_host"] = host[:40]
            else:
                target = {"ssh_user": "root", "reach": {"ssh": host}, "local": host in local_addresses()}
                rc, out = run_on(target,
                                 f"f=$(ls -t {shlex.quote(odir)}/second-brain-*.tar.gpg 2>/dev/null | head -1); [ -n \"$f\" ] && echo $(( ($(date +%s) - $(stat -c %Y \"$f\")) / 3600 )) || echo none")
                raw["offsite_copy_age_h"] = out.strip() if rc == 0 else "unreachable"
                raw["offsite_host"] = host
    # HTTP probes from the catalog
    base = box["reach"]["http"].rstrip("/")
    probes = []
    for sid in box["services"]:
        svc = catalog["services"].get(sid) or {}
        if svc.get("kind") == "box":
            probes += [dict(p, service=sid) for p in (svc.get("probes") or [])]
        if svc.get("kind") == "hub":
            probes += [dict(p, service=sid, hub=True) for p in (svc.get("hub_probes") or [])]
    seen, raw["http"], raw["probes"] = set(), {}, []
    for p in probes:
        url = p.get("url") or f"{base}{p['path']}"
        if url in seen:
            continue
        seen.add(url)
        code = box_http_code(box, url) if p.get("hub") else http_code(url)
        raw["http"][p.get("path") or url] = code
        raw["probes"].append({"url": url, "code": code, "expect": str(p.get("expect", 200)), "critical": bool(p.get("critical", True)), "service": p["service"]})
    if box["connectivity"] == "public":
        raw["tls_days_left"] = tls_days_left(box["host"])
        dom = domain_info(box["host"])
        raw["domain_registrar"], raw["domain_expiry"], raw["domain_days_left"] = dom["registrar"], dom["expiry"], dom["days_left"]
    raw["hub_backup_age_h"] = hub_backup_age_hours(box["host"])
    if box["role"] == "hub" and raw.get("_inv_watchdog_box"):
        wd = raw.pop("_inv_watchdog_box")
        rc, out = run_on(wd, "tail -n 30 /var/lib/fleet/hub-watchdog.log 2>/dev/null")
        recent = [l for l in out.splitlines() if l[:19] >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")]
        raw["watchdog"] = {"box": wd["id"], "reachable": rc == 0, "starts_48h": [l for l in recent if "START sent" in l], "lines_48h": len(recent)}
    return raw


def evaluate(box: dict, raw: dict, inv: dict, catalog: dict) -> dict:
    """Turn raw metrics into levelled findings and a status. Shared by pull and push modes."""
    F: list[dict] = []

    def f(level, code, msg):
        F.append({"level": level, "code": code, "message": msg})

    res = {"id": box["id"], "host": box["host"], "role": box["role"], "connectivity": box["connectivity"], "mode": box["mode"],
           "status_inventory": box["status"], "services": box["services"],
           "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "release_current": inv.get("current_release") or ""}
    res.update({k: v for k, v in raw.items() if k != "findings"})
    suspended = box["status"] == "suspended"
    if raw.get("unreachable"):
        f("CRIT", "unreachable", "unreachable: could not read app heads over ssh")
    if raw.get("stale_h") is not None:
        sla = float(box.get("heartbeat_sla_hours") or 30)
        if raw["stale_h"] >= 2 * sla:
            f("CRIT", "no-report", f"no report for {raw['stale_h']:.0f} h (allowance {sla:.0f} h)")
        elif raw["stale_h"] >= sla:
            f("STALE", "stale", f"last report {raw['stale_h']:.0f} h ago (allowance {sla:.0f} h)")
    if raw.get("report_error"):
        f("STALE", "report-unavailable", raw["report_error"])
    for t, s in zip(TARGETS, (raw.get("targets") or "").split()):
        if s != "active":
            f("CRIT", "target", f"{t} is {s}")
    fu = raw.get("failed_units", "?")
    if fu not in ("0", "?", ""):
        f("WARN", "failed-units", f"{fu} failed systemd unit(s): {raw.get('failed_list', '').strip(',')}")
    disk = raw.get("disk_free_gb", "?")
    if str(disk).isdigit():
        if int(disk) < 5:
            f("CRIT", "disk", f"disk free {disk} GB")
        elif int(disk) < 15:
            f("WARN", "disk", f"disk free {disk} GB")
    for u, st in (raw.get("monitor_units") or {}).items():
        if st != "active":
            f("CRIT", "unit", f"{u} is {st}")
    if "hospitality" in (box.get("services") or []):
        # the PMS's business date advances only when the night audit runs: a date that lags
        # means no room charges are being posted and the front desk is working on yesterday
        bd = raw.get("hotel_business_date") or ""
        today = dt.datetime.now(ZoneInfo(box.get("timezone") or "UTC")).date()
        try:
            lag = (today - dt.date.fromisoformat(bd[:10])).days if bd and bd != "NULL" else None
        except ValueError:
            lag = None
        if lag is None:
            f("WARN", "night-audit", "no active property with a business date (PMS not set up?)")
        elif lag >= 7:
            f("CRIT", "night-audit", f"night audit not run: business date {bd[:10]} is {lag} days behind")
        elif lag >= 2:
            f("WARN", "night-audit", f"night audit not run: business date {bd[:10]} is {lag} days behind")
    for p in raw.get("probes") or []:
        if p["code"] == p["expect"]:
            continue
        if suspended and p["code"] == "503":
            f("INFO", "suspended", f"{p['url']} answers 503 (suspended)")
            continue
        f("CRIT" if p["critical"] else "WARN", "http", f"HTTP {p['code']} on {p['url']} ({p['service']})")
    if box["connectivity"] == "public" and "tls_days_left" in raw:
        d = raw.get("tls_days_left")
        if d is None:
            f("WARN", "tls", "TLS certificate unreadable")
        elif d < 7:
            f("CRIT", "tls", f"TLS certificate expires in {d} days")
        elif d < 14:
            f("WARN", "tls", f"TLS certificate expires in {d} days")
        dd = raw.get("domain_days_left")
        if dd is not None:
            if dd < 7:
                f("CRIT", "domain", f"domain registration expires in {dd} days")
            elif dd < 30:
                f("WARN", "domain", f"domain registration expires in {dd} days")
    age = raw.get("hub_backup_age_h") if box["mode"] == "pull" else None
    if box["mode"] == "pull":
        if age is None or age > 72:
            f("CRIT" if box["status"] in ("active", "grace") else "WARN", "backup", f"hub backup copy stale ({age} h)")
        elif age > 36:
            f("WARN", "backup", f"hub backup copy {age} h old")
    else:
        ba = raw.get("box_backup_age_h", "?")
        if str(ba).isdigit() and int(ba) > 72:
            f("CRIT", "backup", f"box backup {ba} h old")
        elif str(ba).isdigit() and int(ba) > 36:
            f("WARN", "backup", f"box backup {ba} h old")
    el = raw.get("errorlog_rows_today", "?")
    if str(el).isdigit() and int(el) > 50:
        f("WARN", "errorlog", f"{el} Error Log rows today")
    if str(raw.get("maintenance_mode")) == "1" and not suspended:
        f("WARN", "maintenance", "site is in maintenance mode but not suspended")
    up = raw.get("uptime_s", "?")
    if str(up).isdigit() and int(up) < 86400:
        f("INFO", "rebooted", f"booted {int(up) // 3600} h ago")
    kr, kn = raw.get("kernel_running") or "", raw.get("kernel_newest") or ""
    if raw.get("reboot_required") == "1" or (kr and kn and kr != kn):
        f("WARN", "reboot", f"reboot pending (running {kr or '?'}, installed {kn or '?'}); fleet reboot {box['id']} inside its window")
    oa = raw.get("offsite_copy_age_h")
    if oa is not None:
        if oa in ("none", "unreachable"):
            f("WARN", "offsite", f"pipeline off-box copy on {raw.get('offsite_host')}: {oa}")
        elif str(oa).isdigit() and int(oa) > 72:
            f("CRIT", "offsite", f"pipeline off-box copy is {oa} h old")
        elif str(oa).isdigit() and int(oa) > 36:
            f("WARN", "offsite", f"pipeline off-box copy is {oa} h old")
    wd = raw.get("watchdog")
    if wd:
        if not wd.get("reachable"):
            f("WARN", "watchdog", f"hub watchdog box {wd['box']} unreachable")
        for l in wd.get("starts_48h") or []:
            f("WARN", "watchdog", f"watchdog on {wd['box']} started the hub through Contabo: {l[:19]}")
    cur = inv.get("current_release") or ""
    if cur and raw.get("heads"):
        want = manifest_apps(read_manifest(cur))
        behind = [a for a in want if a in raw["heads"] and raw["heads"][a] != want[a]]
        res["behind_release"] = behind
        if behind:
            f("WARN", "release", f"behind {cur}: {', '.join(behind)}")
    worst = max((LEVELS[x["level"]] for x in F), default=-1)
    judge_receivables(box, res, catalog, f)
    res["status"] = {3: "CRIT", 2: "STALE", 1: "WARN"}.get(worst, "OK")
    res["findings"] = F
    res["problems"] = [x["message"] for x in F if x["level"] in ("CRIT", "STALE", "WARN")]
    return res


def judge_receivables(box: dict, res: dict, catalog: dict, f) -> None:
    """Overdue invoices in the fleet site: WARN while overdue, CRIT once past the box's grace."""
    r = (catalog.get("_receivables") or {}).get(box["id"])
    if not r or box["owner"] == "self":
        return
    res["outstanding"], res["overdue_days"], res["oldest_due"] = r.get("outstanding", 0), int(r.get("overdue_days") or 0), r.get("oldest_due")
    if res["overdue_days"] <= 0:
        return
    amount = f"{float(r.get('outstanding') or 0):,.0f} {r.get('currency') or ''}".strip()
    grace = int(box.get("grace_days") or 0)
    if res["overdue_days"] > grace:
        f("CRIT", "unpaid", f"unpaid {res['overdue_days']} d past due ({amount}, {r.get('invoices', 0)} invoice(s)); grace of {grace} d is over: fleet dunning --apply")
    else:
        f("WARN", "unpaid", f"invoice overdue {res['overdue_days']} d ({amount}); grace ends in {grace - res['overdue_days']} d")


def check_box(box: dict, inv: dict, catalog: dict) -> dict:
    if box["mode"] == "push":
        rep = latest_report(box)
        raw: dict = {}
        if rep is None:
            raw["report_error"] = "fleet site not configured on the hub (no /etc/iiab/fleet.conf)"
        elif rep.get("error"):
            raw["report_error"] = f"fleet site unreachable: {rep['error'][:80]}"
        elif not rep.get("checked_at"):
            raw["stale_h"] = 10 ** 6
        else:
            raw = dict(rep.get("raw") or {})
            when = dt.datetime.fromisoformat(str(rep["checked_at"]).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            raw["stale_h"] = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600
            raw["report_checked_at"] = rep["checked_at"]
        return evaluate(box, raw, inv, catalog)
    return evaluate(box, collect_pull(box, catalog), inv, catalog)


INSTALLED_BIN = Path("/usr/local/sbin")
INSTALLED_LIB = Path("/usr/local/lib")


def copy_drift(repo: Path, installed_bin: Path, installed_lib: Path) -> list[str]:
    """The files of the fleet tool whose installed copy differs from the repository's, is missing,
    or is left over (a module the repository no longer has): empty when the copies agree.
    `install-fleet.sh` writes the copies; a pull that touched the tool without that script
    afterwards is what this finds."""
    pairs = [("fleet", repo / "fleet", installed_bin / "fleet")]
    if (repo / "fleet-kuma-seed").exists():
        pairs.append(("fleet-kuma-seed", repo / "fleet-kuma-seed", installed_bin / "fleet-kuma-seed"))
    names = {p.name for p in (repo / "fleet_tool").glob("*.py")} | {p.name for p in (installed_lib / "fleet_tool").glob("*.py")}
    for n in sorted(names):
        pairs.append((f"fleet_tool/{n}", repo / "fleet_tool" / n, installed_lib / "fleet_tool" / n))
    return [name for name, a, b in pairs if not (a.exists() and b.exists() and a.read_bytes() == b.read_bytes())]


def installed_copy_drift() -> list[str]:
    """copy_drift for the hub: the installed copies against the repository at SCRIPTS. Nothing
    to report when the tool runs from the repository itself (a worktree, the tests) or when
    there is no repository beside the copies to compare with."""
    running = Path(__file__).resolve().parent.parent
    if running == SCRIPTS.resolve() or not (SCRIPTS / "fleet_tool" / "cli.py").exists():
        return []
    return copy_drift(SCRIPTS, INSTALLED_BIN, INSTALLED_LIB)


def fmt_days(v) -> str:
    return "?" if v is None else f"{v}d"


def fmt_age(h) -> str:
    if h in (None, "?", ""):
        return "?"
    h = float(h)
    return f"{h:.0f}h" if h < 48 else f"{h / 24:.1f}d"


def build_digest(inv: dict, checks: dict[str, dict], hub_notes: list[str] | None = None) -> str:
    """The digest text: the counts, the release, one line per box with its findings. hub_notes
    are lines about the hub itself (the tool's copies drifted), printed before the boxes."""
    tz = ZoneInfo(inv["defaults"].get("timezone") or "UTC")
    now = dt.datetime.now(tz).strftime("%a %d %b %H:%M")
    counts = {s: sum(1 for c in checks.values() if c.get("status") == s) for s in ("OK", "WARN", "STALE", "CRIT")}
    lines = [f"Fleet {now}: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items() if v) + f" of {len(inv['boxes'])}",
             f"Release: {inv.get('current_release') or 'none'}"]
    lines += [f"    · WARN: {n}" for n in (hub_notes or [])]
    for b in sorted(inv["boxes"], key=lambda b: (b["canary_rank"], b["id"])):
        c = checks.get(b["id"])
        if not c:
            lines.append(f"- ?? {label(b)}: never checked")
            continue
        mark = {"OK": "OK  ", "WARN": "WARN", "STALE": "STAL", "CRIT": "CRIT"}[c["status"]]
        bits = [f"disk {c.get('disk_free_gb', '?')}G", f"bak {fmt_age(c.get('hub_backup_age_h') if b['mode'] == 'pull' else c.get('box_backup_age_h'))}",
                f"err {c.get('errorlog_rows_today', '?')}"]
        if b["role"] == "till" and c.get("open_shifts") not in ("?", None):
            bits.append(f"shifts {c['open_shifts']}")
        if "hospitality" in (b.get("services") or []) and c.get("hotel_business_date"):
            bits.append(f"day {str(c['hotel_business_date'])[:10]}")
        if c.get("tls_days_left") is not None:
            bits.append(f"tls {c['tls_days_left']}d")
        if c.get("domain_days_left") is not None and c["domain_days_left"] < 90:
            bits.append(f"domain {c['domain_days_left']}d")
        if b["mode"] == "push":
            bits.append(f"reported {fmt_age(c.get('stale_h'))} ago")
        if b["status"] != "active":
            bits.append(b["status"].upper())
        lines.append(f"- {mark} {label(b)} ({b['business']}): " + ", ".join(bits))
        for x in c.get("findings", []):
            if x["level"] != "INFO" or x["code"] == "rebooted":
                lines.append(f"    · {x['level']}: {x['message']}")
    return "\n".join(lines)


def _set_backup_box(inv: dict) -> None:
    global _BACKUP_BOX
    bid = inv.get("backup_mail_box")
    _BACKUP_BOX = next((b for b in inv["boxes"] if b["id"] == bid and not b.get("local")), None) if bid else None


def cmd_check(a, inv):
    catalog = load_catalog()
    _set_backup_box(inv)
    catalog["_receivables"] = site_get("fleet_ops.api.receivables") or {}
    wd_id = inv.get("hub_watchdog_box")
    if wd_id:
        catalog["_watchdog_box"] = next((b for b in inv["boxes"] if b["id"] == wd_id), None)
    boxes = select_boxes(inv, a.ids, a.all)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(check_box, b, inv, catalog): b for b in boxes}
        for fu in cf.as_completed(futs):
            b = futs[fu]
            try:
                r = fu.result()
            except BaseException as e:   # SystemExit included: a worker must not take the whole check down
                r = evaluate(b, {"unreachable": True, "check_error": f"{e.__class__.__name__}: {str(e)[:160]}"}, inv, catalog)
                r["findings"].append({"level": "CRIT", "code": "exception", "message": f"check failed: {e.__class__.__name__}: {str(e)[:120]}"})
                r["status"] = "CRIT"
            results[r["id"]] = r
            write_check(r)
    for b in boxes:
        r = results[b["id"]]
        print(f"== {label(b)} {b['host']} [{b['connectivity']}/{b['mode']}] {state(r['status'])}")
        if r.get("heads"):
            print("   " + " ".join(f"{k}={v[:7]}" for k, v in sorted(r["heads"].items())))
        if r.get("http"):
            print("   " + " ".join(f"{p}={c}" for p, c in r["http"].items()))
        print(f"   disk {r.get('disk_free_gb', '?')}G ram {r.get('ram_avail_mb', '?')}M err {r.get('errorlog_rows_today', '?')} shifts {r.get('open_shifts', '?')}"
              f" bak hub {fmt_age(r.get('hub_backup_age_h'))} box {fmt_age(r.get('box_backup_age_h'))} tls {fmt_days(r.get('tls_days_left'))}"
              f" domain {fmt_days(r.get('domain_days_left'))} ({r.get('domain_registrar') or '?'})")
        for x in r.get("findings", []):
            print(f"   {state(x['level'], 5)} {x['message']}")
    hub_notes = []
    drift = installed_copy_drift()
    if drift:
        hub_notes.append("hub: the installed fleet tool differs from the repository (" + ", ".join(drift[:4])
                         + (", …" if len(drift) > 4 else "") + "): run scripts/fleet-install/install-fleet.sh")
        print(f"   {state('WARN', 5)} {hub_notes[-1]}")
    all_checks = latest_checks(inv)
    (STATE / "latest-digest.txt").write_text(build_digest(inv, all_checks, hub_notes) + "\n")
    os.chmod(STATE / "latest-digest.txt", 0o644)
    if not a.no_push:
        push(inv, quiet=True)
    kuma_heartbeats(results, all_checks, whole_fleet=a.all)
    bad = [r["id"] for r in results.values() if r["status"] in ("CRIT", "STALE")]
    warn = [r["id"] for r in results.values() if r["status"] == "WARN"]
    print(paint(f"CHECK-{'FAILED ' + ','.join(bad) if bad else 'OK'}", "red" if bad else "green")
          + ((" (warnings: " + ",".join(warn) + ")") if warn else ""))
    return 1 if (bad and a.strict) else 0   # the check ran: a finding is a finding, not a failed run (--strict for scripts)


def cmd_digest(a, inv):
    p = STATE / "latest-digest.txt"
    if not p.exists():
        sys.exit("fleet: no digest yet (run `fleet check --all`)")
    print(p.read_text(), end="")
    if a.send:
        text = p.read_text().strip()
        if Path("/etc/systemd/system/fleet-digest.service").exists() and Path("/opt/iiab/ai-gateway/.venv").exists():
            run_local(["systemctl", "start", "fleet-digest.service"])
            print("(handed to fleet-digest.service: the gateway's admin notification)")
        else:
            conf = load_push_conf()
            sent = False
            if conf:
                import requests
                try:
                    r = requests.post(f"{conf['FLEET_SITE_URL'].rstrip('/')}/api/method/fleet_ops.api.notify",
                                      json={"subject": "Fleet digest: " + text.splitlines()[0], "text": text, **({"to": conf["DIGEST_TO"]} if conf.get("DIGEST_TO") else {})},
                                      headers={"Authorization": f"token {conf['FLEET_API_KEY']}:{conf['FLEET_API_SECRET']}", "Host": conf["FLEET_SITE_NAME"],
                                               "X-Frappe-Site-Name": conf["FLEET_SITE_NAME"]}, timeout=60)
                    r.raise_for_status()
                    print(f"(sent through the fleet site: {r.json().get('message')})")
                    sent = True
                except Exception as e:
                    print(f"(fleet site mail failed: {e})")
            if not sent:
                sent = digest_via_observer(inv, text)
            if not sent:
                print("(no delivery path worked: no gateway here, the fleet site could not mail, no observer with the digest unit)")
                return 1
    return 0


def digest_via_observer(inv: dict, text: str) -> bool:
    """Hand the digest to the observer's gateway notification path (fleet-digest.service there),
    the delivery that works without any mail account on the hub. False when there is no such box
    or the unit did not deliver."""
    obs = next((b for b in inv["boxes"] if b.get("role") == "observer" and not b.get("local") and b.get("status") != "offboarded"), None)
    if not obs:
        return False
    rc, out = run_on(obs, "test -f /etc/systemd/system/fleet-digest.service && echo unit || echo none")
    if rc != 0 or "unit" not in out:
        print(f"({label(obs)} has no digest unit)")
        return False
    tmp = STATE / "digest-handoff.txt"
    tmp.write_text(text + "\n")
    scp_to(obs, tmp, "/var/lib/fleet/latest-digest.txt", "0644")
    rc, out = run_on(obs, "systemctl start fleet-digest.service && systemctl is-failed fleet-digest.service", timeout=120)
    ok = "inactive" in out and "failed" not in out.replace("is-failed", "")
    print(f"(handed to {label(obs)}'s fleet-digest.service: {'delivered' if ok else 'FAILED ' + out[-80:]})")
    return ok


def cmd_status(a, inv):
    checks = latest_checks(inv)
    print(f"release: {inv.get('current_release') or 'none'}   inventory: {INVENTORY}   catalog: {CATALOG}")
    print(f"{'id':<8} {'alias':<17} {'host':<22} {'role':<9} {'conn':<8} {'status':<10} {'check':<6} {'when':<17} findings")
    for b in sorted(inv["boxes"], key=lambda b: (b["canary_rank"], b["id"])):
        c = checks.get(b["id"], {})
        when = c.get("checked_at", "-")[:16].replace("T", " ")
        fx = "; ".join(f"{x['level']} {x['message']}" for x in c.get("findings", []) if x["level"] != "INFO") or "-"
        print(f"{b['id']:<8} {b['alias']:<17} {b['host']:<22} {b['role']:<9} {b['connectivity']:<8} {b['status']:<10} {state(c.get('status', '-'), 6)} {when:<17} {fx}")
    return 0


def cmd_tailnet(a, inv):
    """Our machines as the tailnet sees them from this hub: which inventory box each node is,
    online or last seen, OS, relay, tag. The iiab-vpn peer table, keyed by box."""
    rc, out, err = tailscale_status()
    if rc != 0:
        sys.exit("fleet: tailscale status failed: " + (err.strip() or out.strip())[-200:])
    d = json.loads(out)
    nodes = [d["Self"]] + list((d.get("Peer") or {}).values())
    by_ip, by_name = {}, {b["id"]: b for b in inv["boxes"]}
    for b in inv["boxes"]:
        for addr in (b["host"], (b.get("reach") or {}).get("ssh", ""), (b.get("reach") or {}).get("http", "")):
            addr = re.sub(r"^https?://", "", addr or "").split("/")[0].split(":")[0]
            if addr:
                by_ip[addr] = b

    def root(tag: str) -> str:            # tag:blondel-ephem -> blondel
        return tag.split(":", 1)[-1].split("-")[0]
    rows, ours = [], {root(t) for t in d["Self"].get("Tags") or []}
    for n in nodes:
        ips = n.get("TailscaleIPs") or []
        box = by_name.get(n.get("HostName")) or next((by_ip[i] for i in ips if i in by_ip), None)
        if box:
            ours |= {root(t) for t in n.get("Tags") or []}
        rows.append((n, box))
    mine = [(n, b) for n, b in rows if b or ({root(t) for t in n.get("Tags") or []} & ours)]
    print(f"{'node':<20} {'address':<13} {'box':<24} {'state':<8} {'last seen':<17} {'os':<8} {'relay':<6} tag")
    for n, b in sorted(mine, key=lambda t: (t[1] is None, (t[1] or {}).get("id", ""), t[0].get("HostName", ""))):
        online = bool(n.get("Online"))
        last = "" if online else (n.get("LastSeen") or "")[:16].replace("T", " ")
        tag = ",".join(t.replace("tag:", "") for t in n.get("Tags") or []) or "-"
        print(f"{n.get('HostName', '?'):<20} {(n.get('TailscaleIPs') or ['?'])[0]:<13} {(label(b) if b else '-'):<24} "
              f"{paint(f'{'online' if online else 'offline':<8}', 'green' if online else 'red')} {last:<17} {n.get('OS', '?'):<8} {n.get('Relay') or '-':<6} {tag}")
    seen = {b["id"] for _, b in mine if b}
    missing = [b for b in inv["boxes"] if b["id"] not in seen and b.get("connectivity") == "tailnet"]
    if missing:
        print(paint("not on the tailnet: " + ", ".join(label(b) for b in missing), "red"))
    unnamed = [n.get("HostName") for n, b in mine if not b and (n.get("OS") == "linux")]
    others = len(rows) - len(mine)
    print(f"\n{others} other members' nodes not shown." + (f"  Linux nodes under our tag without a box record: {', '.join(unnamed)} (fleet hostname <id> names a box's node; tailscale logout retires a stray one)" if unnamed else ""))
    print("Check a node: tailscale ping <address>   tailscale whois <address>   Undo a join: tailscale logout (on that machine)")
    return 0


def cmd_list(a, inv):
    print(f"{'rank':<4} {'id':<8} {'alias':<17} {'host':<22} {'business':<24} {'role':<9} {'owner':<9} {'conn':<8} {'plan':<10} {'status':<10} services")
    for b in sorted(inv["boxes"], key=lambda b: (b["canary_rank"], b["id"])):
        print(f"{b['canary_rank']:<4} {b['id']:<8} {b['alias']:<17} {b['host']:<22} {b['business']:<24} {b['role']:<9} {b['owner']:<9} {b['connectivity']:<8} {b.get('plan', ''):<10} {b['status']:<10} {','.join(b['services'])}")
    return 0
