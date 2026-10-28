"""The check history under /var/lib/fleet/checks/<id>/."""
from __future__ import annotations

import json

from .config import KEEP_CHECKS, STATE


def write_check(res: dict) -> None:
    d = STATE / "checks" / res["id"]
    d.mkdir(parents=True, exist_ok=True)
    stamp = res["checked_at"].replace(":", "").replace("-", "")
    (d / f"{stamp}.json").write_text(json.dumps(res, indent=1))
    (d / "latest.json").write_text(json.dumps(res, indent=1))
    for p in sorted(p for p in d.glob("*.json") if p.name != "latest.json")[:-KEEP_CHECKS]:
        p.unlink()


def latest_checks(inv: dict) -> dict[str, dict]:
    out = {}
    for b in inv["boxes"]:
        p = STATE / "checks" / b["id"] / "latest.json"
        if p.exists():
            out[b["id"]] = json.loads(p.read_text())
    return out
