"""supply-check (roles/ai-gateway/files): the parsing and the severity policy, no box reached."""
import importlib.machinery
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load():
    loader = importlib.machinery.SourceFileLoader("supply_check", str(ROOT / "roles" / "ai-gateway" / "files" / "supply-check"))
    spec = importlib.util.spec_from_loader("supply_check", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_forks_table_parses_and_names_every_pin_role():
    sc = load()
    rows = sc.load_forks(ROOT / "scripts" / "forks.tsv")
    names = {r["name"] for r in rows}
    assert {"courier", "mailroom", "wuzapi", "frappe", "gateway"} <= names
    for r in rows:
        assert r["kind"] in {"go", "python", "frappe", "js", "ansible", "own", "own-aux"}, r
        if r["pin"] != "-":
            role, _, var = r["pin"].partition(":")
            assert (ROOT / "roles" / role / "defaults" / "main.yml").exists(), r["pin"]
            assert var.endswith("_release"), r["pin"]
        assert r["sync"] in {"branch", "tag", "-"}, r
        if r["kind"].startswith("own"):
            assert r["upstream"] == "-" and r["sync"] == "-"
        else:
            assert "/" in r["upstream"]


def test_govulncheck_stream_and_policy():
    sc = load()
    stream = "\n".join(json.dumps(o) for o in [
        {"config": {"protocol_version": "v1.0.0"}},
        {"osv": {"id": "GO-1", "summary": "stdlib thing"}},
        {"osv": {"id": "GO-2", "summary": "module thing"}},
        {"osv": {"id": "GO-3", "summary": "unreleased fix"}},
        {"finding": {"osv": "GO-1", "fixed_version": "v1.27.2", "trace": [{"module": "stdlib", "version": "v1.27.0"}]}},
        {"finding": {"osv": "GO-2", "fixed_version": "v0.62.0", "trace": [{"module": "golang.org/x/net", "version": "v0.60.0"}]}},
        {"finding": {"osv": "GO-2", "fixed_version": "v0.62.0", "trace": [{"module": "golang.org/x/net", "version": "v0.60.0"}]}},
        {"finding": {"osv": "GO-3", "fixed_version": "v0.58.1-0.20261009181718-2f1f834330b2", "trace": [{"module": "golang.org/x/crypto", "version": "v0.58.0"}]}},
    ])
    rows, osvs = sc.parse_govulncheck(stream)
    assert len(rows) == 3 and osvs["GO-2"] == "module thing"
    assert sc.go_level("stdlib", "v1.27.2")[0] == "CRIT"
    assert sc.go_level("golang.org/x/net", "v0.62.0")[0] == "CRIT"
    assert sc.go_level("golang.org/x/crypto", "v0.58.1-0.20261009181718-2f1f834330b2")[0] == "WARN"
    assert sc.go_level("golang.org/x/crypto", "")[0] == "WARN"


def test_pip_audit_rows_and_accept_file(tmp_path):
    sc = load()
    data = {"dependencies": [
        {"name": "certifi", "version": "2026.6.17", "vulns": [{"id": "PYSEC-2026-4320", "fix_versions": ["2026.10.18"], "aliases": ["GHSA-x"]}]},
        {"name": "chromadb", "version": "1.5.9", "vulns": [{"id": "PYSEC-2026-311", "fix_versions": [], "aliases": []}]},
        {"name": "fastapi", "version": "0.128.0", "vulns": []},
    ]}
    rows = sc.pip_audit_rows(data)
    assert [(r[0], bool(r[3])) for r in rows] == [("certifi", True), ("chromadb", False)]
    accept = tmp_path / "accept"
    accept.write_text("# tolerated\nGHSA-x   # embedded use only\n\nnpm:builder-cli:ws # dev server\n")
    ids = sc.load_accept(accept)
    assert ids == {"GHSA-x": "embedded use only", "npm:builder-cli:ws": "dev server"}
    assert sc.load_accept(tmp_path / "missing") == {}


def test_python_eol_and_versions():
    sc = load()
    import datetime as dt
    today = dt.date(2026, 11, 7)
    assert "end of life" in sc.python_eol("3.10.20", today)
    assert sc.python_eol("3.14.5", today) == ""
    assert sc.python_eol("4.0.0", today) == ""
    assert sc.version_tuple("v26.3.74") > sc.version_tuple("v26.3.68-1")
    assert sc.version_tuple("v1.0.9") < sc.version_tuple("v1.0.19")


def test_report_renders_crit_summary_last():
    sc = load()
    import datetime as dt
    r = sc.Report()
    r.add("OK", "go", "courier: fine")
    r.add("CRIT", "python", "gateway: x -> 1.2")
    r.add("WARN", "github", "frappe: 44 behind")
    text = sc.render(r, "box", dt.datetime(2026, 11, 7, 8, 30, tzinfo=dt.timezone.utc), 12.0)
    assert text.index("[go]") < text.index("[python]") < text.index("[github]")
    assert text.rstrip().splitlines()[-2].startswith("summary: 1 CRIT, 1 WARN, 1 OK, 0 SKIP")
    assert text.rstrip().splitlines()[-1] == "  CRIT [python] gateway: x -> 1.2"
    assert r.count("CRIT") == 1


def test_grouping_and_version_order():
    sc = load()
    assert sc.newest(["2.14.0", "2.15.0", "2.9.9"]) == "2.15.0"
    assert sc.newest(["go1.26.9", "go1.26.10"]) == "go1.26.10"
    assert sc.newest([]) == ""
    rows = {("GO-1", "stdlib", "go1.26.0"): "go1.26.2", ("GO-2", "stdlib", "go1.26.0"): "go1.26.10", ("GO-3", "golang.org/x/net", "v0.59.0"): "v0.62.0"}
    g = sc.group_go(rows)
    assert set(g) == {("stdlib", "go1.26.0"), ("golang.org/x/net", "v0.59.0")} and sc.newest(g[("stdlib", "go1.26.0")].values()) == "go1.26.10"
    py = sc.group_python([("pyjwt", "2.13.0", "PYSEC-1", ["2.14.0"], []), ("pyjwt", "2.13.0", "PYSEC-1", ["2.14.0"], []), ("pyjwt", "2.13.0", "PYSEC-2", ["2.15.0"], ["GHSA-a"])])
    assert list(py) == [("pyjwt", "2.13.0")] and len(py[("pyjwt", "2.13.0")]) == 2
    assert sc.ids_text(["a", "b", "c", "d", "e", "f"]) == "a, b, c, d, +2 more"


def test_drift_reads_github_compare_from_the_fork_side():
    sc = load()
    # courier, 2026-11-07: upstream had 24 commits the fork lacked; the fork carried 65 of its own
    assert sc.drift({"ahead_by": 24, "behind_by": 65, "status": "diverged"}) == (24, 65)
    assert sc.drift({}) == (0, 0)


def test_main_gives_a_system_unit_a_home(tmp_path, monkeypatch):
    """A system unit has no HOME, and gh then reads no login: main defaults it to root's."""
    sc = load()
    seen = {}
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(sc, "check_go", lambda report, accept: seen.setdefault("home", sc.os.environ.get("HOME")))
    rc = sc.main(["--only", "go", "--state-dir", str(tmp_path), "--accept", str(tmp_path / "none"),
                  "--forks", str(tmp_path / "none.tsv")])
    assert rc == 0
    assert seen["home"] == "/root"
    assert (tmp_path / "last.txt").is_file()
