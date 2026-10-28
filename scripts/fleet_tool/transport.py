"""The one way the tool leaves this machine: ssh and scp to a box, rsync, curl, a TLS handshake, an
RDAP lookup, the tailnet client, a local program. Everything else in the package calls these
module-level functions, which hand over to `backend`. The tests replace `backend` with a fake
that answers from a script and raises on any host it was not given; the real backend refuses to
run at all while FLEET_NO_NETWORK is set, so a test that forgot the fake cannot reach a box."""
from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import socket
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path


class Real:
    """What the hub does for real."""

    def _guard(self, what: str) -> None:
        if os.environ.get("FLEET_NO_NETWORK"):
            raise RuntimeError(f"fleet: {what} refused: FLEET_NO_NETWORK is set (a test without the fake transport)")

    def run_on(self, box: dict, cmd: str, timeout: int = 120) -> tuple[int, str]:
        self._guard(f"ssh to {box.get('id') or box.get('reach', {}).get('ssh')}")
        if box.get("local"):
            argv = ["bash", "-c", cmd]
        else:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{box['ssh_user']}@{box['reach']['ssh']}", cmd]
        for attempt in (1, 2):
            try:
                p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return 124, "timeout"
            if p.returncode == 255 and not box.get("local") and attempt == 1:
                time.sleep(3)   # ssh itself failed (a blip, a key exchange, a busy sshd): one more try
                continue
            break
        # a success returns stdout alone: an ssh warning on stderr must never end up in parsed output
        return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()

    def scp_to(self, box: dict, src: Path, dest: str, mode: str = "0755") -> None:
        self._guard(f"scp to {box.get('id')}")
        if box.get("local"):
            subprocess.run(["install", "-m", mode, str(src), dest], check=True)
        else:
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(src), f"{box['ssh_user']}@{box['reach']['ssh']}:{dest}"], check=True)
            self.run_on(box, f"chmod {mode} {shlex.quote(dest)}")

    def rsync(self, src: str, dest: str, delete: bool = False) -> tuple[int, str]:
        """rsync over ssh (or locally); (rc, stderr)."""
        self._guard(f"rsync from {src}")
        argv = ["rsync", "-a"] + (["--delete"] if delete else []) + ["-e", "ssh -o BatchMode=yes", src, dest]
        r = subprocess.run(argv, capture_output=True, text=True)
        return r.returncode, r.stderr.strip()

    def http_code(self, url: str, timeout: int = 20) -> str:
        self._guard(f"http to {url}")
        try:
            return subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", str(timeout), "-w", "%{http_code}", "-L", url],
                                  capture_output=True, text=True, timeout=timeout + 5).stdout.strip() or "000"
        except subprocess.TimeoutExpired:
            return "000"

    def tls_days_left(self, host: str) -> int | None:
        self._guard(f"tls handshake with {host}")
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
            exp = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
            return (exp - dt.datetime.now(dt.timezone.utc)).days
        except Exception:
            return None

    def rdap_fetch(self, url: str) -> dict | None:
        """One RDAP document, or None when that registry server does not answer."""
        self._guard(f"rdap lookup at {url}")
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/rdap+json", "User-Agent": "fleet-check"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            return None

    def local_addresses(self) -> set[str]:
        """Names and addresses this machine answers to (its tailnet address included)."""
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
        return set(out) | {socket.gethostname(), socket.getfqdn(), "localhost", "127.0.0.1"}

    def tailscale_status(self) -> tuple[int, str, str]:
        self._guard("tailscale status")
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def run_local(self, argv: list[str]) -> tuple[int, str, str]:
        """A program on this machine (the monitor seeder, systemctl); (rc, stdout, stderr)."""
        self._guard(f"local program {argv[0]}")
        r = subprocess.run(argv, capture_output=True, text=True, check=False)
        return r.returncode, r.stdout, r.stderr


backend = Real()


def run_on(box: dict, cmd: str, timeout: int = 120) -> tuple[int, str]:
    return backend.run_on(box, cmd, timeout)


def scp_to(box: dict, src: Path, dest: str, mode: str = "0755") -> None:
    backend.scp_to(box, src, dest, mode)


def rsync(src: str, dest: str, delete: bool = False) -> tuple[int, str]:
    return backend.rsync(src, dest, delete)


def http_code(url: str, timeout: int = 20) -> str:
    return backend.http_code(url, timeout)


def tls_days_left(host: str) -> int | None:
    return backend.tls_days_left(host)


def rdap_fetch(url: str) -> dict | None:
    return backend.rdap_fetch(url)


def local_addresses() -> set[str]:
    return backend.local_addresses()


def tailscale_status() -> tuple[int, str, str]:
    return backend.tailscale_status()


def run_local(argv: list[str]) -> tuple[int, str, str]:
    return backend.run_local(argv)


def bench(box: dict, args: str) -> str:
    """The shell line that runs a bench command as the bench user on a box."""
    return f"sudo -u frappe -H bash -lc {shlex.quote(f'cd {box['bench']} && bench {args}')}"


def box_http_code(box: dict, url: str, timeout: int = 20) -> str:
    """HTTP status as the box itself sees it: for services that only listen on its loopback (the
    hub-kind probes), which the machine running the check cannot reach once the hub and the box
    that carries the service are no longer the same computer."""
    rc, out = run_on(box, f"curl -s -o /dev/null -m {timeout} -w '%{{http_code}}' -L {shlex.quote(url)}", timeout=timeout + 15)
    code = out.strip()[-3:]
    return code if rc == 0 and code.isdigit() else "000"
