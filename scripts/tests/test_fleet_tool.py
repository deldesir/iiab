"""The fleet tool against a scratch inventory and a fake transport. No test reaches a real box:
the fake raises on any host it was not given, and the real transport refuses to run under
FLEET_NO_NETWORK (conftest.py)."""
import json
import os
import re

import pytest

from fleet_tool import checks, cli, config, releases, site, transport


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
