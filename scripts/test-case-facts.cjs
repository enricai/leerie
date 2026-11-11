// Mechanical coupling facts for ONE test case, from the TypeScript AST of its
// file (DESIGN §8 *Delivery detection*; bench n2d/Q2 facts.cjs, unchanged
// except where `typescript` is loaded from and where its JSON is written
// (`[out]`)).
// Usage: node test-case-facts.cjs <file> <case fullName or title> [out]
//   -> JSON on stdout, or written to <out> (a held-out sandbox's report:
//   the probe it reads is placed in the sandbox, never on disk).
// Reachable code = the case callback + enclosing describe hooks + every
// file-level/describe-level declaration transitively referenced from those.
// Facts are syntactic only; the policy lives in leerie.py
// (`_falsifier_coupling`). `typescript` comes from load-typescript.cjs: the
// installed tree under test's own (resolved from the working directory) when
// it has the compiler API, else the image's pinned copy.
const ts = require('./load-typescript.cjs').loadTypescript().ts;
const fs = require('fs');
const [file, want, out] = process.argv.slice(2);
const emit = (o) => (out ? fs.writeFileSync(out, JSON.stringify(o))
                         : console.log(JSON.stringify(o)));
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
// A case's title as a template: one built with `+` is a template as a
// template literal is, its string operands literal text and every other
// operand a `${}` hole (a loop variable, a constant, a call: never resolved).
function titleOf(n) {
  const s = strOf(n); if (s != null) return s;
  if (n && ts.isParenthesizedExpression(n)) return titleOf(n.expression);
  if (n && ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.PlusToken)
    return (titleOf(n.left) ?? '${}') + (titleOf(n.right) ?? '${}');
  return null;
}
function lastFn(args) { for (let i = args.length - 1; i >= 0; i--) if (ts.isArrowFunction(args[i]) || ts.isFunctionExpression(args[i])) return args[i]; return null; }

// collect cases with their ancestor suites
const cases = [];
function walk(n, suites) {
  if (ts.isCallExpression(n)) {
    const r = calleeRoot(n.expression);
    if (CASE_FNS.has(r)) { const t = titleOf(n.arguments[0]); if (t != null) cases.push({ title: t, node: n, suites: suites.slice() }); }
    if (SUITE_FNS.has(r)) { const fn = lastFn(n.arguments); if (fn) { suites.push(fn); ts.forEachChild(fn.body, c => walk(c, suites)); suites.pop(); return; } }
  }
  ts.forEachChild(n, c => walk(c, suites));
}
walk(sf, []);
// A parametrised title (`it.each`, `test.each`, a template literal, a `+`
// concatenation) names every case it generates: its literal text must match
// exactly and in order, each placeholder any text (vitest/jest's printf `%s
// %d %i %f %j %o %O %p %c %# %$`, `%%` a literal `%`, a table's `$name` /
// `$name.path`, a template's or a concatenation's `${}`), anchored at the end of the runner's full name as a plain title is.
// Scored by its literal length, so the most specific title wins.
const PLACEHOLDER = /%%|%[sdifjoOpc#$]|\$\{\}|\$[A-Za-z_$#][\w$]*(?:\.[\w$]+)*/g;
function titleMatches(t, full) {
  if (full === t || full.endsWith(' ' + t) ) return t.length;
  let src = ''; let lit = 0; let last = 0; let params = 0;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const m of t.matchAll(PLACEHOLDER)) {
    const before = t.slice(last, m.index);
    src += esc(before); lit += before.length;
    if (m[0] === '%%') { src += '%'; lit += 1; } else { src += '[\\s\\S]*?'; params += 1; }
    last = m.index + m[0].length;
  }
  if (!params) return 0;
  src += esc(t.slice(last)); lit += t.length - last;
  return lit > 0 && new RegExp('(?:^|\\s)' + src + '$').test(full) ? lit : 0;
}
let best = null, bl = 0;
for (const c of cases) { const l = titleMatches(c.title, want); if (l > bl) { bl = l; best = c; } }
if (!best) { emit({ error: 'case_not_found', titles: cases.map(c => c.title).slice(0, 50) }); process.exit(0); }

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
// Where a path the case reads comes from (DESIGN §8 *Delivery detection*,
// R1): `P` when it names a file inside a directory the probe made fresh
// (`mkdtemp`/`mkdtempSync` from `fs`) joined only with plain segments, so
// the file was produced while the probe ran and was never one of the
// tree's. Traced through the probe's own bindings, each identifier resolved
// to its declaration by lexical scope, every value a binding is given having
// to agree (a greatest fixed point, so a recursive directory walk keeps its
// paths). Kinds: `A` an array of such paths, `N` one plain segment (a
// literal neither absolute nor holding `..`, or a directory entry's name),
// `NA` an array of segments (`readdir`), `T` not yet known, `B` anything
// else — whatever the trace does not follow, and a `B` read is judged as
// before.
const FS_MODS = new Set(['fs', 'node:fs', 'fs/promises', 'node:fs/promises']);
const PATH_MODS = new Set(['path', 'node:path', 'path/posix', 'node:path/posix']);
const unwrap = (e) => { while (e && (ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isNonNullExpression(e)
  || ts.isTypeAssertionExpression(e) || ts.isAwaitExpression(e) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(e)))) e = e.expression; return e; };
// --- lexical scopes: an identifier's binding is `<scope id>:<name>` ---
const isFnScope = (x) => ts.isFunctionLike(x) && !ts.isTypeNode(x) && !!x.body;
const scopeIds = new Map(); const scopeNames = new Map();
function patNames(b, into) {
  if (!b) return into;
  if (ts.isIdentifier(b)) into.push(b.text);
  else if (b.elements) b.elements.forEach(e => e.name && patNames(e.name, into));
  return into;
}
function varNames(body, into) { // `var` declarations hoisted to a function (or the file)
  (function v(y) {
    if (y !== body && isFnScope(y)) return;
    if (ts.isVariableDeclarationList(y) && !(y.flags & (ts.NodeFlags.Let | ts.NodeFlags.Const))) y.declarations.forEach(d => patNames(d.name, into));
    ts.forEachChild(y, v);
  })(body);
  return into;
}
function declared(s) {
  if (scopeNames.has(s)) return scopeNames.get(s);
  const out = [];
  const stmts = ts.isSourceFile(s) || ts.isBlock(s) || ts.isModuleBlock(s) ? s.statements
    : ts.isCaseBlock(s) ? s.clauses.flatMap(c => [...c.statements]) : null;
  if (stmts) for (const st of stmts) {
    if (ts.isVariableStatement(st) && (st.declarationList.flags & (ts.NodeFlags.Let | ts.NodeFlags.Const))) st.declarationList.declarations.forEach(d => patNames(d.name, out));
    else if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) out.push(st.name.text);
    else if (ts.isImportDeclaration(st) && st.importClause) {
      const cl = st.importClause; if (cl.name) out.push(cl.name.text);
      const nb = cl.namedBindings; if (nb && ts.isNamespaceImport(nb)) out.push(nb.name.text);
      if (nb && ts.isNamedImports(nb)) nb.elements.forEach(e => out.push(e.name.text));
    }
  }
  if (ts.isSourceFile(s)) varNames(s, out);
  if (isFnScope(s)) { s.parameters.forEach(p => patNames(p.name, out)); varNames(s.body, out); if (!ts.isFunctionDeclaration(s) && s.name && ts.isIdentifier(s.name)) out.push(s.name.text); }
  if ((ts.isForStatement(s) || ts.isForOfStatement(s) || ts.isForInStatement(s)) && s.initializer && ts.isVariableDeclarationList(s.initializer)
      && (s.initializer.flags & (ts.NodeFlags.Let | ts.NodeFlags.Const))) s.initializer.declarations.forEach(d => patNames(d.name, out));
  if (ts.isCatchClause(s) && s.variableDeclaration) patNames(s.variableDeclaration.name, out);
  const set = new Set(out); scopeNames.set(s, set); return set;
}
function keyOf(id) {
  for (let a = id.parent; a; a = a.parent) {
    if (declared(a).size && declared(a).has(id.text)) {
      if (!scopeIds.has(a)) scopeIds.set(a, scopeIds.size);
      return scopeIds.get(a) + ':' + id.text;
    }
  }
  return 'global:' + id.text;
}
// A module binding: an import (or a top-level `require`) of the file.
const modBind = new Map(); // key -> {spec, imported}
for (const [local, b] of importBind) for (const st of sf.statements) if (ts.isImportDeclaration(st)) {
  modBind.set(keyOf(Object.assign(ts.factory.createIdentifier(local), { parent: sf })), b); break;
}
for (const st of sf.statements) if (ts.isVariableStatement(st)) for (const d of st.declarationList.declarations) {
  const i = d.initializer && unwrap(d.initializer);
  if (ts.isIdentifier(d.name) && i && ts.isCallExpression(i) && ts.isIdentifier(i.expression) && i.expression.text === 'require') {
    const s = strOf(i.arguments[0]); if (s != null) modBind.set(keyOf(d.name), { spec: s, imported: '*' });
  }
}
// The module function a callee names: `fn` imported from `mods`, or
// `ns.fn` / `ns.promises.fn` / `ns.posix.fn` over a namespace or default
// import of one.
function modFn(callee, mods) {
  callee = unwrap(callee);
  if (ts.isIdentifier(callee)) { const b = modBind.get(keyOf(callee)); return b && mods.has(b.spec) && !['*', 'default', 'promises', 'posix'].includes(b.imported) ? b.imported : null; }
  if (!ts.isPropertyAccessExpression(callee)) return null;
  let r = unwrap(callee.expression); let sub = false;
  if (ts.isPropertyAccessExpression(r) && ['promises', 'posix'].includes(r.name.text)) { r = unwrap(r.expression); sub = true; }
  if (!ts.isIdentifier(r)) return null;
  const b = modBind.get(keyOf(r));
  return b && mods.has(b.spec) && (b.imported === '*' || b.imported === 'default' || (!sub && ['promises', 'posix'].includes(b.imported))) ? callee.name.text : null;
}
// --- the kinds ---
const T = 'T', B = 'B';
const meet = (a, b) => (a === T ? b : b === T ? a : a === b ? a : B);
const elemOf = (k) => (k === 'A' ? 'P' : k === 'NA' ? 'N' : k === T ? T : B);
const arrOf = (k) => (k === 'P' ? 'A' : k === 'N' ? 'NA' : k === T ? T : B);
const isArr = (k) => k === 'A' || k === 'NA' || k === T;
const safeSeg = (s) => !s.startsWith('/') && !s.split(/[\\/]/).includes('..');
const bindKind = new Map(); const bindSrc = new Map(); // binding key -> [thunk]
const retKind = new Map(); const retSrc = new Map(); // function node -> [expr | null]
const fnByKey = new Map(); // binding key -> function node (null when bound twice)
const bottom = () => B;
const addSrc = (id, s) => { const k = keyOf(id); if (!bindSrc.has(k)) bindSrc.set(k, []); bindSrc.get(k).push(s); };
function bindAll(b, s) { // every name a pattern binds gets `s`
  if (!b) return;
  if (ts.isIdentifier(b)) addSrc(b, s);
  else if (b.elements) b.elements.forEach(e => ts.isOmittedExpression(e) ? null : bindAll(e.name || (ts.isSpreadElement(e) ? e.expression : e), bottom));
  else if (b.properties) b.properties.forEach(p => bindAll(p.initializer || p.name || p.expression, bottom));
  else if (ts.isBinaryExpression(b)) bindAll(b.left, bottom);
}
const fnNameId = (f) => (ts.isFunctionDeclaration(f) && f.name ? f.name
  : f.parent && ts.isVariableDeclaration(f.parent) && f.parent.initializer === f && ts.isIdentifier(f.parent.name) ? f.parent.name : null);
const ARRAY_CB = new Set(['map', 'flatMap', 'filter', 'forEach', 'find', 'findLast', 'findIndex', 'some', 'every', 'sort', 'toSorted']);
(function v(x) {
  if (ts.isVariableDeclaration(x)) {
    const fo = x.parent && x.parent.parent;
    if (fo && ts.isForOfStatement(fo) && fo.initializer === x.parent) bindAll(x.name, ts.isIdentifier(x.name) ? () => elemOf(kind(fo.expression)) : bottom);
    else if (fo && ts.isForInStatement(fo) && fo.initializer === x.parent) bindAll(x.name, bottom);
    else if (ts.isIdentifier(x.name)) { if (x.initializer) addSrc(x.name, () => kind(x.initializer)); }
    else bindAll(x.name, bottom);
  } else if (ts.isBinaryExpression(x) && x.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && x.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
    const l = unwrap(x.left); const plain = x.operatorToken.kind === ts.SyntaxKind.EqualsToken;
    if (ts.isIdentifier(l)) addSrc(l, plain ? () => kind(x.right) : bottom);
    else if (ts.isElementAccessExpression(l) && ts.isIdentifier(unwrap(l.expression))) addSrc(unwrap(l.expression), plain ? () => arrOf(kind(x.right)) : bottom);
    else if (ts.isObjectLiteralExpression(l) || ts.isArrayLiteralExpression(l)) bindAll(l, bottom);
  } else if ((ts.isForOfStatement(x) || ts.isForInStatement(x)) && !ts.isVariableDeclarationList(x.initializer)) {
    const l = unwrap(x.initializer);
    if (ts.isIdentifier(l)) addSrc(l, ts.isForOfStatement(x) ? () => elemOf(kind(x.expression)) : bottom);
    else bindAll(l, bottom);
  } else if ((ts.isPrefixUnaryExpression(x) || ts.isPostfixUnaryExpression(x)) && ts.isIdentifier(unwrap(x.operand))) {
    addSrc(unwrap(x.operand), bottom);
  } else if (ts.isCatchClause(x) && x.variableDeclaration) {
    bindAll(x.variableDeclaration.name, bottom);
  } else if (ts.isCallExpression(x) && ts.isPropertyAccessExpression(x.expression) && ts.isIdentifier(unwrap(x.expression.expression))) {
    const n = unwrap(x.expression.expression); const m = x.expression.name.text;
    if (m === 'push' || m === 'unshift') x.arguments.forEach(a => addSrc(n, ts.isSpreadElement(a) ? () => kind(a.expression) : () => arrOf(kind(a))));
    else if (['splice', 'fill', 'copyWithin'].includes(m)) addSrc(n, bottom);
  }
  if (isFnScope(x)) {
    const nm = fnNameId(x);
    if (nm) { const k = keyOf(nm); fnByKey.set(k, fnByKey.has(k) ? null : x); }
    const rs = []; retSrc.set(x, rs);
    if (!ts.isBlock(x.body)) rs.push(x.body);
    else (function r(y) { if (ts.isReturnStatement(y)) { rs.push(y.expression || null); return; } if (isFnScope(y)) return; ts.forEachChild(y, r); })(x.body);
  }
  ts.forEachChild(x, v);
})(sf);
// A named function's parameters take its call sites' arguments; any other
// use of its name (passed, reassigned, `.call`) lets it escape: B. An array
// callback's first parameter (a comparator's two) takes its receiver's
// elements. Every other parameter is B.
const callSites = new Map(); const escaped = new Set();
(function v(x) {
  if (ts.isIdentifier(x) && fnByKey.has(keyOf(x))) {
    const k = keyOf(x); const p = x.parent;
    if (p && ts.isCallExpression(p) && p.expression === x) { if (!callSites.has(k)) callSites.set(k, []); callSites.get(k).push(p); }
    else if (!(p && (ts.isFunctionDeclaration(p) || ts.isVariableDeclaration(p)) && p.name === x)) escaped.add(k);
  }
  ts.forEachChild(x, v);
})(sf);
for (const f of retSrc.keys()) {
  const nm = fnNameId(f); const k = nm && keyOf(nm); const named = k && fnByKey.get(k) === f && !escaped.has(k);
  const p = f.parent; const cbOf = p && ts.isCallExpression(p) && p.arguments[0] === f && ts.isPropertyAccessExpression(p.expression)
    && ARRAY_CB.has(p.expression.name.text) ? p.expression.expression : null;
  f.parameters.forEach((prm, i) => {
    if (!ts.isIdentifier(prm.name) || prm.dotDotDotToken) return bindAll(prm.name, bottom);
    if (prm.initializer) addSrc(prm.name, () => kind(prm.initializer));
    if (named) for (const c of callSites.get(k) || []) { const a = c.arguments[i]; if (a) addSrc(prm.name, ts.isSpreadElement(a) ? bottom : () => kind(a)); }
    else if (cbOf && (i === 0 || (i === 1 && /sort/i.test(p.expression.name.text)))) addSrc(prm.name, () => elemOf(kind(cbOf)));
    else addSrc(prm.name, bottom);
  });
}
function fnOf(e) { e = e && unwrap(e); if (e && ts.isIdentifier(e)) { const k = keyOf(e); return escaped.has(k) ? null : fnByKey.get(k); } return e; }
function retOf(e) { const f = fnOf(e); return f && retKind.has(f) ? retKind.get(f) : B; }
function kind(e) {
  e = unwrap(e);
  if (!e) return B;
  if (ts.isIdentifier(e)) { const k = keyOf(e); return bindKind.has(k) ? bindKind.get(k) : B; }
  if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) return safeSeg(e.text) ? 'N' : B;
  if (ts.isTemplateExpression(e)) { // `${dir}/a/${name}`: a fresh path first, then plain segments
    if (e.head.text !== '') return B;
    let k = kind(e.templateSpans[0].expression);
    if (k !== 'P' && k !== T) return B;
    for (const [i, s] of e.templateSpans.entries()) {
      if (s.literal.text.split('/').includes('..')) return B;
      if (i) { const j = kind(s.expression); if (meet(j, 'N') === B) return B; if (j === T) k = T; }
    }
    return k;
  }
  if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const l = kind(e.left), r = kind(e.right);
    if ((l !== 'P' && l !== T) || meet(r, 'N') === B) return B;
    return l === T || r === T ? T : 'P';
  }
  if (ts.isConditionalExpression(e)) return meet(kind(e.whenTrue), kind(e.whenFalse));
  if (ts.isArrayLiteralExpression(e)) {
    let k = T;
    for (const el of e.elements) k = meet(k, ts.isSpreadElement(el) ? kind(el.expression) : arrOf(kind(el)));
    return k;
  }
  if (ts.isElementAccessExpression(e)) return elemOf(kind(e.expression));
  if (ts.isPropertyAccessExpression(e)) { const r = kind(e.expression); return e.name.text === 'name' && (r === 'N' || r === T) ? r : B; } // a Dirent's name
  if (ts.isCallExpression(e)) {
    const fsf = modFn(e.expression, FS_MODS); const pf = modFn(e.expression, PATH_MODS);
    if (fsf === 'mkdtempSync' || fsf === 'mkdtemp') return 'P';
    if (fsf === 'readdirSync' || fsf === 'readdir') return 'NA';
    if (pf === 'join' || pf === 'resolve') {
      if (!e.arguments.length) return B;
      let k = kind(e.arguments[0]); if (k !== 'P' && k !== T) return B;
      for (const a of e.arguments.slice(1)) { const s = ts.isSpreadElement(a) ? B : kind(a); if (meet(s, 'N') === B) return B; if (s === T) k = T; }
      return k;
    }
    if (fsf || pf) return B;
    if (ts.isPropertyAccessExpression(e.expression)) {
      const m = e.expression.name.text; const rk = kind(e.expression.expression);
      if (!isArr(rk)) return B;
      if (['filter', 'slice', 'sort', 'reverse', 'toSorted', 'toReversed'].includes(m)) return rk;
      if (m === 'concat') return e.arguments.reduce((k, a) => meet(k, kind(a)), rk);
      if (['find', 'findLast', 'at', 'pop', 'shift'].includes(m)) return elemOf(rk);
      if (m === 'map') return arrOf(retOf(e.arguments[0]));
      if (m === 'flatMap') { const r = retOf(e.arguments[0]); return r === 'P' || r === 'N' ? arrOf(r) : r; }
      return B;
    }
    if (ts.isIdentifier(e.expression)) return retOf(e.expression);
    return B;
  }
  return B;
}
for (const k of bindSrc.keys()) bindKind.set(k, T);
for (const f of retSrc.keys()) retKind.set(f, T);
let settled = true;
for (let changed = true, i = 0; changed; i++) {
  if (i >= 200) { settled = false; break; }
  changed = false;
  for (const [k, ss] of bindSrc) { const v = ss.reduce((a, s) => meet(a, s()), T); if (v !== bindKind.get(k)) { bindKind.set(k, v); changed = true; } }
  for (const [f, rs] of retSrc) { const v = rs.length ? rs.reduce((a, r) => meet(a, r ? kind(r) : B), T) : B; if (v !== retKind.get(f)) { retKind.set(f, v); changed = true; } }
}
const fresh = (e) => settled && kind(e) === 'P';
const READ_FNS = new Set(['readFileSync', 'readFile', 'createReadStream']);
// Calls that put a file's text at another path: a fresh directory holding
// one whose source is not itself fresh may hold the tree's text.
const COPY_FNS = new Set(['copyFileSync', 'copyFile', 'cpSync', 'cp', 'linkSync', 'link', 'symlinkSync', 'symlink', 'renameSync', 'rename']);
const copies = [];
const EXEC_FNS = new Set(['execSync', 'exec', 'execFileSync', 'execFile', 'spawnSync', 'spawn', 'execa', '$']);
for (const r of reach) (function v(x) {
  if (ts.isCallExpression(x) || ts.isNewExpression(x)) {
    const e = x.expression; const name = ts.isIdentifier(e) ? e.text : (ts.isPropertyAccessExpression(e) ? e.name.text : null);
    const args = x.arguments || [];
    if (name && READ_FNS.has(name)) reads.push({ fn: name, lits: args.length ? litsIn(args[0]) : [], line: sf.getLineAndCharacterOfPosition(x.getStart()).line + 1, fresh: args.length ? fresh(args[0]) : false });
    if (name && COPY_FNS.has(name)) copies.push({ fn: name, fresh_src: args.length ? fresh(args[0]) : false });
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
// The reachable code's statements, nested ones included, for the
// at-assertion gate's binding slice (`_falsifier_failed_at_assertion`): the
// lines each spans, its innermost enclosing function, the names it binds (a
// variable statement's declarations, an assignment statement's targets) and
// the identifiers it reads (never a property name).
const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
const isFn = (x) => ts.isFunctionLike(x) && !ts.isTypeNode(x);
function namesIn(b, into) {
  if (ts.isIdentifier(b)) into.push(b.text);
  else if (ts.isObjectBindingPattern(b) || ts.isArrayBindingPattern(b)) b.elements.forEach(e => e.name && namesIn(e.name, into));
  else if (ts.isObjectLiteralExpression(b)) b.properties.forEach(p => namesIn(ts.isShorthandPropertyAssignment(p) ? p.name : (p.initializer || p.expression || p.name), into));
  else if (ts.isArrayLiteralExpression(b)) b.elements.forEach(e => namesIn(ts.isSpreadElement(e) ? e.expression : e, into));
  return into;
}
function bindsOf(st) {
  if (ts.isVariableStatement(st)) return st.declarationList.declarations.flatMap(d => namesIn(d.name, []));
  if (ts.isExpressionStatement(st)) {
    let e = st.expression;
    while (ts.isAwaitExpression(e) || ts.isParenthesizedExpression(e)) e = e.expression;
    if (ts.isBinaryExpression(e) && e.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
        && e.operatorToken.kind <= ts.SyntaxKind.LastAssignment) return namesIn(e.left, []);
  }
  return [];
}
function readsOf(st) {
  const out = new Set();
  (function v(x) {
    if (ts.isIdentifier(x)) {
      const p = x.parent;
      const propName = p && ((ts.isPropertyAccessExpression(p) && p.name === x)
        || (ts.isPropertyAssignment(p) && p.name === x)
        || (ts.isMethodDeclaration(p) && p.name === x)
        || (ts.isPropertyDeclaration(p) && p.name === x));
      if (!propName) out.add(x.text);
    }
    ts.forEachChild(x, v);
  })(st);
  return [...out];
}
const steps = []; const seenSteps = new Set();
for (const r of reach) (function v(x, fn) {
  if (isFn(x)) fn = x.pos;
  const exec = ts.isStatement(x) && !ts.isBlock(x) && !ts.isEmptyStatement(x);
  if (exec && !seenSteps.has(x)) {
    seenSteps.add(x);
    steps.push({ start: lineOf(x.getStart(sf)), end: lineOf(x.getEnd()), fn, binds: bindsOf(x), reads: readsOf(x) });
  }
  ts.forEachChild(x, c => v(c, fn));
})(r, -1);
// The reachable code's assertions, which the quoted assertion must lie in
// (DESIGN §8 *The quoted assertion is one the test framework makes*;
// `_falsifier_quoted_assertion_lines`): a call whose callee chain starts at
// `expect` or `assert` — the runner's global (a name nothing in scope
// declares), an import of that name from any module (a framework, or a
// fixture file re-exporting it), or any import of Node's `assert` — and,
// for `expect`, calls `expect(…)` / `expect.soft(…)` / `expect.poll(…)`
// itself, so an asymmetric matcher (`expect.any(String)`) is not one. Each
// is the lines of the outermost call of its chain.
const ASSERT_MODS = new Set(['assert', 'node:assert', 'assert/strict', 'node:assert/strict']);
const ASSERT_NAMES = new Set(['expect', 'assert']);
function assertKind(id) {
  const k = keyOf(id); const b = modBind.get(k);
  if (b) return ASSERT_MODS.has(b.spec) ? 'assert' : (ASSERT_NAMES.has(b.imported) ? b.imported : null);
  return k === 'global:' + id.text && ASSERT_NAMES.has(id.text) ? id.text : null;
}
function isAssertion(call) {
  let e = call; let own = false; let root = null;
  while (e) {
    e = unwrap(e);
    if (ts.isCallExpression(e)) {
      const c = unwrap(e.expression);
      if (ts.isIdentifier(c) || (ts.isPropertyAccessExpression(c) && ts.isIdentifier(unwrap(c.expression))
          && ['soft', 'poll'].includes(c.name.text))) own = true;
      e = e.expression;
    } else if (ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e)) e = e.expression;
    else { root = ts.isIdentifier(e) ? e : null; break; }
  }
  const kind = root && assertKind(root);
  return kind === 'assert' || (kind === 'expect' && own);
}
const chainOuter = (x) => { // the call continues a longer chain
  const p = x.parent;
  return !(p && ((ts.isPropertyAccessExpression(p) || ts.isElementAccessExpression(p) || ts.isCallExpression(p)
    || ts.isNonNullExpression(p)) && p.expression === x));
};
const asserts = []; const seenAsserts = new Set();
for (const r of reach) (function v(x) {
  if (ts.isCallExpression(x) && !seenAsserts.has(x) && chainOuter(x) && isAssertion(x)) {
    seenAsserts.add(x);
    asserts.push({ start: lineOf(x.getStart(sf)), end: lineOf(x.getEnd()) });
  }
  ts.forEachChild(x, v);
})(r);
// Every string and regular-expression literal of the reachable code, for the
// moved-literal classification (`_base_form_moved_literals`): text as the
// literal holds it, a regex as its source and flags.
const literals = []; const seenLit = new Set();
for (const r of reach) (function v(x) {
  let lit = null;
  if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) lit = { kind: 'string', text: x.text };
  else if (x.kind === ts.SyntaxKind.RegularExpressionLiteral) {
    const t = x.getText(sf); const i = t.lastIndexOf('/');
    lit = { kind: 'regex', text: t.slice(1, i), flags: t.slice(i + 1) };
  }
  if (lit) { const k = lit.kind + '\0' + lit.text + '\0' + (lit.flags || ''); if (!seenLit.has(k)) { seenLit.add(k); literals.push(lit); } }
  ts.forEachChild(x, v);
})(r);
const reach_src = [...reach].map(n => n.getText(sf));
emit({ title: best.title, reach_src, literals, case_src: best.node.getText(sf), steps, asserts, reads, copies, execs, evals, imports: usedImports,
  vm_import: imports.some(s => s === 'vm' || s === 'node:vm'), all_imports: imports });
