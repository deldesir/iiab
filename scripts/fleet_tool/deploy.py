"""What the hub installs on a box: the agent, the watchdog, a key, a hostname; the private copies of each box's local_vars."""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

from .config import ID_RE, PUSH_CONF, SCRIPTS, STATE, UPGRADE_SCRIPT, in_window, label, log_action, read_conf, require_targets, save_inventory, select_boxes, write_private
from .site import agent_token_for, push, push_token_for
from .transport import run_on, scp_to


def freeze_windows_text(box: dict) -> str:
    """The box's freeze windows as the agent reads them: `from..to` pairs joined by spaces."""
    return " ".join(f"{w['from']}..{w['to']}" for w in box.get("freeze_windows") or [])


def agent_conf_text(box: dict) -> str:
    conf = read_conf(PUSH_CONF)
    hub = conf.get("FLEET_PUBLIC_URL") or sys.exit("fleet: FLEET_PUBLIC_URL is not set in the fleet configuration")
    kuma = f"{conf['KUMA_URL'].rstrip('/')}/api/push/{push_token_for(box['id'], conf['KUMA_PUSH_TOKEN'])}" if conf.get("KUMA_URL") and conf.get("KUMA_PUSH_TOKEN") else ""
    return (f"# fleet-agent configuration for {box['id']} (0600). Reports go to the fleet site on the hub.\n"
            f"FLEET_BOX_ID={box['id']}\nFLEET_HUB_URL={hub}\nFLEET_AGENT_TOKEN={agent_token_for(box['id'])}\n"
            f"KUMA_PUSH_URL={kuma}\nSITE={box['site']}\nBENCH={box['bench']}\nMAINTENANCE_WINDOW={box.get('maintenance_window', '')}\nTIMEZONE={box.get('timezone', 'UTC')}\n"
            f"FREEZE_WINDOWS={freeze_windows_text(box)}\n")


def cmd_agent_token(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    print(agent_conf_text(box), end="")
    return 0


def cmd_deploy_agent(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    units = SCRIPTS / "fleet-units"
    tmp = STATE / f"agent-{box['id']}.conf"
    write_private(tmp, agent_conf_text(box))
    scp_to(box, SCRIPTS / "fleet-agent", "/usr/local/sbin/fleet-agent")
    scp_to(box, SCRIPTS / "fleet-probe", "/usr/local/sbin/fleet-probe")
    scp_to(box, UPGRADE_SCRIPT, "/root/bench-upgrade.sh")
    scp_to(box, tmp, "/etc/iiab/fleet-agent.conf", "0600")
    for u in ("fleet-agent.service", "fleet-agent.timer", "fleet-agent-upgrade.service", "fleet-agent-upgrade.timer"):
        scp_to(box, units / u, f"/etc/systemd/system/{u}", "0644")
    rc, out = run_on(box, "systemctl daemon-reload && systemctl enable --now fleet-agent.timer fleet-agent-upgrade.timer && /usr/local/sbin/fleet-agent report")
    tmp.unlink(missing_ok=True)
    print(out[-800:])
    log_action(f"deploy-agent {box['id']} rc={rc}")
    push(inv, quiet=True)
    if rc == 0:
        print(f"Check: fleet status  ({label(box)} reports within the hour; its push monitor in Uptime Kuma turns up)")
    return 0 if rc == 0 else 1


def cmd_deploy_watchdog(a, inv):
    box = select_boxes(inv, [a.id], False)[0]
    if box["role"] == "hub":
        sys.exit("fleet: the hub watchdog must run on another box")
    hub = next(b for b in inv["boxes"] if b["role"] == "hub")
    units = SCRIPTS / "fleet-units"
    scp_to(box, SCRIPTS / "fleet-hub-watchdog", "/usr/local/sbin/fleet-hub-watchdog")
    for u in ("fleet-hub-watchdog.service", "fleet-hub-watchdog.timer"):
        scp_to(box, units / u, f"/etc/systemd/system/{u}", "0644")
    conf = (f"# Hub watchdog (0600). Fill the Contabo API credentials (Customer Control Panel -> API) to let it\n"
            f"# start the hub when Contabo reports it stopped. Without them it only logs.\n"
            f"HUB_PING_URL=https://{hub['host']}/erp/api/method/ping\nHUB_PUBLIC_HOST={hub['host']}\nFAIL_THRESHOLD=3\nCOOLDOWN_MIN=30\n"
            f"CONTABO_CLIENT_ID=\nCONTABO_CLIENT_SECRET=\nCONTABO_API_USER=\nCONTABO_API_PASSWORD=\nCONTABO_INSTANCE_ID=\n")
    tmp = STATE / f"watchdog-{box['id']}.conf"
    write_private(tmp, conf)
    rc, out = run_on(box, "test -f /etc/iiab/fleet-hub-watchdog.conf && echo exists || echo new")
    if "exists" not in out:
        scp_to(box, tmp, "/etc/iiab/fleet-hub-watchdog.conf", "0600")
    tmp.unlink(missing_ok=True)
    rc, out = run_on(box, "mkdir -p /var/lib/fleet && systemctl daemon-reload && systemctl enable --now fleet-hub-watchdog.timer && /usr/local/sbin/fleet-hub-watchdog --once")
    print(out[-800:])
    log_action(f"deploy-watchdog {box['id']} rc={rc}")
    if rc == 0:
        inv["hub_watchdog_box"] = box["id"]
        save_inventory(inv)
        print(f"inventory: hub_watchdog_box = {box['id']} (the hub's check now reads its log)")
        print(f"Next:  on {label(box)}, fill /etc/iiab/fleet-hub-watchdog.conf with the Contabo API credentials; it only logs until then\n"
              f"Check: ssh {box['ssh_user']}@{box['reach']['ssh']} tail -n 5 /var/lib/fleet/hub-watchdog.log")
    return 0 if rc == 0 else 1


def cmd_vars(a, inv):
    """Keep a private copy of every box's /etc/iiab/local_vars.yml on the hub (pull), show drift
    (diff). Pushing is deliberate and manual: `fleet vars show <id>` prints the path to copy."""
    store = Path("/etc/iiab/fleet-vars")
    store.mkdir(mode=0o700, exist_ok=True)
    require_targets(a)
    boxes = select_boxes(inv, a.ids, a.all)
    rc_all = 0
    for b in boxes:
        f = store / f"{b['id']}.local_vars.yml"
        if a.action == "pull":
            rc, out = run_on(b, "cat /etc/iiab/local_vars.yml")
            if rc != 0 or not out:
                print(f"{b['id']}: could not read local_vars.yml")
                rc_all = 1
                continue
            changed = (not f.exists()) or f.read_text() != out + "\n"
            f.write_text(out + "\n")
            os.chmod(f, 0o600)
            print(f"{b['id']}: {'updated' if changed else 'unchanged'} -> {f}")
        elif a.action == "diff":
            if not f.exists():
                print(f"{b['id']}: no copy yet (fleet vars pull {b['id']})")
                continue
            rc, out = run_on(b, "cat /etc/iiab/local_vars.yml")
            import difflib
            d = list(difflib.unified_diff(f.read_text().splitlines(), (out + "\n").splitlines(), f"hub copy {b['id']}", f"box {b['id']}", lineterm=""))
            d = [l for l in d if not re.search(r"(password|secret|token|key)\s*:", l, re.I) or l.startswith(("---", "+++", "@@"))]
            print(f"== {b['id']}: {'in sync' if not d else str(len(d)) + ' diff lines (secret lines hidden)'}")
            for l in d[:60]:
                print("   " + l)
        elif a.action == "show":
            print(f"{b['id']}: {f if f.exists() else 'no copy yet'}")
    return rc_all


HOSTNAME_SCRIPT = r"""set -e
new="$1"; old=$(hostnamectl --static 2>/dev/null || hostname)
python3 - "$new" <<'PY'
import re, sys
new = sys.argv[1]
lines, out, seen = open("/etc/hosts").read().splitlines(), [], set()
for l in lines:                      # IIAB appends its line on every run: collapse duplicates
    k = " ".join(l.split())
    if k and not l.startswith("#") and k in seen:
        continue
    seen.add(k); out.append(l)
hosts = "\n".join(out).rstrip("\n") + "\n"
want = f"127.0.0.1 box.lan localhost.localdomain localhost box {new} {new}.lan"
m = re.search(r"^127\.0\.0\.1\s+.*\bbox\.lan\b.*$", hosts, re.M)
hosts = hosts.replace(m.group(0), want, 1) if m else hosts + want + "\n"
open("/etc/hosts", "w").write(hosts)
PY
hostnamectl set-hostname "$new"
grep -q '^iiab_hostname:' /etc/iiab/local_vars.yml && sed -i "s/^iiab_hostname:.*/iiab_hostname: $new/" /etc/iiab/local_vars.yml || true
if command -v tailscale >/dev/null; then tailscale set --hostname "$new" 2>&1 | head -1 || true; fi
echo "HOSTNAME-OK $(hostname) (was $old)"
"""


def cmd_hostname(a, inv):
    """Make the machine's own name match its id: /etc/hosts, hostnamectl, iiab_hostname in
    local_vars (so IIAB never writes the old name back), and the tailnet hostname."""
    box = select_boxes(inv, [a.id], False)[0]
    if not ID_RE.match(box["id"]):
        sys.exit(f"fleet: {box['id']} is not a box-NNN id yet (fleet rename first)")
    if not (a.now or box.get("local") or in_window(box)):
        sys.exit(f"fleet: {label(box)} is outside its maintenance window {box.get('maintenance_window')} ({box.get('timezone')}); use --now")
    tmp = STATE / f"hostname-{box['id']}.sh"
    tmp.write_text(HOSTNAME_SCRIPT)
    scp_to(box, tmp, "/root/fleet-hostname.sh", "0700")
    rc, out = run_on(box, f"bash /root/fleet-hostname.sh {shlex.quote(box['id'])}; r=$?; rm -f /root/fleet-hostname.sh; exit $r", timeout=120)
    tmp.unlink(missing_ok=True)
    print(out[-600:])
    log_action(f"hostname {box['id']} rc={rc}")
    if rc == 0:
        print(f"Check: ssh {box['ssh_user']}@{box['reach']['ssh']} hostname   # {box['id']}; fleet tailnet shows the node's name once its admins rename it")
    return 0 if rc == 0 else 1


def cmd_authorize_key(a, inv):
    """Let another machine (typically the next hub) in as root on the selected boxes: append
    its public key to /root/.ssh/authorized_keys where it is missing. --check only reports."""
    pub = Path(a.file).read_text().strip().splitlines()
    pub = [l for l in pub if l and not l.startswith("#")]
    if len(pub) != 1 or not re.match(r"^(ssh-(rsa|ed25519)|ecdsa-sha2-\S+) \S+", pub[0]):
        sys.exit("fleet: expected one OpenSSH public key line in the file")
    key = pub[0]
    keytype, blob = key.split()[:2]
    require_targets(a)
    rc_all = 0
    for b in select_boxes(inv, a.ids, a.all):
        probe = f"grep -qsF {shlex.quote(blob)} /root/.ssh/authorized_keys && echo PRESENT || echo ABSENT"
        rc, out = run_on(b, probe, timeout=30)
        if rc != 0 and "ABSENT" not in out and "PRESENT" not in out:
            print(f"{label(b)}: unreachable ({out[-80:]})")
            rc_all = 1
            continue
        if "PRESENT" in out:
            print(f"{label(b)}: already present")
            continue
        if a.check:
            print(f"{label(b)}: absent")
            continue
        add = (f"install -d -m 700 /root/.ssh && printf '%s\\n' {shlex.quote(key)} >> /root/.ssh/authorized_keys"
               f" && chmod 600 /root/.ssh/authorized_keys && echo ADDED")
        rc, out = run_on(b, add, timeout=30)
        print(f"{label(b)}: {'added' if 'ADDED' in out else 'FAILED ' + out[-80:]}")
        rc_all = rc_all or (0 if "ADDED" in out else 1)
    log_action(f"authorize-key {keytype} {blob[-12:]} check={a.check} boxes={','.join(b['id'] for b in select_boxes(inv, a.ids, a.all))}")
    return rc_all
