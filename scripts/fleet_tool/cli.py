"""fleet — operate a fleet of Frappe/ERPNext boxes from the hub (v2).

The inventory (/etc/iiab/fleet.yml) is the source of truth: one record per box with its
role, owner, plan, status, canary rank, maintenance window, connectivity class and the
catalog services it carries (scripts/fleet-catalog.yml). Nothing here edits it except
`onboard`, `rename`, `suspend`, `resume`, `offboard` and `manifest set-current`.
Identity: every box has an id (box-NNN, given once at onboarding, never reused), an alias
(short slug the people use; may change) and a host (the customer's domain). Commands
accept id, alias or host; output shows "id alias".

  fleet list | status | tailnet         inventory; last check per box; our nodes as the tailnet sees them
  fleet check [ID...|--all] [--no-push]   OK / WARN / CRIT / STALE per box with levelled
                                          findings; feeds the per-box monitors; writes
                                          /var/lib/fleet/checks/<id>/ and the digest
  fleet digest [--send]
  fleet manifest capture --id ID [--from BOX] [--notes ..] | list | show ID | set-current ID
  fleet upgrade [ID...|--all] [--release ID] [--preflight] [--now] [--allow-discard ERE]
  fleet suspend ID [--reason TEXT] | resume ID | offboard ID
  fleet dunning [--apply]                 boxes with overdue invoices in the fleet site; --apply
                                          suspends those past their grace (nightly when the
                                          inventory says auto_suspend: true)
  fleet rollback ID [--release RID] [--no-restore] [--dry-run] [--now]
                                          undo the box's last upgrade: apps back to the commits
                                          that ran before it and the database/files restored
                                          from the pre-upgrade backup (or go to a manifest)
  fleet push                              mirror inventory, catalog, checks, releases
  fleet agent-token ID                    the stanza for /etc/iiab/fleet-agent.conf on an
                                          offline (push-mode) box
  fleet deploy-agent ID                   install fleet-agent + units on that box (ssh)
  fleet deploy-watchdog ID                install the hub watchdog on a non-hub box (ssh)
  fleet runrole ID ROLE [--tags T]        run an IIAB role on a box through its own runrole
  fleet reboot ID|--all [--now]           reboot inside the window, wait, re-check
  fleet backup-pull [--dest DIR]          mirror the observer's backup copies onto this hub
  fleet vars pull|diff|show [ID...|--all] private copies of each box's local_vars.yml on the hub
  fleet onboard --host H --business B --alias A [--role customer|till|hub|observer] [--owner customer|self]
                                          [--profile P] [--plan P] [--connectivity C] [--services a,b]
                                          [--billing E] [--technical E] [--id box-NNN]   register a box
  fleet rename ID [NEW_ID] [--alias A]    rename everywhere: inventory, history, vars copy, fleet
                                          site record, monitors (+ push token)
  fleet hostname ID [--now]               set the box's OS (and tailnet) hostname to its id
  fleet authorize-key FILE [ID...|--all] [--check]   add a public key to root's authorized_keys on
                                          boxes (a new hub's key); --check only reports

Roles: hub (this control plane; may live at home, intermittently online), observer (an
always-on VPS that runs the monitor, the hub watchdog and fleet-backup-mail), till, customer.
Connectivity: public boxes are probed over https on their host; tailnet boxes over http on
their tailnet address (no TLS/domain checks); offline boxes are never polled: fleet-agent
reports to the fleet site and the hub judges freshness (STALE past the heartbeat allowance,
CRIT past twice). Exit 0 ok, 1 a box is CRIT/STALE or an upgrade failed, 2 usage/config.
"""
from __future__ import annotations

import argparse

from .checks import cmd_check, cmd_digest, cmd_list, cmd_status, cmd_tailnet
from .config import MUTATING, acquire_lock, load_inventory
from .deploy import cmd_agent_token, cmd_authorize_key, cmd_deploy_agent, cmd_deploy_watchdog, cmd_hostname, cmd_vars
from .identity import cmd_onboard, cmd_rename
from .lifecycle import cmd_dunning, cmd_offboard, cmd_resume, cmd_suspend
from .releases import cmd_manifest
from .site import cmd_push
from .upgrade import cmd_backup_pull, cmd_reboot, cmd_rollback, cmd_runrole, cmd_upgrade


_HELD: list = []   # lock handles kept open for the process's life

COMMANDS = {"list": cmd_list, "status": cmd_status, "tailnet": cmd_tailnet, "check": cmd_check, "digest": cmd_digest, "manifest": cmd_manifest,
            "upgrade": cmd_upgrade, "suspend": cmd_suspend, "resume": cmd_resume, "offboard": cmd_offboard, "push": cmd_push,
            "agent-token": cmd_agent_token, "deploy-agent": cmd_deploy_agent, "deploy-watchdog": cmd_deploy_watchdog,
            "runrole": cmd_runrole, "vars": cmd_vars, "reboot": cmd_reboot, "backup-pull": cmd_backup_pull,
            "onboard": cmd_onboard, "rename": cmd_rename, "hostname": cmd_hostname, "authorize-key": cmd_authorize_key,
            "rollback": cmd_rollback, "dunning": cmd_dunning}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fleet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sub.add_parser("list", help="the inventory, in canary order")
    sub.add_parser("status", help="the last check of every box")
    sub.add_parser("tailnet", help="our nodes as the tailnet sees them from this hub")
    p = sub.add_parser("check", help="check boxes: OK / WARN / CRIT / STALE with findings; feeds the monitors and the digest")
    p.add_argument("ids", nargs="*", help="box ids, aliases or hosts (none = every box)")
    p.add_argument("--all", action="store_true", help="every box (also sends the fleet-wide heartbeat)")
    p.add_argument("--no-push", action="store_true", help="do not mirror the results into the fleet site")
    p.add_argument("--strict", action="store_true", help="exit 1 when a box is CRIT or STALE")
    p = sub.add_parser("digest", help="print the last digest; --send delivers it")
    p.add_argument("--send", action="store_true", help="deliver it (the gateway here, else the fleet site, else the observer)")
    p = sub.add_parser("manifest", help="release manifests: capture, list, show, set-current")
    ps = p.add_subparsers(dest="action", required=True, metavar="ACTION")
    c = ps.add_parser("capture", help="record the app commits a box runs as a release")
    c.add_argument("--id", required=True, help="the release id, e.g. 2026.10-r14")
    c.add_argument("--from", dest="from_box", help="the box to read (default: the hub)")
    c.add_argument("--notes"); c.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    ps.add_parser("list", help="every release, the current one marked")
    c = ps.add_parser("show", help="the commits of a release"); c.add_argument("id")
    c = ps.add_parser("set-current", help="make a release the one the fleet is judged against"); c.add_argument("id")
    p = sub.add_parser("upgrade", help="upgrade boxes in canary order, each followed and checked; stops at the first failure")
    p.add_argument("ids", nargs="*"); p.add_argument("--all", action="store_true")
    p.add_argument("--release", help="a manifest to pin every app to (default: the current release)")
    p.add_argument("--preflight", action="store_true", help="only the checks bench-upgrade.sh runs before touching anything")
    p.add_argument("--now", action="store_true", help="outside the maintenance window, or a suspended box")
    p.add_argument("--allow-discard", metavar="ERE", help="apps whose local modifications may be discarded")
    p = sub.add_parser("suspend", help="maintenance page on, status suspended"); p.add_argument("id"); p.add_argument("--reason", default="")
    p = sub.add_parser("resume", help="maintenance page off, status active"); p.add_argument("id")
    p = sub.add_parser("offboard", help="final backup with files, status offboarded (the wipe stays manual)"); p.add_argument("id")
    sub.add_parser("push", help="mirror inventory, catalog, checks and releases into the fleet site")
    p = sub.add_parser("agent-token", help="the fleet-agent configuration stanza for an offline box"); p.add_argument("id")
    p = sub.add_parser("deploy-agent", help="install fleet-agent and its units on a box over ssh"); p.add_argument("id")
    p = sub.add_parser("deploy-watchdog", help="install the hub watchdog on a non-hub box over ssh"); p.add_argument("id")
    p = sub.add_parser("runrole", help="run an IIAB role on a box through its own runrole, following the log")
    p.add_argument("id"); p.add_argument("role"); p.add_argument("--tags")
    p = sub.add_parser("reboot", help="reboot a box inside its window, wait for it, re-check")
    p.add_argument("id", nargs="?"); p.add_argument("--all", action="store_true", help="every remote pull-mode box, canary order"); p.add_argument("--now", action="store_true")
    p = sub.add_parser("backup-pull", help="mirror the observer's backup copies onto this hub"); p.add_argument("--dest")
    p = sub.add_parser("rollback", help="undo a box's last upgrade: apps back, database and files restored")
    p.add_argument("id"); p.add_argument("--release", help="go to this manifest instead of the pre-upgrade state")
    p.add_argument("--no-restore", action="store_true", help="code only, keep the database"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--now", action="store_true")
    p = sub.add_parser("dunning", help="boxes with overdue invoices; --apply suspends those past their grace")
    p.add_argument("--apply", action="store_true"); p.add_argument("--auto", action="store_true", help="apply only when the inventory's auto_suspend is on (the nightly timer)")
    p = sub.add_parser("vars", help="private copies of each box's local_vars.yml on the hub: pull, diff, show")
    p.add_argument("action", choices=["pull", "diff", "show"]); p.add_argument("ids", nargs="*"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("onboard", help="register a box: id, alias, host, business, role, owner; monitors and vars copy follow")
    p.add_argument("--host", required=True); p.add_argument("--business", required=True); p.add_argument("--alias", required=True)
    p.add_argument("--role", default="customer", choices=["customer", "till", "hub", "observer"]); p.add_argument("--owner", default="customer", choices=["customer", "self"])
    p.add_argument("--profile", default="retail"); p.add_argument("--plan"); p.add_argument("--connectivity", choices=["public", "tailnet", "offline"])
    p.add_argument("--services", help="catalog service ids, comma-separated"); p.add_argument("--billing"); p.add_argument("--technical"); p.add_argument("--timezone"); p.add_argument("--window")
    p.add_argument("--rank", type=int, help="canary rank (default: last)"); p.add_argument("--id", help="box-NNN (default: the next free number)")
    p = sub.add_parser("rename", help="rename a box everywhere: inventory, history, vars copy, fleet site, monitors")
    p.add_argument("id"); p.add_argument("new_id", nargs="?"); p.add_argument("--alias")
    p = sub.add_parser("hostname", help="set the box's OS and tailnet hostname to its id"); p.add_argument("id"); p.add_argument("--now", action="store_true")
    p = sub.add_parser("authorize-key", help="add a public key to root's authorized_keys on boxes; --check only reports")
    p.add_argument("file"); p.add_argument("ids", nargs="*"); p.add_argument("--all", action="store_true"); p.add_argument("--check", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd in MUTATING:
        _HELD.append(acquire_lock())   # held until exit: the nightly check and a console command never interleave their writes
    inv = load_inventory()
    return COMMANDS[a.cmd](a, inv)
