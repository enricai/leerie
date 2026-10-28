// Mechanical coupling facts for ONE test case, from the TypeScript AST of its
// file (DESIGN §8 *Delivery detection*; bench n2d/Q2 facts.cjs, unchanged
// except where `typescript` is loaded from).
// Usage: node test-case-facts.cjs <file> <case fullName or title> -> JSON on stdout
// Reachable code = the case callback + enclosing describe hooks + every
// file-level/describe-level declaration transitively referenced from those.
// Facts are syntactic only; the policy lives in leerie.py
// (`_falsifier_coupling`). `typescript` comes from load-typescript.cjs: the
// installed tree under test's own (resolved from the working directory) when
// it has the compiler API, else the image's pinned copy.
const ts = require('./load-typescript.cjs').loadTypescript().ts;
const fs = require('fs');
const [file, want] = process.argv.slice(2);
const text = fs.readFileSync(file, 'utf8');
const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
  file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

const CASE_FNS = new Set(['it', 'test']);
const SUITE_FNS = new Set(['describe']);
const HOOKS = new Set(['beforeEach', 'beforeAll', 'afterEach', 'afterAll']);
function calleeRoot(e) { // it / it.each(...) / test.skip / it.concurrent
  while (true) {
    if (ts.isIdentifier(e)) return e.text;
    if (ts.isPropertyAccessExpression(e)) { e = e.expression; continue; }
    if (ts.isCallExpression(e)) { e = e.expression; continue; }
    return null;
  }
}
function strOf(n) {
  if (!n) return null;
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
  if (ts.isTemplateExpression(n)) return n.head.text + n.templateSpans.map(s => '${}' + s.literal.text).join('');
  return null;
}
function lastFn(args) { for (let i = args.length - 1; i >= 0; i--) if (ts.isArrowFunction(args[i]) || ts.isFunctionExpression(args[i])) return args[i]; return null; }

// collect cases with their ancestor suites
const cases = [];
function walk(n, suites) {
  if (ts.isCallExpression(n)) {
    const r = calleeRoot(n.expression);
    if (CASE_FNS.has(r)) { const t = strOf(n.arguments[0]); if (t != null) cases.push({ title: t, node: n, suites: suites.slice() }); }
    if (SUITE_FNS.has(r)) { const fn = lastFn(n.arguments); if (fn) { suites.push(fn); ts.forEachChild(fn.body, c => walk(c, suites)); suites.pop(); return; } }
  }
  ts.forEachChild(n, c => walk(c, suites));
}
walk(sf, []);
function titleMatches(t, full) {
  if (full === t || full.endsWith(' ' + t) ) return t.length;
  if (t.includes('%') || t.includes('${}') || t.includes('$')) { // parametrised title: compare the literal prefix tagged part
    const head = t.split(/%[sdifjo#]|\$\{\}|\$[a-zA-Z_]+/)[0];
    if (head.length > 8 && full.includes(head)) return head.length;
  }
  return 0;
}
let best = null, bl = 0;
for (const c of cases) { const l = titleMatches(c.title, want); if (l > bl) { bl = l; best = c; } }
if (!best) { console.log(JSON.stringify({ error: 'case_not_found', titles: cases.map(c => c.title).slice(0, 50) })); process.exit(0); }

// declarations available: top-level statements + each enclosing suite body statements
const scopes = [sf.statements, ...best.suites.map(s => s.body.statements || [])];
const decls = new Map(); // name -> node
const importBind = new Map(); // local name -> {spec, imported, typeOnly}
const imports = [];
for (const st of sf.statements) {
  if (ts.isImportDeclaration(st)) {
    const spec = st.moduleSpecifier.text; const cl = st.importClause;
    imports.push(spec);
    if (!cl) continue;
    const tOnly = !!cl.isTypeOnly;
    if (cl.name) importBind.set(cl.name.text, { spec, imported: 'default', typeOnly: tOnly });
    const nb = cl.namedBindings;
    if (nb && ts.isNamespaceImport(nb)) importBind.set(nb.name.text, { spec, imported: '*', typeOnly: tOnly });
    if (nb && ts.isNamedImports(nb)) for (const e of nb.elements)
      importBind.set(e.name.text, { spec, imported: (e.propertyName || e.name).text, typeOnly: tOnly || !!e.isTypeOnly });
  }
}
const roots = [best.node];
for (const sc of scopes) for (const st of sc) {
  if (ts.isFunctionDeclaration(st) && st.name) decls.set(st.name.text, st);
  else if (ts.isVariableStatement(st)) for (const d of st.declarationList.declarations) {
    const add = (b) => { if (ts.isIdentifier(b)) decls.set(b.text, d); else if (b.elements) b.elements.forEach(e => e.name && add(e.name)); };
    add(d.name);
  } else if (ts.isClassDeclaration(st) && st.name) decls.set(st.name.text, st);
  else if (ts.isExpressionStatement(st) && ts.isCallExpression(st.expression) && HOOKS.has(calleeRoot(st.expression.expression))) roots.push(st);
}
// transitive closure
const reach = new Set(roots); const queue = roots.slice(); const usedNames = new Set();
while (queue.length) {
  const n = queue.pop();
  (function v(x) {
    if (ts.isIdentifier(x)) { usedNames.add(x.text); const d = decls.get(x.text); if (d && !reach.has(d)) { reach.add(d); queue.push(d); } }
    ts.forEachChild(x, v);
  })(n);
}
// facts over reachable code
const strLits = []; const reads = []; const execs = []; const evals = []; const dyn = [];
function litsIn(n, depth = 0) { // string literals in an expression, resolving const identifiers
  const out = [];
  (function v(x) {
    const s = strOf(x); if (s != null) out.push(s);
    if (ts.isIdentifier(x) && depth < 4) { const d = decls.get(x.text); if (d && d.initializer) out.push(...litsIn(d.initializer, depth + 1)); }
    ts.forEachChild(x, v);
  })(n);
  return out;
}
const READ_FNS = new Set(['readFileSync', 'readFile', 'createReadStream']);
const EXEC_FNS = new Set(['execSync', 'exec', 'execFileSync', 'execFile', 'spawnSync', 'spawn', 'execa', '$']);
for (const r of reach) (function v(x) {
  if (ts.isCallExpression(x) || ts.isNewExpression(x)) {
    const e = x.expression; const name = ts.isIdentifier(e) ? e.text : (ts.isPropertyAccessExpression(e) ? e.name.text : null);
    const args = x.arguments || [];
    if (name && READ_FNS.has(name)) reads.push({ fn: name, lits: args.length ? litsIn(args[0]) : [], line: sf.getLineAndCharacterOfPosition(x.getStart()).line + 1 });
    if (name === 'file' && ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.expression) && e.expression.text === 'Bun') reads.push({ fn: 'Bun.file', lits: args.length ? litsIn(args[0]) : [] });
    if (name && EXEC_FNS.has(name)) execs.push({ fn: name, lits: args.flatMap(a => litsIn(a)) });
    if (name === 'eval' || (name === 'Function' && (ts.isNewExpression(x) || ts.isIdentifier(e)))) evals.push(name);
    if (name && /^(transpileModule|transpile|transformSync|transform|runInNewContext|runInThisContext|runInContext|compileFunction)$/.test(name)) evals.push(name);
    if (ts.isCallExpression(x) && e.kind === ts.SyntaxKind.ImportKeyword) {
      const s = strOf(args[0]); if (s != null && !s.includes('${}')) dyn.push(s); else evals.push('dynamic_import_nonliteral');
    }
    if (ts.isIdentifier(e) && e.text === 'require' && args.length) { const s = strOf(args[0]); if (s != null) dyn.push(s); else evals.push('require_nonliteral'); }
  }
  ts.forEachChild(x, v);
})(r);
const usedImports = [];
for (const [local, b] of importBind) if (usedNames.has(local)) usedImports.push({ local, ...b });
for (const s of dyn) usedImports.push({ local: null, spec: s, imported: '*dynamic', typeOnly: false });
const reach_src = [...reach].map(n => n.getText(sf));
console.log(JSON.stringify({ title: best.title, reach_src, case_src: best.node.getText(sf), reads, execs, evals, imports: usedImports,
  vm_import: imports.some(s => s === 'vm' || s === 'node:vm'), all_imports: imports }));
