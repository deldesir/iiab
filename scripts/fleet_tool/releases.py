"""Release manifests: app=commit files under /etc/iiab/fleet-releases, and the heads a box runs."""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from .config import RELEASES, save_inventory, select_boxes
from .transport import run_on


def manifest_path(rid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", rid):
        sys.exit("fleet: release id may contain letters, digits, . _ -")
    return RELEASES / f"{rid}.manifest"


def read_manifest(rid: str) -> dict[str, str]:
    p = manifest_path(rid)
    if not p.exists():
        sys.exit(f"fleet: no release {rid} ({p})")
    heads = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            heads[k.strip()] = v.strip()
    return heads


def manifest_apps(m: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in m.items() if k not in ("release", "captured", "notes") and re.fullmatch(r"[0-9a-f]{40}", v or "")}


def dropped_apps(inv: dict, apps: dict[str, str]) -> list[str]:
    """The apps the current release pins that a new manifest (or a box's heads) would leave unpinned."""
    cur = inv.get("current_release") or ""
    if not cur or not manifest_path(cur).exists():
        return []
    return sorted(set(manifest_apps(read_manifest(cur))) - set(apps))


def heads_of(box: dict) -> dict[str, str]:
    """Every app in the bench's apps.txt -> full commit (bench-upgrade.sh uses the same list)."""
    cmd = (f"for a in $(cat {box['bench']}/sites/apps.txt 2>/dev/null); do "
           f"printf '%s=%s\\n' $a $(sudo -u frappe git -C {box['bench']}/apps/$a rev-parse HEAD 2>/dev/null || echo '?'); done")
    rc, out = run_on(box, cmd)
    heads = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k and re.fullmatch(r"[a-z_0-9]+", k):
            heads[k] = v.strip()
    return heads


def cmd_manifest(a, inv):
    RELEASES.mkdir(parents=True, exist_ok=True)
    if a.action == "capture":
        box = select_boxes(inv, [a.from_box] if a.from_box else ["hub"], False)[0]
        heads = heads_of(box)
        bad = [k for k, v in heads.items() if v == "?"]
        if not heads or bad:
            sys.exit(f"fleet: could not read heads on {box['id']}: {bad or 'none'}")
        p = manifest_path(a.id)
        if p.exists() and not a.force:
            sys.exit(f"fleet: {p} exists (use --force to overwrite)")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [f"# fleet release {a.id} — captured {stamp} from {box['id']}",
                 "# app=full commit; bench-upgrade.sh pins each app it finds on the box to exactly this commit",
                 f"release={a.id}", f"captured={stamp}"]
        if a.notes:
            lines.append(f"notes={a.notes}")
        lines += [f"{k}={v}" for k, v in sorted(heads.items())]
        p.write_text("\n".join(lines) + "\n")
        print(f"captured {a.id} -> {p}")
        for k, v in sorted(heads.items()):
            print(f"  {k:<10} {v[:10]}")
        dropped = dropped_apps(inv, heads)
        if dropped:
            print(f"note: {inv['current_release']} pins {', '.join(dropped)}, which {box['id']} does not run; a manifest from one box pins only"
                  f" what that box carries (the hub's bench carries every app). set-current will refuse {a.id} unless --allow-drop is passed")
    elif a.action == "list":
        cur = inv.get("current_release") or ""
        for p in sorted(RELEASES.glob("*.manifest")):
            m = read_manifest(p.stem)
            print(f"{p.stem:<18} {m.get('captured', '?'):<22} {len(manifest_apps(m))} apps{'  (current)' if p.stem == cur else ''}")
    elif a.action == "show":
        for k, v in read_manifest(a.id).items():
            print(f"{k}={v}")
    elif a.action == "set-current":
        dropped = dropped_apps(inv, manifest_apps(read_manifest(a.id))) if a.id != inv.get("current_release") else []
        if dropped and not a.allow_drop:
            sys.exit(f"fleet: {a.id} pins no commit for {', '.join(dropped)}, which {inv['current_release']} pins: an upgrade would leave those apps"
                     f" wherever each box has them (capture from the hub, whose bench carries every app, or pass --allow-drop)")
        inv["current_release"] = a.id
        save_inventory(inv)
        print(f"current_release = {a.id}")
    return 0
