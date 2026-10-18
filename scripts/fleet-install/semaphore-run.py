#!/usr/bin/env python3
"""Run a Semaphore template by name and print its output (test harness; prints no secrets)."""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path
conf = {k.strip(): v.strip() for k, _, v in (l.partition("=") for l in Path("/etc/iiab/fleet.conf").read_text().splitlines() if "=" in l and not l.startswith("#"))}
BASE = conf["SEMAPHORE_URL"].rstrip("/"); name = sys.argv[1]; env = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
cookie = None; token = None
def call(method, path, body=None, raw=False):
    global cookie
    req = urllib.request.Request(BASE + "/api" + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", "Accept": "application/json"})
    if token: req.add_header("Authorization", f"Bearer {token}")
    elif cookie: req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.headers.get("Set-Cookie"): cookie = r.headers["Set-Cookie"].split(";")[0]
        d = r.read(); return (json.loads(d) if d else None) if not raw else d
call("POST", "/auth/login", {"auth": conf["SEMAPHORE_ADMIN_USER"], "password": conf["SEMAPHORE_ADMIN_PASSWORD"]}, raw=True)
token = call("POST", "/user/tokens", {})["id"]
pid = next(p["id"] for p in call("GET", "/projects") if p["name"] == "Fleet")
tpl = next(t for t in call("GET", f"/project/{pid}/templates") if t["name"] == name)
task = call("POST", f"/project/{pid}/tasks", {"template_id": tpl["id"], "project_id": pid, "environment": json.dumps(env), "params": {}})
tid = task["id"]
for _ in range(60):
    time.sleep(3)
    st = call("GET", f"/project/{pid}/tasks/{tid}")
    if st["status"] in ("success", "error", "stopped"): break
print("task", tid, "status:", st["status"])
for line in call("GET", f"/project/{pid}/tasks/{tid}/output") or []:
    print("  ", line.get("output", "")[:160])
