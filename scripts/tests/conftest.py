"""The fleet tool's tests run against a scratch inventory and a fake transport. The environment
below is set before the package is imported (its paths are read at import), and FLEET_NO_NETWORK
makes the real transport refuse everything: a test that forgets the fake cannot reach a box."""
import datetime as dt
import os
import sys
from pathlib import Path

import pytest

_SCRATCH = Path(os.environ.get("PYTEST_FLEET_SCRATCH") or "/tmp/fleet-tool-tests")   # noqa: S108  a per-run scratch tree, replaced below per test
_SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["FLEET_NO_NETWORK"] = "1"
for k, v in {"FLEET_INVENTORY": "fleet.yml", "FLEET_RELEASES": "releases", "FLEET_STATE": "state", "FLEET_PUSH_CONF": "fleet.conf", "FLEET_CATALOG": "catalog.yml", "FLEET_HUB_BACKUPS": "backups", "FLEET_OFFBOARD_DIR": "offboard"}.items():
    os.environ[k] = str(_SCRATCH / v)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fleet_tool import config, transport  # noqa: E402

INVENTORY = """# scratch inventory for the tests
defaults:
  ssh_user: root
  bench: /home/frappe/frappe-bench
  site: site.local
  timezone: America/Port-au-Prince
  maintenance_window: "20:00-05:00"
  plan: standard
  grace_days: 14
current_release: 2026.10-r1
boxes:
  - id: box-001
    alias: hubby
    host: hub.example.test
    business: Our own hub
    role: hub
    owner: self
    connectivity: public
    canary_rank: 1
    services: [erp]
    local: true
  - id: box-002
    alias: shop
    host: shop.example.test
    business: A customer shop
    role: till
    owner: customer
    connectivity: public
    canary_rank: 2
    services: [erp]
  - id: box-003
    alias: hotel
    host: hotel.example.test
    business: A hotel
    role: customer
    owner: customer
    connectivity: tailnet
    canary_rank: 3
    services: [erp, hospitality]
    reach: {ssh: hotel-tailnet.example.test}
"""

CATALOG = """services:
  erp:
    kind: box
    probes:
      - {path: /erp/api/method/ping, expect: 200}
  hospitality:
    kind: box
    probes:
      - {path: /erp/app/property, expect: 200, critical: false}
"""


class Fake(transport.Real):
    """A transport that answers from a script and raises on anything it was not told about.
    Keyed by the ssh address of a box: a command that reaches a host outside the table is the
    accident the tests exist to catch (an inventory copy once pointed the harness at a live box)."""

    def __init__(self, hosts: dict[str, dict]):
        self.hosts = hosts          # ssh address -> {"run": {substring: (rc, out)}, "http": {url: code}, "tls": days}
        self.calls: list[tuple] = []

    def _host(self, box: dict) -> dict:
        addr = (box.get("reach") or {}).get("ssh") or box.get("host")
        if addr not in self.hosts:
            raise RuntimeError(f"fake transport: {addr} is not a test host (refusing to reach it)")
        return self.hosts[addr]

    def run_on(self, box, cmd, timeout=120):
        h = self._host(box)
        self.calls.append(("run_on", box.get("id"), cmd))
        for key, answer in (h.get("run") or {}).items():
            if key in cmd:
                return answer
        return 1, f"fake: no answer scripted for: {cmd[:60]}"

    def scp_to(self, box, src, dest, mode="0755"):
        self._host(box)
        self.calls.append(("scp_to", box.get("id"), str(src), dest, mode))

    def rsync(self, src, dest, delete=False):
        self.calls.append(("rsync", src, dest, delete))
        return 0, ""

    def http_code(self, url, timeout=20):
        host = url.split("//", 1)[-1].split("/")[0]
        if host not in {k.split("//")[-1].split("/")[0] for k in self.hosts} and host not in self.hosts:
            raise RuntimeError(f"fake transport: {host} is not a test host (refusing to probe it)")
        self.calls.append(("http_code", url))
        for h in self.hosts.values():
            if url in (h.get("http") or {}):
                return h["http"][url]
        return "000"

    def tls_days_left(self, host):
        if host not in self.hosts:
            raise RuntimeError(f"fake transport: {host} is not a test host")
        return self.hosts[host].get("tls")

    def rdap_fetch(self, url):
        return None

    def local_addresses(self):
        return {"localhost", "127.0.0.1"}

    def tailscale_status(self):
        return 1, "", "no tailnet in the tests"

    def run_local(self, argv):
        self.calls.append(("run_local", list(argv)))
        return 0, "", ""


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A fresh inventory, catalog, state and release directory per test; the paths the package
    read at import are pointed at them."""
    (tmp_path / "fleet.yml").write_text(INVENTORY)
    (tmp_path / "catalog.yml").write_text(CATALOG)
    (tmp_path / "releases").mkdir()
    (tmp_path / "releases" / "2026.10-r1.manifest").write_text("release=2026.10-r1\ncaptured=2026-10-01T00:00:00Z\nfrappe=" + "a" * 40 + "\nerpnext=" + "b" * 40 + "\n")
    (tmp_path / "state").mkdir()
    for host in ("hub.example.test", "shop.example.test", "hotel.example.test"):     # a fresh backup copy per box
        d = tmp_path / "backups" / host / dt.date.today().isoformat()
        d.mkdir(parents=True)
        (d / f"{host}-database.sql.gz").write_bytes(b"")
    monkeypatch.setattr(config, "HUB_BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(config, "OFFBOARD_DIR", tmp_path / "offboard")
    monkeypatch.setattr(config, "INVENTORY", tmp_path / "fleet.yml")
    monkeypatch.setattr(config, "CATALOG", tmp_path / "catalog.yml")
    monkeypatch.setattr(config, "RELEASES", tmp_path / "releases")
    monkeypatch.setattr(config, "STATE", tmp_path / "state")
    monkeypatch.setattr(config, "PUSH_CONF", tmp_path / "fleet.conf")
    # modules that imported the names bind their own copies: point those too
    import fleet_tool.checks as checks, fleet_tool.lifecycle as lifecycle, fleet_tool.releases as releases, fleet_tool.site as site, fleet_tool.state as state, fleet_tool.transport as transport_, fleet_tool.upgrade as upgrade  # noqa: E401
    for mod in (checks, lifecycle, releases, site, state, transport_, upgrade):
        for name in ("INVENTORY", "CATALOG", "RELEASES", "STATE", "PUSH_CONF", "HUB_BACKUPS", "OFFBOARD_DIR"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, getattr(config, name))
    return tmp_path


GOOD_PROBE = (0, "\n".join([
    "targets=active active active", "failed_units=0", "failed_list=", "errorlog_rows_today=3", "open_shifts=1", "hotel_business_date=",
    "disk_free_gb=40", "ram_avail_mb=2000", "load1=0.2", "box_backup_age_h=5", "maintenance_mode=0", "uptime_s=900000",
    "reboot_required=0", "kernel_running=6.8.0-1", "kernel_newest=6.8.0-1", "units="]))
HEADS = (0, "frappe=" + "a" * 40 + "\nerpnext=" + "b" * 40)
HOTEL_PROBE = (0, GOOD_PROBE[1].replace("hotel_business_date=", "hotel_business_date=" + dt.date.today().isoformat()))


@pytest.fixture
def fake(monkeypatch):
    """The fake transport knowing the two remote test boxes and the local hub; installed as the backend."""
    f = Fake({
        "shop.example.test": {"run": {"rev-parse HEAD": HEADS, "targets=": GOOD_PROBE}, "http": {"https://shop.example.test/erp/api/method/ping": "200"}, "tls": 60},
        "hotel-tailnet.example.test": {"run": {"rev-parse HEAD": HEADS, "targets=": HOTEL_PROBE}, "http": {"http://hotel-tailnet.example.test/erp/api/method/ping": "200", "http://hotel-tailnet.example.test/erp/app/property": "200"}},
        "hub.example.test": {"run": {"rev-parse HEAD": HEADS, "targets=": GOOD_PROBE}, "http": {"https://hub.example.test/erp/api/method/ping": "200"}, "tls": 60},
    })
    monkeypatch.setattr(transport, "backend", f)
    return f
