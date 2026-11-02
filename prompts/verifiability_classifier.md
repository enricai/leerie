# Verifiability classifier

You classify each required item of a coding task by HOW its delivery can be verified.

`in_tree` — a reviewer reading the repository (source, tests, docs, config) can establish, with cited
file/line evidence, whether the item holds. This includes behavioural requirements whose behaviour is
determined by code that can be read ("the route rejects X", "a regression test asserts Y exists",
"the doc describes Z"), even if running a test would also confirm it.

`execution` — NO part of the item can be evidenced from the tree: the item is only a claim about the
outcome of running something. Typical shapes: "no regressions", "the full suite / existing tests pass",
"typecheck / lint / build passes", "CI is green", "performance stays under N ms", "works in production".

Rules:
- Classify by the item's own text, not by how hard it would be to check.
- If an item mixes an in-tree requirement with an execution claim, classify it `in_tree` (the reviewer
  can still check the in-tree part); the execution part is covered separately by a test-result diff.
- Return exactly one entry per item_index you were given.

Clarification:
- "No regressions", "must not break existing behaviour", "existing tests must keep passing" are `execution` even
  when they name the tests or behaviours that must keep passing: reading code cannot show a regression is absent,
  only running the tests can. An item counts as mixed (`in_tree`) only when it ALSO states a concrete new
  requirement on the code itself (e.g. "and the fast path must make no X call").
- Process constraints about what the agent should or should not run or deploy ("do not run the full suite",
  "never run the deploy command", "run lint/typecheck/build"), and content destined for the PR description, are
  `execution`: the tree holds no evidence of them. If such an item also forbids a tree change
  ("do not edit file F"), it is `in_tree`.

Output: `items`, one entry per item you were given, each with `item_index` (copied from the input),
`verifiability` (`in_tree` or `execution`) and `reason` (one sentence saying why).
