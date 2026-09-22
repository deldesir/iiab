#!/usr/bin/env python3
"""Render a rapidpro role template with the variable precedence Ansible uses.

    render-rapidpro-settings.py <template.j2> <out> [--compare <existing settings>]

Layers, lowest to highest: roles/rapidpro/defaults/main.yml < vars/default_vars.yml
< /etc/iiab/local_vars.yml. Nested "{{ }}" references are resolved lazily and
lookup('password', '<path> ...') reads the file like Ansible does.

Why: the ai-update role once blanked every RapidPro secret because an
include_vars of the role defaults outranked local_vars.yml. Rendering here, with
the correct precedence, lets an operator check the result BEFORE anything is
installed or restarted. Nothing secret is ever printed: --compare reports, for
each *_KEY/*_TOKEN/*_SECRET/*_PASSWORD assignment, only the value lengths and
whether they match, then a unified diff of the remaining lines with secret
values masked. The output file is created 0600.
"""
import difflib, os, re, sys

import jinja2, yaml

IIAB = os.environ.get("IIAB_REPO", "/opt/iiab/iiab")
LAYERS = [f"{IIAB}/roles/rapidpro/defaults/main.yml", f"{IIAB}/vars/default_vars.yml",
          "/etc/iiab/local_vars.yml"]
SECRET = re.compile(r'^(\s*)([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*(.*)$')


def load_vars():
    V = {}
    for f in LAYERS:
        if os.path.exists(f):
            with open(f) as fh:
                V.update(yaml.safe_load(fh) or {})
    return V


def lookup(kind, spec, *a, **k):
    if kind == "password":          # "<path> chars=... length=..." -> the stored value
        with open(spec.split()[0]) as fh:
            return fh.read().strip()
    raise NotImplementedError(f"lookup({kind!r}) is not supported")


def make_env():
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True)  # as Ansible's template module
    env.globals["lookup"] = lookup
    env.filters["bool"] = lambda v: str(v).lower() in ("1", "true", "yes", "on")
    return env


def refs(text):
    """Variable-looking names inside the {{ }} / {% %} of a template string."""
    return set(re.findall(r"\b([a-z][a-z0-9_]+)\b", " ".join(re.findall(r"\{[{%].*?[}%]\}", text, re.S))))


def resolve(env, V, name, depth=0):
    """Value of a layered variable; nested {{ refs }} are rendered on demand."""
    v = V[name]
    while isinstance(v, str) and ("{{" in v or "{%" in v) and depth < 10:
        ctx = {k: resolve(env, V, k, depth + 1) for k in refs(v) if k in V}
        v = env.from_string(v).render(**ctx); depth += 1
    return v


def render(tmpl):
    env, V = make_env(), load_vars()
    with open(tmpl) as fh:
        src = fh.read()
    ctx, unresolved = {}, []
    for n in sorted(refs(src) & set(V)):
        try: ctx[n] = resolve(env, V, n)
        except Exception as e: unresolved.append(f"{n} ({type(e).__name__})")
    if unresolved:
        print("  unresolved: " + ", ".join(unresolved))
    out = env.from_string(src).render(**ctx)
    return out if out.endswith("\n") else out + "\n", sorted(ctx)


def write_0600(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def secrets_of(lines):
    found = {}
    for ln in lines:
        m = SECRET.match(ln)
        if m: found[m.group(2)] = m.group(3).strip().strip('"\'')
    return found


def mask(ln):
    m = SECRET.match(ln)
    return f"{m.group(1)}{m.group(2)} = <masked>" if m else ln


def compare(new_text, existing):
    with open(existing) as fh:
        old_text = fh.read()
    new, old = new_text.splitlines(), old_text.splitlines()
    ns, os_ = secrets_of(new), secrets_of(old)
    print("  secrets (lengths only):")
    for k in sorted(set(ns) | set(os_)):
        a, b = ns.get(k), os_.get(k)
        state = "SAME" if a == b else ("MISSING in new" if a is None else "MISSING in existing" if b is None else "DIFFERENT")
        print(f"    {k:<40} new={len(a) if a is not None else '-':>4} existing={len(b) if b is not None else '-':>4}  {state}")
    diff = list(difflib.unified_diff([mask(l) for l in old], [mask(l) for l in new],
                                     fromfile=existing, tofile="rendered", lineterm=""))
    print("  non-secret diff: " + ("none" if not diff else f"{len(diff)} lines"))
    for ln in diff: print("    " + ln)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (2, 4) or (len(args) == 4 and args[2] != "--compare"):
        sys.exit(f"usage: {os.path.basename(sys.argv[0])} <template.j2> <out> [--compare <existing>]")
    text, used = render(args[0])
    write_0600(args[1], text)
    print(f"  rendered {os.path.basename(args[0])} -> {args[1]} ({len(text)} bytes, 0600); vars used: {len(used)}")
    if len(args) == 4: compare(text, args[3])
