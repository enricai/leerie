#!/bin/bash
# The real shim's `--replace` / `--remove` in a real held-out sandbox, inside
# a leerie repository image (tests/test_base_form_in_place_real_overlay.py).
# Runs as leerie in the nested user namespace container-entry.sh's rootless
# branch starts the orchestrator in. Prints PASS/FAIL lines; exits non-zero
# on any FAIL.
set -u
SHIM=${SHIM:-/mc/scripts/landlock_exec.py}
W=/leerie-state/work
rm -rf "$W"; mkdir -p "$W"; cd "$W"
id
fail=0
ok()  { echo "PASS: $*"; }
bad() { echo "FAIL: $*"; fail=1; }
G="git -c user.email=t@x -c user.name=t"
DEV=(--rw /dev/null --rw /dev/zero --rw /dev/full --rw /dev/random --rw /dev/urandom --rw /dev/tty --rw /dev/shm); WR=(); [ -d /opt/leerie-worker-root ] && WR=(--worker-root /opt/leerie-worker-root)

# A real git tree: a final-form test, a file the run added, a vitest setup.
mk_tree() {
  local t=$1; mkdir -p "$t/src"; cd "$t"; git init -q -b main
  mkdir -p node_modules/.bin
  ln -s /mc/node/node_modules/vitest node_modules/vitest
  printf '{"type":"module"}\n' > package.json
  printf 'node_modules/.vite/\nnode_modules/.vitest/\n' > .gitignore
  printf 'export const fmt = (n) => n.toFixed(1);\n' > src/fmt.js
  printf "import { expect, test } from 'vitest';\nimport { fmt } from './fmt.js';\ntest('formats', () => { expect(fmt(2)).toBe('2.0'); });\n" > src/fmt.test.js
  printf "import { test } from 'vitest';\ntest('added', () => {});\n" > src/added.test.js
  $G add -A >/dev/null; $G commit -qm final; cd "$W"
}
run_shim() {  # $1 tree, rest: shim options then -- cmd
  local t=$1; shift
  exec 7>"$W/taken.txt" 8>"$W/refusal.txt"
  ( cd "$t" && /usr/bin/python3 -I -S "$SHIM" --refusal-fd 8 --sandbox --view "$t" "$@" ) 7>&7 8>&8
  local rc=$?
  exec 7>&- 8>&-
  return $rc
}

mk_tree "$W/t1"
BASE="$W/base.test.js"
printf "import { expect, test } from 'vitest';\nimport { fmt } from './fmt.js';\ntest('formats two places', () => { expect(fmt(2)).toBe('2.00'); });\n" > "$BASE"
before_sha=$(cd "$W/t1" && git hash-object src/fmt.test.js)

# 1. --replace and --remove, observed from inside the sandbox.
out=$(run_shim "$W/t1" --taken-fd 7 --replace "$BASE=src/fmt.test.js" \
      --remove src/added.test.js "${WR[@]}" "${DEV[@]}" --ro / -- \
      /bin/sh -c 'cat src/fmt.test.js; echo "--- added:"; ls src/added.test.js 2>&1; echo "--- status:"; git status --porcelain')
rc=$?
echo "$out"
[ $rc -eq 0 ] && ok "sandbox ran (rc 0)" || bad "sandbox rc $rc: $(cat $W/refusal.txt)"
echo "$out" | grep -q "toBe('2.00')" && ok "--replace: the base form is at the file's path inside" || bad "--replace not seen inside"
echo "$out" | grep -q "No such file" && ok "--remove: the added file is gone inside (whiteout)" || bad "--remove not seen inside"
echo "$out" | grep -q "^ M src/fmt.test.js" && echo "$out" | grep -q "^ D src/added.test.js" && ok "git inside sees the replacement as M and the removal as D" || bad "git status inside unexpected"
# Outside, afterwards: untouched.
[ "$(cd $W/t1 && git hash-object src/fmt.test.js)" = "$before_sha" ] && ok "outside: the final form untouched" || bad "outside: final form changed"
[ -f "$W/t1/src/added.test.js" ] && ok "outside: the removed file is still there" || bad "outside: removed file gone"
[ -z "$(cd $W/t1 && git status --porcelain)" ] && ok "outside: tree clean" || bad "outside: tree dirty: $(cd $W/t1 && git status --porcelain)"

# 2. A link at either path, or on the way, is a taken path, never a refusal.
for shape in file_link dir_link; do
  for op in replace remove; do
    t="$W/t-$shape-$op"; mk_tree "$t"; cd "$t"
    if [ $shape = file_link ]; then
      rm src/fmt.test.js; ln -s /leerie-state/outside.js src/fmt.test.js; rel=src/fmt.test.js
    else
      mkdir -p /leerie-state/outdir; ln -s /leerie-state/outdir lnk; rel=lnk/fmt.test.js
    fi
    mkdir -p /leerie-state/outdir; printf 'outside\n' > /leerie-state/outside.js; printf 'outside\n' > /leerie-state/outdir/fmt.test.js
    $G add -A >/dev/null; $G commit -qm link; cd "$W"
    if [ $op = replace ]; then o=(--replace "$BASE=$rel"); else o=(--remove "$rel"); fi
    run_shim "$t" --taken-fd 7 "${o[@]}" "${WR[@]}" "${DEV[@]}" --ro / -- /bin/echo RAN > "$W/o.txt" 2>&1
    rc=$?
    if [ $rc -eq 126 ] && [ -s "$W/taken.txt" ] && [ ! -s "$W/refusal.txt" ] && ! grep -q RAN "$W/o.txt" \
       && [ "$(cat /leerie-state/outside.js)" = outside ] && [ "$(cat /leerie-state/outdir/fmt.test.js)" = outside ]; then
      ok "$op at a $shape: taken ($(head -c 120 $W/taken.txt)), nothing written through"
    else
      bad "$op at a $shape: rc $rc taken=[$(cat $W/taken.txt)] refusal=[$(cat $W/refusal.txt)] out=[$(cat $W/o.txt)]"
    fi
  done
done

# 3. A real vitest runs the base form in the sandbox.
NODE=/mc/nodebin/bin/node
VT=/mc/node/node_modules/vitest/vitest.mjs
cd "$W/t1"
run_shim "$W/t1" --taken-fd 7 "${WR[@]}" "${DEV[@]}" --ro / -- \
  "$NODE" "$VT" run src/fmt.test.js > "$W/v0.txt" 2>&1
rc0=$?
run_shim "$W/t1" --taken-fd 7 --replace "$BASE=src/fmt.test.js" --remove src/added.test.js \
  "${WR[@]}" "${DEV[@]}" --ro / -- "$NODE" "$VT" run src/fmt.test.js src/added.test.js > "$W/v1.txt" 2>&1
rc1=$?
cd "$W"
tail -5 "$W/v0.txt"; echo ...; grep -E "formats|expected|added|Test Files|Tests" "$W/v1.txt" | head -12
[ $rc0 -eq 0 ] && ok "vitest, final form: passes" || bad "vitest final form rc $rc0: $(tail -20 $W/v0.txt)"
[ $rc1 -ne 0 ] && grep -q "formats two places" "$W/v1.txt" && grep -q "2.00" "$W/v1.txt" \
  && ok "vitest, base form in place: the base case runs and fails (rc $rc1)" || bad "vitest base form rc $rc1: $(tail -30 $W/v1.txt)"
grep -q "No test files found\|added.test.js.*No such\|not found" "$W/v1.txt" || grep -q "added" "$W/v1.txt" && true
[ -z "$(cd $W/t1 && git status --porcelain)" ] && ok "outside after vitest: tree clean" || bad "dirty after vitest"
echo "RESULT: $([ $fail -eq 0 ] && echo ALL PASS || echo FAILURES)"
exit $fail
