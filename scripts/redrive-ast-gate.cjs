// AST safety facts of ONE changed production file, base vs head, for the
// delivery re-drive's escalation gate (d+) (DESIGN §8 *Delivery re-drive*;
// bench q3/d dgate.js, unchanged except where `typescript` is loaded from).
// Usage: node redrive-ast-gate.cjs <base file> <head file> <name>
//   -> JSON {hits[], guardThrown[], newSwallowed[]} on stdout.
// D1 a throw / error-return guarded by a null|undefined|!x|typeof test in base
//    has no throw/error-return under the same (function, condition) in head.
// D2 head adds (not in base, same function) an `if (<null test>)` whose
//    consequent returns a success-shaped value, positioned before a throw /
//    error-return of the same function.
// D3 (decided in leerie.py across files): a catch new in head that swallows
//    an error class some changed file throws under a null guard.
// `typescript` comes from load-typescript.cjs: the installed tree under
// test's own (resolved from the working directory) when it has the compiler
// API, else the image's pinned copy. A missing base file means a new file:
// nothing to compare.
const ts = require('./load-typescript.cjs').loadTypescript().ts;
const fs = require('fs');
const [, , baseFile, headFile, name] = process.argv;

const norm = (s) => s.replace(/\s+/g, ' ').trim();

function isNullTest(e) {
  let hit = false;
  const visit = (n) => {
    if (ts.isBinaryExpression(n)) {
      const op = n.operatorToken.kind;
      const eq = [ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken,
        ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken].includes(op);
      const nullish = (x) => x.kind === ts.SyntaxKind.NullKeyword || (ts.isIdentifier(x) && x.text === 'undefined');
      if (eq && (nullish(n.left) || nullish(n.right))) hit = true;
      if (eq && (ts.isTypeOfExpression(n.left) || ts.isTypeOfExpression(n.right))) hit = true;
    }
    if (ts.isPrefixUnaryExpression(n) && n.operator === ts.SyntaxKind.ExclamationToken &&
        (ts.isIdentifier(n.operand) || ts.isPropertyAccessExpression(n.operand) || ts.isElementAccessExpression(n.operand))) hit = true;
    ts.forEachChild(n, visit);
  };
  visit(e);
  return hit;
}

function objProps(e) {
  const out = {};
  if (e && ts.isObjectLiteralExpression(e)) for (const p of e.properties) {
    if (ts.isPropertyAssignment(p) && p.name && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name))) out[p.name.text] = p.initializer;
  }
  return out;
}

function isErrorReturn(st) {
  if (!ts.isReturnStatement(st) || !st.expression) return false;
  let e = st.expression;
  while (ts.isAsExpression(e) || ts.isParenthesizedExpression(e) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(e))) e = e.expression;
  const p = objProps(e);
  if ('error' in p) return true;
  if ('ok' in p && p.ok.kind === ts.SyntaxKind.FalseKeyword) return true;
  if (ts.isCallExpression(e)) {
    const t = e.getText();
    if (/^(err|fail|error|badRequest|notFound|unauthorized|forbidden)\b/i.test(e.expression.getText())) return true;
    if (/status\s*:\s*4\d\d/.test(t)) return true;
  }
  return false;
}

function isSuccessReturn(st) {
  if (!ts.isReturnStatement(st) || !st.expression) return false;
  let e = st.expression;
  while (ts.isAsExpression(e) || ts.isParenthesizedExpression(e)) e = e.expression;
  if (e.kind === ts.SyntaxKind.TrueKeyword) return true;
  const p = objProps(e);
  for (const k of ['state', 'status', 'ok', 'success', 'verified', 'result']) {
    if (k in p) {
      const v = p[k];
      if (v.kind === ts.SyntaxKind.TrueKeyword) return true;
      if (ts.isStringLiteral(v) && !/fail|error|reject|invalid|miss|block|denied|unknown/i.test(v.text)) return true;
      if (ts.isPropertyAccessExpression(v) && /SUCCE|OK|PASS|HIT|DONE|COMPLETE|ACTIVE|PAID/i.test(v.name.text)) return true;
    }
  }
  if (ts.isPropertyAccessExpression(e) && /SUCCE|OK|PASS|HIT|DONE|COMPLETE/.test(e.name.text)) return true;
  return false;
}

function fnName(n) {
  let p = n.parent;
  while (p) {
    if ((ts.isFunctionDeclaration(p) || ts.isMethodDeclaration(p)) && p.name) return p.name.getText();
    if ((ts.isArrowFunction(p) || ts.isFunctionExpression(p)) && p.parent && ts.isVariableDeclaration(p.parent)) return p.parent.name.getText();
    p = p.parent;
  }
  return '<top>';
}

function stmtsOf(s) { return ts.isBlock(s) ? Array.from(s.statements) : [s]; }

function scan(file) {
  const src = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(name, src, ts.ScriptTarget.Latest, true, name.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const guards = [];
  const ifs = [];
  const errors = [];
  const guardThrown = new Set();
  const swallowed = [];
  const visit = (n) => {
    if (ts.isCatchClause(n) && n.variableDeclaration) {
      const v = n.variableDeclaration.name.getText();
      const inst = (x) => {
        while (ts.isParenthesizedExpression(x)) x = x.expression;
        return ts.isBinaryExpression(x) && x.operatorToken.kind === ts.SyntaxKind.InstanceOfKeyword && x.left.getText() === v ? x.right.getText() : null;
      };
      const walk = (m) => {
        if (ts.isIfStatement(m)) {
          const body = stmtsOf(m.thenStatement);
          let e = m.expression, negated = false;
          while (ts.isParenthesizedExpression(e)) e = e.expression;
          if (ts.isPrefixUnaryExpression(e) && e.operator === ts.SyntaxKind.ExclamationToken) { negated = true; e = e.operand; }
          const cls = inst(e);
          if (cls && negated && body.some((st) => ts.isThrowStatement(st) && st.expression && st.expression.getText() === v)) swallowed.push({ cls, fn: fnName(n), text: norm(n.getText()).slice(0, 160) });
          if (cls && !negated && body.some((st) => ts.isReturnStatement(st) && !isErrorReturn(st)) && !body.some(ts.isThrowStatement)) swallowed.push({ cls, fn: fnName(n), text: norm(n.getText()).slice(0, 160) });
        }
        ts.forEachChild(m, walk);
      };
      walk(n.block);
    }
    if (ts.isThrowStatement(n) || isErrorReturn(n)) errors.push({ fn: fnName(n), pos: n.getStart() });
    if (ts.isIfStatement(n) && isNullTest(n.expression)) {
      const body = stmtsOf(n.thenStatement);
      const guard = body.some((s) => ts.isThrowStatement(s) || isErrorReturn(s));
      const succ = body.length <= 3 && body.some(isSuccessReturn);
      const rec = { fn: fnName(n), cond: norm(n.expression.getText()), pos: n.getStart(), text: norm(n.getText()).slice(0, 200) };
      if (guard) {
        guards.push(rec);
        for (const st of body) if (ts.isThrowStatement(st) && st.expression && ts.isNewExpression(st.expression)) guardThrown.add(st.expression.expression.getText());
      }
      if (succ) ifs.push(rec);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return { guards, ifs, errors, guardThrown: [...guardThrown], swallowed };
}

const B = baseFile && fs.existsSync(baseFile) ? scan(baseFile) : { guards: [], ifs: [], errors: [], guardThrown: [], swallowed: [] };
const Hh = scan(headFile);
const hits = [];
const headGuardKeys = new Set(Hh.guards.map((g) => g.fn + '|' + g.cond));
for (const g of B.guards) {
  if (!headGuardKeys.has(g.fn + '|' + g.cond)) hits.push({ rule: 'D1_guard_removed', fn: g.fn, cond: g.cond, base: g.text });
}
const baseIfKeys = new Set(B.ifs.map((i) => i.fn + '|' + i.text));
for (const i of Hh.ifs) {
  if (baseIfKeys.has(i.fn + '|' + i.text)) continue;
  if (Hh.errors.some((e) => e.fn === i.fn && e.pos > i.pos)) hits.push({ rule: 'D2_early_success_return', fn: i.fn, head: i.text });
}
const baseSw = new Set(B.swallowed.map((x) => x.fn + '|' + x.cls));
const newSwallowed = Hh.swallowed.filter((x) => !baseSw.has(x.fn + '|' + x.cls));
process.stdout.write(JSON.stringify({ hits, guardThrown: Hh.guardThrown, newSwallowed }));
