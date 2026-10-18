#!/usr/bin/env python3
"""Seed Semaphore UI with the Fleet project: local repository (the iiab scripts), a local
inventory, one template per runbook wrapper with survey variables (env target). Idempotent by
name. Credentials from /etc/iiab/fleet.conf; nothing secret is printed."""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

conf = {}
for line in Path("/etc/iiab/fleet.conf").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        conf[k.strip()] = v.strip()
BASE = conf["SEMAPHORE_URL"].rstrip("/")
REPO_PATH = "/opt/iiab/iiab"


class Api:
    def __init__(self):
        self.cookie = None
        self.token = None

    def call(self, method, path, body=None, raw=False):
        req = urllib.request.Request(BASE + "/api" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        elif self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.headers.get("Set-Cookie"):
                    self.cookie = r.headers["Set-Cookie"].split(";")[0]
                data = r.read()
                return json.loads(data) if data and not raw else data
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            raise SystemExit(f"{method} {path} -> {e.code}: {body}")


api = Api()
api.call("POST", "/auth/login", {"auth": conf["SEMAPHORE_ADMIN_USER"], "password": conf["SEMAPHORE_ADMIN_PASSWORD"]}, raw=True)
tok = api.call("POST", "/user/tokens", {})
api.token = tok["id"]

projects = api.call("GET", "/projects")
proj = next((p for p in projects if p["name"] == "Fleet"), None) or api.call("POST", "/projects", {"name": "Fleet", "alert": False, "max_parallel_tasks": 1})
pid = proj["id"]
print("project Fleet:", pid)
P = f"/project/{pid}"


def ensure(path, name, body, key="name"):
    items = api.call("GET", path) or []
    for it in items:
        if it.get(key) == name:
            return it
    return api.call("POST", path, body)


key = ensure(f"{P}/keys", "none", {"name": "none", "type": "none", "project_id": pid})
repo = ensure(f"{P}/repositories", "iiab scripts (local)", {"name": "iiab scripts (local)", "project_id": pid, "git_url": REPO_PATH, "git_branch": "master", "ssh_key_id": key["id"]})
inv = ensure(f"{P}/inventory", "hub (local)", {"name": "hub (local)", "project_id": pid, "type": "static", "inventory": "[hub]\nlocalhost ansible_connection=local\n", "ssh_key_id": key["id"]})
env = ensure(f"{P}/environment", "default", {"name": "default", "project_id": pid, "json": "{}", "env": "{}"})
print("repo/inventory/environment:", repo["id"], inv["id"], env["id"])


def sv(name, title, desc="", required=False, default=""):
    d = {"name": name, "title": title, "description": desc, "required": required, "type": "", "target": "env"}
    if default:
        d["default_value"] = default
    return d


BOX = sv("BOX", "Box id", "empty = every box, in canary order")
TEMPLATES = [
    ("Fleet · check", "check.sh", "Health check (OK/WARN/CRIT/STALE per box), pushed to the fleet site and the monitors", [BOX]),
    ("Fleet · status", "status.sh", "Last check per box, release, inventory status", []),
    ("Fleet · digest", "digest.sh", "Show the digest; SEND=yes delivers it through the gateway", [sv("SEND", "Send it (yes/no)", default="no")]),
    ("Fleet · preflight", "preflight.sh", "Would every box fast-forward cleanly to the release? Changes nothing", [BOX, sv("RELEASE", "Release id", "empty = current")]),
    ("Fleet · upgrade", "upgrade.sh", "Roll boxes to the release in canary order (stops at the first failure)", [BOX, sv("RELEASE", "Release id", "empty = current"), sv("NOW", "Ignore maintenance windows (yes/no)", default="no"), sv("ALLOW_DISCARD", "Paths whose local changes may be discarded (regex)")]),
    ("Fleet · manifest capture", "manifest-capture.sh", "Record a release manifest from a box's heads", [sv("RELEASE_ID", "Release id (e.g. 2026.11-r9)", required=True), sv("BOX", "From box", default="hub"), sv("NOTES", "Notes"), sv("SET_CURRENT", "Make it current (yes/no)", default="yes")]),
    ("Fleet · suspend", "suspend.sh", "Maintenance page ON for a customer box (after the contract's grace period)", [sv("BOX", "Box id", required=True), sv("REASON", "Reason (logged)")]),
    ("Fleet · resume", "resume.sh", "Maintenance page OFF", [sv("BOX", "Box id", required=True)]),
    ("Fleet · monitor seed", "kuma-seed.sh", "Re-seed Uptime Kuma (adds what is missing)", []),
    ("Fleet · push", "push.sh", "Mirror inventory, catalog, checks and releases into the fleet site", []),
    ("Fleet · reboot", "reboot.sh", "Reboot a box (or all remote boxes) inside its maintenance window; NOW=yes overrides", [sv("BOX", "Box id, or all", required=True), sv("NOW", "Ignore the window (yes/no)", default="no")]),
    ("Fleet · backup pull", "backup-pull.sh", "Mirror the observer's backup copies onto this hub", []),
    ("IIAB · run role", "runrole.sh", "Run an IIAB Ansible role on a box through the box's own runrole (reads that box's local_vars.yml)", [sv("BOX", "Box id", required=True, default="hub"), sv("ROLE", "Role name (e.g. erpnext, rapidpro, ai-gateway)", required=True), sv("TAGS", "Tags (optional, comma-separated)")]),
]
existing = {t["name"]: t for t in (api.call("GET", f"{P}/templates") or [])}
for name, script, desc, vars_ in TEMPLATES:
    body = {"project_id": pid, "inventory_id": inv["id"], "repository_id": repo["id"], "environment_id": env["id"],
            "name": name, "playbook": f"scripts/fleet-tasks/{script}", "app": "bash", "type": "", "arguments": "[]",
            "description": desc, "survey_vars": vars_, "allow_override_args_in_task": False, "suppress_success_alerts": True}
    if name in existing:
        body["id"] = existing[name]["id"]
        api.call("PUT", f"{P}/templates/{existing[name]['id']}", body, raw=True)
        print("template updated:", name)
    else:
        api.call("POST", f"{P}/templates", body)
        print("template created:", name)
print(f"SEMAPHORE-SEED-OK {BASE}")
