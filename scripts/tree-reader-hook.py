#!/usr/bin/env python3
"""PreToolUse hook for a tree reader (`TREE_READER_TOOLS`: the satisfied probe
and the delivery judge), wired by `claude_p` through `--settings`
(`_tree_reader_hook_settings` in orchestrator/leerie.py; DESIGN §8 *A
base-tree reader has the verbs a reader reaches for*).

1. `cd <the worker's own cwd>` (`&&` or `;` after it, repeated) is stripped
   from the front of a Bash command before the CLI matches it against the
   allow list: the hook returns the stripped command as `updatedInput`.
2. `git grep` may search the working tree, the index or HEAD only: a
   positional argument that names any other commit or tree (`git rev-parse
   --verify <arg>^{tree}` resolves and it is not HEAD) is denied.
Fails open on any error (the allow list still stands). Every decision is
appended to $CLAUDE_CONFIG_DIR/tree-reader-hook.ndjson (the worker's own
directory).

The logic is the measured one (bench LB re-run 3, arm RO+) unchanged: only
this docstring and the log's file name differ from it."""
import glob, json, os, re, shlex, subprocess, sys

GREP_VALUE_OPTS = {'-e', '-f', '-A', '-B', '-C', '-m', '--max-count', '--max-depth', '--threads',
                   '--after-context', '--before-context', '--context', '-O', '--open-files-in-pager'}
SEP = re.compile(r'\s*(&&|;)\s*')


def _resolves_to(p: str, cwd: str) -> bool:
    p = p.strip()
    try:
        w = shlex.split(p)
    except ValueError:
        return False
    if len(w) != 1:
        return False
    hits = glob.glob(w[0]) if any(c in w[0] for c in '*?[') else [w[0]]
    return len(hits) == 1 and os.path.realpath(os.path.join(cwd, hits[0])) == os.path.realpath(cwd)


def strip_cd(cmd: str, cwd: str) -> str:
    out = cmd
    while True:
        m = re.match(r'\s*cd\s+((?:"[^"]*"|\'[^\']*\'|[^\s;&|])+)\s*(&&|;)\s*', out)
        if not m or not _resolves_to(m.group(1), cwd):
            return out
        out = out[m.end():]


def grep_foreign_refs(cmd: str, cwd: str) -> list[str]:
    bad = []
    for seg in re.split(r'\|\||&&|;|\|', cmd):
        try:
            w = shlex.split(seg)
        except ValueError:
            continue
        if len(w) < 2 or w[0] != 'git' or w[1] != 'grep':
            continue
        args = w[2:]
        has_e = any(a in ('-e', '-f') or a.startswith(('-e', '--regexp', '--file')) for a in args)
        pos, i, seen_pattern = [], 0, has_e
        while i < len(args):
            a = args[i]
            if a == '--':
                break
            if a.startswith('-'):
                if a in GREP_VALUE_OPTS:
                    i += 1
                i += 1
                continue
            if not seen_pattern:
                seen_pattern = True
            else:
                pos.append(a)
            i += 1
        for a in pos:
            if a == 'HEAD' or a.startswith('HEAD:'):
                continue
            r = subprocess.run(['git', 'rev-parse', '--verify', '-q', a.split(':', 1)[0] + '^{tree}'],
                               cwd=cwd, capture_output=True, text=True)
            if r.returncode == 0:
                bad.append(a)
    return bad


def main() -> int:
    try:
        ev = json.load(sys.stdin)
        if ev.get('tool_name') != 'Bash':
            return 0
        ti = dict(ev.get('tool_input') or {})
        cmd = str(ti.get('command') or '')
        cwd = os.environ.get('CLAUDE_PROJECT_DIR') or ev.get('cwd') or os.getcwd()
        new = strip_cd(cmd, cwd)
        bad = grep_foreign_refs(new, ev.get('cwd') or cwd)
        out = None
        if bad:
            out = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                   'permissionDecisionReason': 'git grep may search only the working tree or HEAD in this session '
                                               f'(not {", ".join(bad)})'}}
        elif new != cmd:
            ti['command'] = new
            out = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'updatedInput': ti}}
        logd = os.environ.get('CLAUDE_CONFIG_DIR')
        if logd and os.path.isdir(logd):
            with open(os.path.join(logd, 'tree-reader-hook.ndjson'), 'a') as f:
                f.write(json.dumps({'cmd': cmd[:400], 'new': new[:400] if new != cmd else None, 'deny': bad}) + '\n')
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
