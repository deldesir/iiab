"""The fleet tool against a scratch inventory and a fake transport. No test reaches a real box:
the fake raises on any host it was not given, and the real transport refuses to run under
FLEET_NO_NETWORK (conftest.py)."""
import json
import os
import re
import subprocess

import pytest

from fleet_tool import checks, cli, config, lifecycle, releases, site, transport, upgrade


def test_real_transport_refuses_while_the_tests_run():
    with pytest.raises(RuntimeError, match="FLEET_NO_NETWORK"):
        transport.Real().run_on({"id": "box-999", "ssh_user": "root", "reach": {"ssh": "a-real-box.example.test"}}, "true")


def test_fake_transport_raises_on_a_host_it_was_not_given(fake):
    with pytest.raises(RuntimeError, match="not a test host"):
        transport.run_on({"id": "box-099", "ssh_user": "root", "reach": {"ssh": "malianova.example.test"}}, "true")
    with pytest.raises(RuntimeError, match="not a test host"):
        transport.http_code("https://somebody-else.example.test/erp/api/method/ping")


def test_inventory_defaults_reach_and_mode(scratch):
    inv = config.load_inventory()
    by = {b["id"]: b for b in inv["boxes"]}
    assert by["box-002"]["reach"] == {"ssh": "shop.example.test", "http": "https://shop.example.test"}
    assert by["box-003"]["reach"] == {"ssh": "hotel-tailnet.example.test", "http": "http://hotel-tailnet.example.test"}
    assert by["box-003"]["mode"] == "pull" and by["box-002"]["bench"] == "/home/frappe/frappe-bench"
    assert by["box-001"]["local"] is True and by["box-002"]["status"] == "active"


def test_save_inventory_drops_the_derived_keys_and_keeps_the_header(scratch):
    inv = config.load_inventory()
    config.save_inventory(inv)
    text = (scratch / "fleet.yml").read_text()
    assert text.startswith("# scratch inventory")
    assert "mode:" not in text and "reach:\n" not in text.split("box-002")[1].split("box-003")[0]
    assert "ssh: hotel-tailnet.example.test" in text          # a reach that differs from the host is kept
    assert config.load_inventory()["boxes"][2]["reach"]["http"] == "http://hotel-tailnet.example.test"


def test_select_boxes_by_id_alias_or_host_and_canary_order(scratch):
    inv = config.load_inventory()
    assert [b["id"] for b in config.select_boxes(inv, [], True)] == ["box-001", "box-002", "box-003"]
    assert config.select_boxes(inv, ["shop"], False)[0]["id"] == "box-002"
    assert config.select_boxes(inv, ["hotel.example.test"], False)[0]["id"] == "box-003"
    with pytest.raises(SystemExit, match="unknown box"):
        config.select_boxes(inv, ["nobody"], False)


def test_require_targets_refuses_a_fleet_wide_mutation_by_omission():
    import argparse
    with pytest.raises(SystemExit, match="name the box"):
        config.require_targets(argparse.Namespace(ids=[], all=False))
    config.require_targets(argparse.Namespace(ids=["box-002"], all=False))


def test_conf_files_are_private_and_round_trip(scratch):
    p = scratch / "fleet.conf"
    config.set_conf(p, "KUMA_URL", "http://monitor.example.test")
    config.set_conf(p, "KUMA_URL", "http://monitor2.example.test")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert config.read_conf(p) == {"KUMA_URL": "http://monitor2.example.test"}


def test_in_window_wraps_midnight():
    import datetime as dt
    box = {"maintenance_window": "20:00-05:00", "timezone": "UTC"}
    now = dt.datetime.now(dt.timezone.utc).time()
    expect = now >= dt.time(20, 0) or now <= dt.time(5, 0)
    assert config.in_window(box) is expect
    assert config.in_window({"maintenance_window": ""}) is True


def test_manifest_read_and_apps(scratch):
    m = releases.read_manifest("2026.10-r1")
    assert releases.manifest_apps(m) == {"frappe": "a" * 40, "erpnext": "b" * 40}
    with pytest.raises(SystemExit, match="no release"):
        releases.read_manifest("nope")
    with pytest.raises(SystemExit, match="letters, digits"):
        releases.manifest_path("../etc")


def test_push_token_is_derived_and_stable():
    assert site.push_token_for("box-002", "master") == site.push_token_for("box-002", "master")
    assert site.push_token_for("box-002", "master") != site.push_token_for("box-003", "master")
    assert re.fullmatch(r"[0-9a-f]{32}", site.push_token_for("box-002", "master"))


def _box(inv, bid):
    return next(b for b in inv["boxes"] if b["id"] == bid)


def test_evaluate_levels(scratch):
    inv = config.load_inventory()
    cat = config.load_catalog()
    box = _box(inv, "box-002")
    ok = {"heads": {"frappe": "a" * 40, "erpnext": "b" * 40}, "targets": "active active active", "failed_units": "0", "disk_free_gb": "40",
          "probes": [{"url": "https://shop.example.test/erp/api/method/ping", "code": "200", "expect": "200", "critical": True, "service": "erp"}],
          "tls_days_left": 60, "hub_backup_age_h": 5.0, "errorlog_rows_today": "3", "maintenance_mode": "0", "uptime_s": "900000"}
    assert checks.evaluate(box, ok, inv, cat)["status"] == "OK"
    r = checks.evaluate(box, {"unreachable": True}, inv, cat)
    assert r["status"] == "CRIT" and r["findings"][0]["code"] == "unreachable"
    r = checks.evaluate(box, dict(ok, disk_free_gb="3"), inv, cat)
    assert r["status"] == "CRIT" and any(x["code"] == "disk" for x in r["findings"])
    r = checks.evaluate(box, dict(ok, disk_free_gb="10", tls_days_left=10), inv, cat)
    assert r["status"] == "WARN" and {x["code"] for x in r["findings"] if x["level"] == "WARN"} == {"disk", "tls"}
    r = checks.evaluate(box, dict(ok, heads={"frappe": "a" * 40, "erpnext": "c" * 40}), inv, cat)
    assert r["behind_release"] == ["erpnext"] and r["status"] == "WARN"
    r = checks.evaluate(box, dict(ok, hub_backup_age_h=None), inv, cat)
    assert r["status"] == "CRIT" and any(x["code"] == "backup" for x in r["findings"])
    r = checks.evaluate(box, dict(ok, probes=[dict(ok["probes"][0], code="502")]), inv, cat)
    assert r["status"] == "CRIT" and "HTTP 502" in r["problems"][0]


def test_evaluate_stale_report_and_night_audit(scratch):
    inv = config.load_inventory()
    cat = config.load_catalog()
    hotel = _box(inv, "box-003")
    offline = dict(hotel, mode="push", connectivity="offline")    # reports come from fleet-agent; freshness is judged here
    r = checks.evaluate(offline, {"stale_h": 31.0, "box_backup_age_h": "5"}, inv, cat)
    assert r["status"] == "STALE"
    r = checks.evaluate(offline, {"stale_h": 61.0, "box_backup_age_h": "5"}, inv, cat)
    assert r["status"] == "CRIT"
    base = {"heads": {"frappe": "a" * 40, "erpnext": "b" * 40}, "targets": "active active active", "hub_backup_age_h": 1.0, "hotel_business_date": "2020-01-01"}
    r = checks.evaluate(hotel, base, inv, cat)
    assert any(x["code"] == "night-audit" and x["level"] == "CRIT" for x in r["findings"])
    r = checks.evaluate(hotel, dict(base, hotel_business_date=""), inv, cat)
    assert any(x["code"] == "night-audit" and x["level"] == "WARN" for x in r["findings"])


def test_collect_pull_parses_the_probe_through_the_fake_transport(scratch, fake):
    inv = config.load_inventory()
    cat = config.load_catalog()
    raw = checks.collect_pull(_box(inv, "box-002"), cat)
    assert raw["heads"]["frappe"] == "a" * 40
    assert raw["targets"] == "active active active" and raw["disk_free_gb"] == "40" and raw["open_shifts"] == "1"
    assert raw["probes"][0]["code"] == "200" and raw["tls_days_left"] == 60
    assert raw["domain_days_left"] is None          # no RDAP answer in the tests
    assert all(c[1] == "box-002" for c in fake.calls if c[0] == "run_on")


def test_collect_pull_sends_the_shared_probe_with_its_parameters(scratch, fake):
    inv = config.load_inventory()
    box = _box(inv, "box-002")
    checks.collect_pull(box, config.load_catalog())
    cmd = next(c[2] for c in fake.calls if c[0] == "run_on" and "targets=" in c[2])
    assert cmd.startswith(f"B={box['bench']}; S={box['site']}; SINCE=") and "TARGETS=" in cmd and "UNITS=" in cmd
    assert cmd.split("\n", 1)[1] == checks.PROBE.read_text()     # the file itself, not a copy of it


def test_the_probe_script_yields_every_metric_the_check_reads(tmp_path):
    bench = tmp_path / "bench"
    (bench / "sites" / "s.test").mkdir(parents=True)
    (bench / "sites" / "s.test" / "site_config.json").write_text(json.dumps({"db_name": "_x", "maintenance_mode": 1}))
    env = {**os.environ, "B": str(bench), "S": "s.test", "SINCE": "2026-01-01 00:00:00", "TARGETS": "no-such.target", "UNITS": "no-such.service"}
    out = subprocess.run(["bash", str(checks.PROBE)], capture_output=True, text=True, timeout=60, env=env).stdout
    raw = checks.parse_probe(out, ["no-such.service"])
    assert set(checks.PROBE_KEYS) <= set(raw) and raw["maintenance_mode"] == "1"
    assert all(raw[k].isdigit() for k in ("disk_free_gb", "ram_avail_mb", "uptime_s", "failed_units"))
    assert raw["box_backup_age_h"] == "?" and raw["errorlog_rows_today"] == "?"      # no backups, no database: unknown, not wrong
    assert raw["kernel_running"] and raw["reboot_required"] in ("0", "1")
    assert "no-such.service" in raw["monitor_units"]


def _agent():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("fleet_agent", str(config.SCRIPTS / "fleet-agent"))
    spec = importlib.util.spec_from_loader("fleet_agent", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_the_agent_reads_the_probe_as_the_hub_does(tmp_path):
    agent = _agent()
    assert agent.PROBE == checks.PROBE                              # beside the script, in the repository as on a box
    from conftest import GOOD_PROBE
    hub = checks.parse_probe(GOOD_PROBE[1], [])
    hub.pop("monitor_units")
    assert {k: v for k, v in agent.parse_probe(GOOD_PROBE[1]).items() if k != "units"} == hub
    bench = tmp_path / "bench"
    (bench / "sites" / "s.test").mkdir(parents=True)
    (bench / "sites" / "s.test" / "site_config.json").write_text(json.dumps({"db_name": "_x"}))
    raw = agent.collect({"BENCH": str(bench), "SITE": "s.test"})
    assert raw["agent"] == "fleet-agent/2" and set(checks.PROBE_KEYS) <= set(raw) and raw["heads"] == {}
    assert raw["probes"][0]["service"] == "erp"


def test_check_box_end_to_end_and_the_history(scratch, fake):
    inv = config.load_inventory()
    cat = config.load_catalog()
    r = checks.check_box(_box(inv, "box-003"), inv, cat)
    assert r["status"] == "OK", r["problems"]
    checks.write_check(r)
    latest = json.loads((scratch / "state" / "checks" / "box-003" / "latest.json").read_text())
    assert latest["id"] == "box-003" and latest["connectivity"] == "tailnet" and "tls_days_left" not in latest


def test_cmd_check_runs_every_box_and_writes_the_digest(scratch, fake, capsys):
    rc = cli.main(["check", "--all", "--no-push"])
    out = capsys.readouterr().out
    assert rc == 0 and "CHECK-OK" in out
    digest = (scratch / "state" / "latest-digest.txt").read_text()
    assert digest.startswith("Fleet ") and "3 ok of 3" in digest and "box-003 hotel" in digest
    assert sorted(p.name for p in (scratch / "state" / "checks").iterdir()) == ["box-001", "box-002", "box-003"]
    hosts = {c[1] for c in fake.calls if c[0] == "run_on"}
    assert hosts == {"box-001", "box-002", "box-003"}


def test_cmd_check_strict_exits_one_on_a_critical_box(scratch, fake, capsys):
    fake.hosts["shop.example.test"]["run"] = {"rev-parse HEAD": (255, "ssh: connect to host shop.example.test port 22: No route")}
    assert cli.main(["check", "shop", "--no-push"]) == 0            # a finding is a finding, not a failed run
    assert cli.main(["check", "shop", "--no-push", "--strict"]) == 1
    assert "unreachable" in capsys.readouterr().out


def test_status_and_list_read_only(scratch, fake, capsys):
    cli.main(["check", "--all", "--no-push"])
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"box-002\s+shop\s+shop.example.test", out) and "OK" in out
    assert cli.main(["list"]) == 0
    assert "A customer shop" in capsys.readouterr().out


def test_digest_lists_findings(scratch):
    inv = config.load_inventory()
    chk = {"box-002": {"status": "WARN", "disk_free_gb": "10", "hub_backup_age_h": 5, "errorlog_rows_today": "0", "open_shifts": "0",
                       "findings": [{"level": "WARN", "code": "disk", "message": "disk free 10 GB"}]}}
    text = checks.build_digest(inv, chk)
    assert "- WARN box-002 shop (A customer shop): disk 10G" in text and "· WARN: disk free 10 GB" in text and "never checked" in text


def test_copy_drift_names_the_files_that_differ_or_are_missing_or_left_over(tmp_path):
    repo, sbin, lib = tmp_path / "scripts", tmp_path / "sbin", tmp_path / "lib"
    for d in (repo / "fleet_tool", sbin, lib / "fleet_tool"):
        d.mkdir(parents=True)
    (repo / "fleet").write_text("#!stub v2\n")
    (sbin / "fleet").write_text("#!stub v1\n")
    (repo / "fleet_tool" / "cli.py").write_text("same\n")
    (lib / "fleet_tool" / "cli.py").write_text("same\n")
    (repo / "fleet_tool" / "new.py").write_text("new module\n")
    (lib / "fleet_tool" / "stale.py").write_text("gone from the repository\n")
    assert checks.copy_drift(repo, sbin, lib) == ["fleet", "fleet_tool/new.py", "fleet_tool/stale.py"]
    (sbin / "fleet").write_text("#!stub v2\n")
    (lib / "fleet_tool" / "new.py").write_text("new module\n")
    (lib / "fleet_tool" / "stale.py").unlink()
    assert checks.copy_drift(repo, sbin, lib) == []


def test_installed_copy_drift_is_silent_when_the_tool_runs_from_the_repository():
    assert checks.installed_copy_drift() == []       # the tests import the package from the repository


def test_digest_carries_the_hub_notes_before_the_boxes(scratch):
    inv = config.load_inventory()
    text = checks.build_digest(inv, {}, ["hub: the installed fleet tool differs from the repository (fleet): run scripts/fleet-install/install-fleet.sh"])
    head, _, rest = text.partition("- ?? ")
    assert "· WARN: hub: the installed fleet tool differs" in head and "install-fleet.sh" in head and "never checked" in rest


def _script_bench_run(fake, host, log_prefix, final_line):
    """Teach the fake what a followed bench run looks like on a box: the box's date, the log's
    length before the run, the unit start, and a tail that ends with the script's verdict."""
    run = fake.hosts[host]["run"]
    run["date +%F"] = (0, "2026-10-28")
    run["wc -l <"] = (0, "4")
    run["systemd-run"] = (0, "")
    run[f"tail -n +5 /root/{log_prefix}-2026-10-28.log"] = (0, "step 3\n" + final_line)


def test_upgrade_follows_the_log_then_checks_the_box(scratch, fake, capsys, monkeypatch):
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    _script_bench_run(fake, "shop.example.test", "bench-upgrade", "UPGRADE-OK 2026.10-r1")
    assert cli.main(["upgrade", "shop", "--now"]) == 0
    out = capsys.readouterr().out
    assert "release: 2026.10-r1" in out and "UPGRADE-OK" in out and "post-upgrade check OK" in out
    copies = [c for c in fake.calls if c[0] == "scp_to"]
    assert [c[3] for c in copies] == ["/root/bench-upgrade.sh", "/root/bench-upgrade.manifest"] and copies[1][4] == "0644"
    started = next(c[2] for c in fake.calls if c[0] == "run_on" and "systemd-run" in c[2])
    assert "--setenv=MANIFEST=/root/bench-upgrade.manifest" in started and "bench-upgrade.sh" in started
    assert "upgrade box-002 release=2026.10-r1 preflight=False result=ok" in (scratch / "state" / "actions.log").read_text()
    assert json.loads((scratch / "state" / "checks" / "box-002" / "latest.json").read_text())["status"] == "OK"


def test_upgrade_stops_the_rollout_at_the_first_failed_box(scratch, fake, capsys, monkeypatch):
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    _script_bench_run(fake, "shop.example.test", "bench-upgrade", "UPGRADE-FAILED: migrate")
    assert cli.main(["upgrade", "shop", "hotel", "--now"]) == 1
    out = capsys.readouterr().out
    assert "UPGRADE-FAILED box-002" in out and "stopping the rollout" in out
    assert not [c for c in fake.calls if c[0] in ("run_on", "scp_to") and c[1] == "box-003"]   # the canary failed: nothing reached the next box
    assert "result=FAILED" in (scratch / "state" / "actions.log").read_text()


def test_upgrade_waits_for_the_window_and_skips_a_suspended_box(scratch, fake, capsys, monkeypatch):
    monkeypatch.setattr(upgrade, "in_window", lambda b: False)
    assert cli.main(["upgrade", "shop"]) == 1
    assert "outside its maintenance window" in capsys.readouterr().out
    assert not [c for c in fake.calls if c[0] in ("run_on", "scp_to")]
    inv = config.load_inventory()
    lifecycle.set_status(inv, _box(inv, "box-002"), "suspended")
    assert cli.main(["upgrade", "shop", "--preflight"]) == 0
    assert "skipped (suspended)" in capsys.readouterr().out and not fake.calls


def test_upgrade_refuses_an_allow_discard_that_is_not_a_plain_pattern(scratch, fake, capsys):
    with pytest.raises(SystemExit, match="plain regular expression"):
        cli.main(["upgrade", "shop", "--now", "--allow-discard", "erpnext; rm -rf /"])
    assert not [c for c in fake.calls if c[0] == "run_on" and "systemd-run" in c[2]]


def test_rollback_dry_run_prints_the_plan_and_a_real_one_is_followed(scratch, fake, capsys, monkeypatch):
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    run = fake.hosts["shop.example.test"]["run"]
    run["DRY_RUN=1"] = (0, "would reset frappe to aaaa\nROLLBACK-PLAN-OK")
    assert cli.main(["rollback", "shop", "--dry-run"]) == 0
    assert "ROLLBACK-PLAN-OK" in capsys.readouterr().out
    assert not [c for c in fake.calls if c[0] == "run_on" and "systemd-run" in c[2]]
    _script_bench_run(fake, "shop.example.test", "bench-rollback", "ROLLBACK-OK")
    assert cli.main(["rollback", "shop", "--now", "--no-restore"]) == 0
    out = capsys.readouterr().out
    assert "rollback of the last upgrade (code only)" in out and "post-rollback check OK" in out
    started = next(c[2] for c in fake.calls if c[0] == "run_on" and "systemd-run" in c[2])
    assert "--setenv=RESTORE=0" in started and "bench-rollback.sh" in started
    assert "rollback box-002 release=last-upgrade restore=False result=ok" in (scratch / "state" / "actions.log").read_text()


def test_rollback_refuses_an_offline_box_and_the_window(scratch, fake, capsys, monkeypatch):
    inv = config.load_inventory()
    monkeypatch.setattr(upgrade, "in_window", lambda b: False)
    with pytest.raises(SystemExit, match="outside its maintenance window"):
        cli.main(["rollback", "shop"])
    lifecycle.set_field(inv, _box(inv, "box-003"), "connectivity", "offline")
    with pytest.raises(SystemExit, match="rolls back from its own console"):
        cli.main(["rollback", "hotel", "--now"])
    assert not fake.calls


def test_suspend_and_resume_flip_the_maintenance_page_and_the_status(scratch, fake, capsys):
    run = fake.hosts["shop.example.test"]["run"]
    run["set-maintenance-mode on"] = (0, "")
    run["set-maintenance-mode off"] = (0, "")
    assert cli.main(["suspend", "shop", "--reason", "unpaid"]) == 0
    assert "suspended (maintenance page on)" in capsys.readouterr().out
    box = _box(config.load_inventory(), "box-002")
    assert box["status"] == "suspended" and "suspended_for" not in box
    cmd = next(c[2] for c in fake.calls if c[0] == "run_on" and "set-maintenance-mode on" in c[2])
    assert "sudo -u frappe" in cmd and "clear-cache" in cmd and "restart frappe-bench-frappe-web" in cmd
    assert cli.main(["resume", "shop"]) == 0
    assert _box(config.load_inventory(), "box-002")["status"] == "active"
    log = (scratch / "state" / "actions.log").read_text()
    assert "suspend box-002 reason='unpaid' cause=operator" in log and "resume box-002" in log


def test_suspend_reports_a_box_that_refused_the_command(scratch, fake, capsys):
    fake.hosts["shop.example.test"]["run"]["set-maintenance-mode on"] = (1, "bench: site not found")
    assert cli.main(["suspend", "shop"]) == 1
    assert "site not found" in capsys.readouterr().out
    assert _box(config.load_inventory(), "box-002")["status"] == "active"


def test_dunning_moves_a_box_through_grace_suspension_and_back(scratch, fake):
    inv = config.load_inventory()
    run = fake.hosts["shop.example.test"]["run"]
    run["set-maintenance-mode on"] = (0, "")
    run["set-maintenance-mode off"] = (0, "")
    overdue = {"box-002": {"overdue_days": 5}}
    assert lifecycle.apply_dunning(inv, overdue, apply=False) == ["box-002 shop: overdue 5 d -> grace"]
    assert _box(inv, "box-002")["status"] == "grace"
    lines = lifecycle.apply_dunning(inv, {"box-002": {"overdue_days": 20}}, apply=False)
    assert lines == ["box-002 shop: 20 d past due, grace 14 d over -> would suspend (fleet dunning --apply)"]
    assert _box(inv, "box-002")["status"] == "grace" and not fake.calls
    lines = lifecycle.apply_dunning(inv, {"box-002": {"overdue_days": 20}}, apply=True)
    assert lines == ["box-002 shop: box-002 shop suspended (maintenance page on). Reason: unpaid 20 d past due, grace 14 d"]
    box = _box(inv, "box-002")
    assert box["status"] == "suspended" and box["suspended_for"] == "unpaid"
    assert lifecycle.apply_dunning(inv, {"box-002": {"overdue_days": 0}}, apply=True) == ["box-002 shop: paid -> box-002 shop resumed"]
    assert _box(inv, "box-002")["status"] == "active"
    lifecycle.apply_dunning(inv, {"box-001": {"overdue_days": 99}}, apply=True)
    assert _box(inv, "box-001")["status"] == "active"      # our own boxes never follow the money


def test_offboard_takes_the_final_backup_and_marks_the_box(scratch, fake, capsys):
    fake.hosts["shop.example.test"]["run"]["backup --with-files"] = (0, "")
    assert cli.main(["offboard", "shop"]) == 0
    out = capsys.readouterr().out
    assert "marked offboarded" in out and "bench drop-site site.local --force" in out
    copies = [c for c in fake.calls if c[0] == "rsync"]
    assert copies[0][1] == "root@shop.example.test:/home/frappe/frappe-bench/sites/site.local/private/backups/"
    dest = next((scratch / "offboard" / "box-002").iterdir())
    assert "A customer shop" in (dest / "HANDOVER.txt").read_text()
    assert _box(config.load_inventory(), "box-002")["status"] == "offboarded"
    with pytest.raises(SystemExit, match="refusing to offboard"):
        cli.main(["offboard", "hubby"])


def test_ssh_sessions_share_one_connection_per_box(scratch):
    opts = transport.ssh_options()
    assert "ControlMaster=auto" in opts and "ControlPersist=60" in opts and "BatchMode=yes" in opts
    path = opts[opts.index("ControlMaster=auto") + 2]
    assert path.startswith("ControlPath=") and path.endswith("/%C")
    sockets = scratch / "state" / "ssh"
    assert sockets.is_dir() and oct(sockets.stat().st_mode & 0o777) == "0o700"


def test_help_names_every_command(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for cmd in cli.COMMANDS:
        assert cmd in out
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "--help"])
    assert e.value.code == 0 and "--strict" in capsys.readouterr().out


def test_mutating_commands_name_their_targets(scratch, fake, capsys):
    with pytest.raises(SystemExit, match="name the box"):
        cli.main(["upgrade"])
    assert not [c for c in fake.calls if c[0] in ("scp_to", "run_on")]


def test_suspend_refuses_our_own_box(scratch, fake, capsys):
    assert cli.main(["suspend", "box-001", "--reason", "test"]) == 1
    assert "refusing" in capsys.readouterr().out
    assert config.load_inventory()["boxes"][0]["status"] == "active"


def test_environment_is_the_scratch_one():
    assert os.environ["FLEET_NO_NETWORK"] == "1"
    assert not str(config.INVENTORY).startswith("/etc/")


def test_frozen_reads_the_windows_in_box_local_dates():
    import datetime as dt
    box = {"id": "box-009", "timezone": "UTC", "freeze_windows": [{"from": "2026-12-20", "to": "2027-01-05", "reason": "high season"}]}
    assert config.frozen(box, dt.date(2026, 12, 25)) == "frozen until 2027-01-05 (high season)"
    assert config.frozen(box, dt.date(2027, 1, 5)) and config.frozen(box, dt.date(2027, 1, 6)) is None
    assert config.frozen({"id": "x"}, dt.date(2026, 12, 25)) is None
    with pytest.raises(SystemExit):
        config.frozen({"id": "x", "freeze_windows": [{"from": "tomorrow"}]}, dt.date(2026, 12, 25))


def test_rollout_skips_a_frozen_box_and_reboot_refuses_it(scratch, fake, capsys, monkeypatch):
    monkeypatch.setattr(upgrade, "in_window", lambda b: True)
    inv = config.load_inventory()
    _box(inv, "box-002")["freeze_windows"] = [{"from": "2000-01-01", "to": "2999-12-31", "reason": "month-end"}]
    config.save_inventory(inv)
    assert "freeze_windows" in config.INVENTORY.read_text()                     # a plain inventory key, kept on save
    assert cli.main(["upgrade", "shop"]) == 0
    out = capsys.readouterr().out
    assert "skipped, frozen until 2999-12-31 (month-end)" in out and "UPGRADE-OK (frozen, to catch up: box-002)" in out
    assert not [c for c in fake.calls if c[0] in ("run_on", "scp_to")]
    assert cli.main(["list"]) == 0 and "[frozen until 2999-12-31 (month-end)]" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="frozen until 2999-12-31"):
        cli.main(["reboot", "shop"])
    assert not [c for c in fake.calls if c[0] == "run_on"]


def test_the_agent_honours_its_freeze_windows():
    import datetime as dt
    agent = _agent()
    c = {"TIMEZONE": "UTC", "FREEZE_WINDOWS": "2026-12-20..2027-01-05 2027-02-27..2027-03-02"}
    assert agent.frozen(c, dt.date(2027, 3, 1)) == "frozen until 2027-03-02"
    assert agent.frozen(c, dt.date(2027, 3, 3)) is None and agent.frozen({"FREEZE_WINDOWS": ""}, dt.date(2027, 3, 1)) is None
    from fleet_tool import deploy
    assert deploy.freeze_windows_text({"freeze_windows": [{"from": "2026-12-20", "to": "2027-01-05"}]}) == "2026-12-20..2027-01-05"
    assert deploy.freeze_windows_text({}) == ""


# ---- the lease (one lifecycle for every box) ------------------------------------------------

@pytest.fixture
def lease_key(scratch, monkeypatch):
    """A scratch signing key for the hub side; the hub's own lease lands in a scratch etc."""
    from fleet_tool import lease
    monkeypatch.setattr(lease, "LEASE_KEY", scratch / "keys" / "fleet-lease.key")
    monkeypatch.setattr(lease, "LOCAL_ETC", scratch / "etc")
    assert cli.main(["lease", "keygen"]) == 0
    assert oct(lease.LEASE_KEY.stat().st_mode)[-3:] == "600" and lease.pub_path().exists()
    return lease


def test_lease_state_ladder_agrees_between_hub_and_agent():
    import datetime as dt
    from fleet_tool import lease
    agent = _agent()
    le = {"expires": "2026-12-01", "grace_days": 15}
    for day, want in (("2026-11-01", "valid"), ("2026-11-16", "valid"), ("2026-11-17", "notice"), ("2026-12-01", "notice"),
                      ("2026-12-02", "restricted"), ("2026-12-16", "restricted"), ("2026-12-17", "readonly")):
        today = dt.date.fromisoformat(day)
        assert lease.lease_state(le, today)[0] == want == agent.lease_state(le, today)[0], day


def test_lease_issue_signs_delivers_and_the_agent_verifies(lease_key, fake, capsys, scratch, monkeypatch):
    lease = lease_key
    with pytest.raises(SystemExit, match="exists"):
        cli.main(["lease", "keygen"])                                    # a second keygen would orphan every lease
    assert cli.main(["lease", "issue", "shop"]) == 0
    out = capsys.readouterr().out
    assert "ladder on" in out and "grace 14 d" in out                 # a customer's box: the ladder; grace from the inventory
    sent = [c for c in fake.calls if c[0] == "scp_to" and c[1] == "box-002"]
    assert [c[3] for c in sent] == ["/etc/iiab/fleet-lease.json", "/etc/iiab/fleet-lease.pub"]
    assert any(c[0] == "run_on" and "fleet-agent lease" in c[2] for c in fake.calls)
    doc = lease.stored("box-002")
    assert doc["lease"]["box"] == "box-002" and doc["lease"]["ladder"] is True and lease.verify(doc, lease.pub_path())
    agent = _agent()
    assert agent.verify(doc, lease.pub_path())
    doc["lease"]["expires"] = "2999-01-01"                               # tampered: the signature no longer matches
    assert not agent.verify(doc, lease.pub_path())
    assert cli.main(["lease", "issue", "hubby", "--days", "7"]) == 0   # our own box: the ladder off, installed locally
    assert "ladder off" in capsys.readouterr().out and (scratch / "etc" / "fleet-lease.json").exists()
    assert cli.main(["lease", "status"]) == 0
    out = capsys.readouterr().out
    assert "box-002  shop" in out and "valid (+30 d)" in out and "no lease issued" in out
    assert cli.main(["lease", "show", "shop"]) == 0 and "paste line:" in capsys.readouterr().out


def test_lease_issue_is_gated_by_the_obligations_and_revoke_is_immediate(lease_key, fake, capsys):
    lease = lease_key
    from fleet_tool import state
    inv = config.load_inventory()
    box = _box(inv, "box-002")
    state.write_check({"id": "box-002", "checked_at": "2026-10-29T00:00:00+00:00", "status": "CRIT",
                       "findings": [{"level": "CRIT", "code": "unpaid", "message": "unpaid 20 d past due"}]})
    with pytest.raises(SystemExit, match="does not meet its obligations"):
        cli.main(["lease", "issue", "shop"])
    assert cli.main(["lease", "issue", "shop", "--anyway"]) == 0
    assert cli.main(["lease", "revoke", "shop", "--reason", "offboarding"]) == 0
    assert "revoked" in capsys.readouterr().out
    assert lease.lease_state(lease.stored("box-002")["lease"], lease.today_for(box))[0] == "readonly"
    assert cli.main(["lease", "issue", "hotel"]) == 0                 # tailnet box: 30 d lease, still delivered over ssh
    fake.hosts["shop.example.test"]["run"]["fleet-lease.timer"] = (0, "lease: unleased")
    assert cli.main(["lease", "deploy", "shop"]) == 0
    units = [c[3] for c in fake.calls if c[0] == "scp_to" and c[1] == "box-002" and "systemd" in c[3]]
    assert units == ["/etc/systemd/system/fleet-lease.service", "/etc/systemd/system/fleet-lease.timer"]


def test_the_agent_applies_the_ladder_only_when_told_and_only_undoes_its_own(lease_key, tmp_path, monkeypatch, capsys):
    import datetime as dt
    lease = lease_key
    agent = _agent()
    monkeypatch.setattr(agent, "STATE", tmp_path / "state")
    monkeypatch.setattr(agent, "LEASE", tmp_path / "fleet-lease.json")
    monkeypatch.setattr(agent, "LEASE_PUB", lease.pub_path())
    monkeypatch.setattr(agent, "LEASE_NGINX", tmp_path / "fleet-lease.conf")
    ran = []
    monkeypatch.setattr(agent, "sh", lambda cmd: ran.append(cmd) or "")
    monkeypatch.setattr(agent, "nginx_reload", lambda: True)
    c = {"FLEET_BOX_ID": "box-002", "TIMEZONE": "UTC", "BENCH": "/home/frappe/frappe-bench", "SITE": "site.local"}
    assert agent.lease(c, dt.date(2026, 11, 1)) == 0                   # no lease file yet
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["state"] == "unleased" and not st["enforced"] and (tmp_path / "state" / "lease-clock").read_text().strip() == "2026-11-01"
    doc = lease.sign({"v": 1, "box": "box-002", "issued": "2026-11-01", "expires": "2026-12-01", "grace_days": 15, "ladder": True})
    agent.LEASE.write_text(json.dumps(doc))
    assert agent.lease(c, dt.date(2026, 11, 20)) == 0
    assert json.loads((tmp_path / "state" / "lease-state.json").read_text())["state"] == "notice" and not ran
    assert agent.lease(c, dt.date(2026, 12, 5)) == 0                   # expired, inside the grace: the desk pauses, no bench call
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["state"] == "restricted" and st["enforced"] == "restricted" and "return 503" in agent.LEASE_NGINX.read_text() and not ran
    assert agent.lease(c, dt.date(2026, 12, 20)) == 0                  # past the grace: maintenance mode, the pause file emptied
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["state"] == "readonly" and st["enforced"] == "readonly" and agent.LEASE_NGINX.read_text() == ""
    assert len(ran) == 1 and "set-maintenance-mode on" in ran[0]
    assert agent.lease(c, dt.date(2026, 12, 18)) == 0                  # the clock went back: recorded, nothing bypassed
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["clock"] == "back" and st["enforced"] == "readonly"
    doc = lease.sign({"v": 1, "box": "box-002", "issued": "2026-12-20", "expires": "2027-01-19", "grace_days": 15, "ladder": True})
    agent.LEASE.write_text(json.dumps(doc))
    assert agent.lease(c, dt.date(2026, 12, 21)) == 0                  # renewed: only what the agent set is undone
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["state"] == "valid" and st["enforced"] == "" and "set-maintenance-mode off" in ran[-1]
    other = lease.sign({"v": 1, "box": "box-003", "issued": "2026-12-20", "expires": "2027-01-19", "grace_days": 15, "ladder": True})
    agent.LEASE.write_text(json.dumps(other))
    assert agent.lease(c, dt.date(2026, 12, 22)) == 0                  # another box's lease: invalid, and the ladder is not run on it
    assert json.loads((tmp_path / "state" / "lease-state.json").read_text())["state"] == "invalid"
    ladder_off = lease.sign({"v": 1, "box": "box-002", "issued": "2026-01-01", "expires": "2026-02-01", "grace_days": 15, "ladder": False})
    agent.LEASE.write_text(json.dumps(ladder_off))
    before = len(ran)
    assert agent.lease(c, dt.date(2026, 12, 23)) == 0
    st = json.loads((tmp_path / "state" / "lease-state.json").read_text())
    assert st["state"] == "readonly" and st["enforced"] == "" and len(ran) == before   # our own box: state only


def test_check_judges_the_lease_the_probe_reports(scratch):
    inv = config.load_inventory()
    catalog = config.load_catalog()
    box = _box(inv, "box-002")
    from conftest import GOOD_PROBE
    for line, level, needle in (("lease=notice 2026-12-01 - ok", "WARN", "lease ends 2026-12-01"),
                                ("lease=restricted 2026-12-01 restricted ok", "CRIT", "the till sells"),
                                ("lease=restricted 2026-12-01 - ok", "CRIT", "not enforced"),
                                ("lease=readonly 2026-12-01 readonly back", "CRIT", "maintenance mode")):
        raw = checks.parse_probe(GOOD_PROBE[1].replace("lease=", line), [])
        raw["heads"] = {}
        res = checks.evaluate(box, raw, inv, catalog)
        hits = [f for f in res["findings"] if f["code"] == "lease"]
        assert hits and hits[0]["level"] == level and needle in hits[0]["message"], line
        if "back" in line:
            assert any("clock went backwards" in f["message"] for f in hits)
    raw = checks.parse_probe(GOOD_PROBE[1], [])
    raw["heads"] = {}
    assert not [f for f in checks.evaluate(box, raw, inv, catalog)["findings"] if f["code"] == "lease"]
