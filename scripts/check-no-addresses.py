#!/usr/bin/env python3
"""The fork's own files carry no real address, hostname of the fleet, business name or phone
number: public repositories must not identify the estate. Private ranges, loopback, netmasks,
the tailnet's CGNAT range, the two public resolvers used as examples and version-like tokens
are not addresses in that sense.

    check-no-addresses.py ROLE... (role names) and DIR/FILE...   exit 1 with the lines found
"""
import re, sys
from pathlib import Path

IPV4 = re.compile(r"(?<![\w.-])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")
ALLOWED = re.compile(r"^(127\.|0\.0\.0\.0$|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|255\.|100\.64\.0\.0$|8\.8\.8\.8$|1\.1\.1\.1$)")
NAMES = re.compile(r"novadah|\+509 ?\d{4} ?\d{4}|\b(garantie\.boutique|novadah\.net|malianova\.com|winndixie\.me|resistanceenergy\.app|michocell\.com)\b", re.I)
SUFFIXES = {".yml", ".yaml", ".j2", ".md", ".sh", ".txt", ".conf", ".py", ".service", ".timer", ".json"}
SKIP = ("demo-hotel-seed.py", "check-no-addresses.py")   # fictional guests with phone-shaped numbers; this script's own patterns

def files(args):
    for a in args:
        p = Path(a)
        if not p.exists():
            p = Path("roles") / a
        if p.is_file():
            yield p
        else:
            for f in p.rglob("*"):
                if f.is_file() and (f.suffix in SUFFIXES or f.name in ("fleet", "fleet-agent", "fleet-kuma-seed", "fleet-hub-watchdog", "fleet-mirror")):
                    yield f

bad = []
for f in files(sys.argv[1:]):
    if f.name in SKIP:
        continue
    try:
        text = f.read_text(errors="replace")
    except OSError:
        continue
    for n, line in enumerate(text.splitlines(), 1):
        if re.search(r"firmware|version", line, re.I):   # dotted version numbers look like addresses
            continue
        for m in IPV4.finditer(line):
            if all(int(x) <= 255 for x in m.groups()) and not ALLOWED.match(m.group(0)):
                bad.append(f"{f}:{n}: address {m.group(0)}")
        if NAMES.search(line):
            bad.append(f"{f}:{n}: name or number")
for b in bad:
    print(b)
print("ADDRESS-SCAN-" + ("FAIL" if bad else "OK"))
sys.exit(1 if bad else 0)
