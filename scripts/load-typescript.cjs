// A `typescript` with the JS compiler API, for leerie's two AST scripts
// (test-case-facts.cjs, redrive-ast-gate.cjs; DESIGN §8 *Delivery
// detection*). Their facts are syntax-only — one file parsed, no type
// checking — so the compiler's version does not matter, only that it has
// the API. The tree under test's own `typescript` (resolved from the cwd)
// comes first; when it lacks `createSourceFile` (the TypeScript 7 native
// port exports a version string only) or does not resolve, the pinned copy
// the image ships, named by LEERIE_BUNDLED_TYPESCRIPT, is used.
// Run directly, it is the availability probe: prints `tree` or `bundled`
// and exits 0, or exits 1 when neither loads.
'use strict';

function hasApi(ts) {
  return !!ts && typeof ts.createSourceFile === 'function';
}

function tryLoad(load) {
  try { return load(); } catch (e) { return null; }
}

function loadTypescript() {
  const own = tryLoad(() => require(require.resolve('typescript',
    { paths: [process.cwd()] })));
  if (hasApi(own)) return { ts: own, source: 'tree' };
  const dir = process.env.LEERIE_BUNDLED_TYPESCRIPT;
  const bundled = dir ? tryLoad(() => require(dir)) : null;
  if (hasApi(bundled)) return { ts: bundled, source: 'bundled' };
  throw new Error('no typescript with the compiler API: the tree\'s '
    + (own ? 'has none' : 'does not resolve') + ', bundled '
    + (dir ? `${dir} does not load one` : 'copy not configured'));
}

module.exports = { loadTypescript };

if (require.main === module) {
  try {
    console.log(loadTypescript().source);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
