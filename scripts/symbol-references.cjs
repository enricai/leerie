// Whether production code uses ONE symbol, from the TypeScript AST of the
// files that name it (DESIGN §8 *Delivery detection*: a probe imports "no
// symbol only tests use"). The falsifier's coupling check (`_falsifier_coupling`
// in leerie.py) asks it for each in-repo symbol a probe imports.
// Usage: node symbol-references.cjs <name> <module> <file>...
//   paths relative to the working directory (an installed tree of the commit
//   the check reads); <module> is the file that declares <name>.
//   -> JSON {"referenced": [file...], "unread": [file...]} on stdout.
// A file is `referenced` when an identifier <name> occurs in its code outside
// <name>'s own declaration: in <module>, its top-level declaration (name and
// body, so a self-recursive call is not a use) and its own export of it
// (`export { name }`, `export default name`) are not uses; anywhere else any
// value-level occurrence is (a call, a reference, an import, a re-export, a
// `ns.name` access). Comments, strings and type positions are never uses.
// Syntax only: a local of another symbol that shares the name reads as a use
// (as the whole-word text match it replaces did). `unread`: files it could not
// read. `typescript` comes from load-typescript.cjs: the tree's own when it
// has the compiler API, else the image's pinned copy.
'use strict';
const ts = require('./load-typescript.cjs').loadTypescript().ts;
const fs = require('fs');
const path = require('path');
const [name, moduleFile, ...files] = process.argv.slice(2);

function kindOf(f) {
  const ext = path.extname(f).toLowerCase();
  if (ext === '.tsx') return ts.ScriptKind.TSX;
  if (ext === '.jsx') return ts.ScriptKind.JSX;
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

const DECL = (n) => ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n)
  || ts.isEnumDeclaration(n) || ts.isModuleDeclaration(n);

// The spans of <name>'s own declaration in its module: top-level only — the
// symbol a probe imports is the module's own export.
function ownSpans(sf) {
  const spans = [];
  const add = (n) => spans.push([n.getStart(sf), n.getEnd()]);
  for (const st of sf.statements) {
    if (DECL(st) && st.name && st.name.text === name) add(st);
    else if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) {
        const names = [];
        (function collect(b) {
          if (ts.isIdentifier(b)) names.push(b.text);
          else if (b.elements) b.elements.forEach((e) => e.name && collect(e.name));
        })(d.name);
        if (names.includes(name)) add(d);
      }
    } else if (ts.isExportDeclaration(st) && !st.moduleSpecifier
               && st.exportClause && ts.isNamedExports(st.exportClause)) {
      for (const e of st.exportClause.elements)
        if ((e.propertyName || e.name).text === name) add(e);
    } else if (ts.isExportAssignment(st) && ts.isIdentifier(st.expression)
               && st.expression.text === name) add(st);
  }
  return spans;
}

function uses(sf, skip) {
  let hit = false;
  (function visit(n) {
    if (hit) return;
    // `class X extends name` is a value use, though its node is a type's.
    const classBase = ts.isExpressionWithTypeArguments(n)
      && ts.isHeritageClause(n.parent)
      && n.parent.token === ts.SyntaxKind.ExtendsKeyword
      && ts.isClassLike(n.parent.parent);
    if ((ts.isTypeNode(n) && !classBase) || ts.isInterfaceDeclaration(n)
        || ts.isTypeAliasDeclaration(n)) return;
    if ((ts.isImportDeclaration(n) && n.importClause && n.importClause.isTypeOnly)
        || (ts.isImportSpecifier(n) && n.isTypeOnly)
        || (ts.isExportDeclaration(n) && n.isTypeOnly)) return;
    const s = n.getStart(sf);
    if (skip.some(([a, b]) => s >= a && n.getEnd() <= b)) return;
    if (ts.isIdentifier(n) && n.text === name) { hit = true; return; }
    ts.forEachChild(n, visit);
  })(sf);
  return hit;
}

const referenced = [];
const unread = [];
for (const f of files) {
  let text;
  try { text = fs.readFileSync(f, 'utf8'); } catch (e) { unread.push(f); continue; }
  const sf = ts.createSourceFile(f, text, ts.ScriptTarget.Latest, true, kindOf(f));
  if (uses(sf, f === moduleFile ? ownSpans(sf) : [])) referenced.push(f);
}
console.log(JSON.stringify({ referenced, unread }));
