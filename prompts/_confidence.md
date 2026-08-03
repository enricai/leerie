## Keep the confidence block tight

Only the numeric score axes and `basis` are required. `falsifiers_tested` and
`contradictions_reconciled` are optional — still fill them in when you have real
falsifiers or reconciled contradictions, because that is what makes the score
mean anything (§8), but a missing one costs a judgment, not the response.

There is no length limit and nothing is truncated. What matters is that the
block stays *compact*: an oversized confidence block measurably raises the
chance the whole tool call is corrupted in transport and your entire answer is
thrown away — including the parts that were right. Real submissions run about
400–1400 characters for `basis` and 130–500 per list item; that is plenty for
specific, cited evidence.

So: write the evidence you actually have, cite it concretely, and stop. Prefer
several focused list items over one sprawling one. Do not pad the basis with
restatement or narration of your process — a long block is not a stronger
block, and past some point it is a lost one.
