"""usage: derive_runners.py <help-dir> <mocha-metadata.json> <out.json>

Derive each runner's and run-wrapper's value-taking options from its real
`--help` capture (files in <help-dir>), the way the pytest table was derived
from pytest's parser. Mocha's come from its own option metadata (types +
aliases), Jest's from yargs' type tags."""
import json
import re
import sys

HELP, MOCHA, OUT = sys.argv[1:4]
OPT = re.compile(r"(-{1,2}(?:\[no-\])?[A-Za-z0-9?][\w.-]*)(.*)", re.S)


def _spellings(tok: str) -> list[str]:
    if "[no-]" in tok:
        return [tok.replace("[no-]", ""), tok.replace("[no-]", "no-")]
    return [tok]


def generic(text: str, *, indent_required=True) -> dict:
    """Option lines: the option segment ends at the first run of 2+ spaces;
    a placeholder after any spelling in it (`<x>`, `[x]`, `X`, `=X`, a
    lowercase word as in go's `-run regexp`) makes every spelling
    value-taking; an attached optional (`[=x]`, `-W[x]`) or `...` does
    not."""
    value, flag = set(), set()
    for line in text.splitlines():
        if indent_required and not re.match(r"^\s+-", line):
            continue
        if not indent_required and not re.match(r"^-", line):
            continue
        seg = re.split(r"\s{2,}|\s*:\s", line.strip(), maxsplit=1)[0]
        specs = re.split(r",\s+|\s+/\s+", seg)
        takes = False
        names = []
        for sp in specs:
            sp = sp.strip()
            m = OPT.match(sp)
            if not m or sp in ("-", "--"):
                continue
            tok, rest = m.group(1), m.group(2)
            if "|" in tok:  # poetry's `-v|vv|vvv`
                tok = tok.split("|")[0]
            names += _spellings(tok)
            if not rest or rest == "..." or rest.startswith("[=") \
                    or (rest.startswith("[") and not tok.startswith("--")
                        and not rest.startswith("[<")):
                continue
            if rest.startswith(("=", " ", "\t")) or rest.startswith("[<"):
                takes = True
        (value if takes else flag).update(names)
    return {"value_opts": sorted(value), "flags": sorted(flag - value)}


def jest(text: str) -> dict:
    blocks, cur = [], None
    for line in text.splitlines():
        if re.match(r"^\s{2}(-\w, )?--\S|^\s{6}--\S", line):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)
    value, flag = set(), set()
    for b in blocks:
        head = b[0].strip().split("  ")[0]
        names = [n.strip() for n in head.split(",")]
        body = " ".join(x.strip() for x in b)
        tags = re.findall(r"\[(boolean|string|number|array|count|choices:)",
                          body)
        (flag if not tags or tags[-1] in ("boolean", "count")
         else value).update(names)
    return {"value_opts": sorted(value), "flags": sorted(flag - value)}


def mocha(meta: dict) -> dict:
    t, al = meta["types"], meta["aliases"]
    value, flag = set(), set()
    for kind, names in t.items():
        for n in names:
            sp = ["--" + n] + [("-" if len(a) == 1 else "--") + a
                               for a in al.get(n, [])]
            (flag if kind == "boolean" else value).update(sp)
    return {"value_opts": sorted(value), "flags": sorted(flag)}


def read(name: str) -> str:
    with open(f"{HELP}/{name}") as fh:
        return fh.read()


def merge(*ds: dict) -> dict:
    v = set().union(*(d["value_opts"] for d in ds))
    f = set().union(*(d["flags"] for d in ds)) - v
    return {"value_opts": sorted(v), "flags": sorted(f)}


out = {
    "vitest": generic(read("vitest_help_full.txt")),
    "jest": jest(read("jest_help.txt")),
    "mocha": mocha(json.load(open(MOCHA))),
    "go test": merge(generic(read("go_testflag.txt")),
                     generic(read("go_build.txt"))),
    "cargo test": generic(read("cargo_test.txt")),
    "libtest": generic(read("libtest.txt")),
    "rspec": generic(read("rspec.txt")),
    "rails test": generic(read("rails_test.txt")),
    "python": generic(read("python.txt"), indent_required=False),
    "node": generic(read("node.txt")),
    "uv": generic(read("uv.txt")),
    "uv run": generic(read("uv_run.txt")),
    "uvx": generic(read("uvx.txt")),
    "poetry run": generic(read("poetry_run.txt")),
    "pipenv": generic(read("pipenv.txt")),
    "pipenv run": generic(read("pipenv_run.txt")),
    "hatch": generic(read("hatch.txt")),
    "hatch run": generic(read("hatch_run.txt")),
    "pdm": generic(read("pdm.txt")),
    "pdm run": generic(read("pdm_run.txt")),
    "rye run": generic(read("rye_run.txt")),
    "bundle exec": generic(read("bundle_exec.txt")),
}
json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
for k, v in out.items():
    print(k, len(v["value_opts"]), len(v["flags"]))
