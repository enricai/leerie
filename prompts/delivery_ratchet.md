# Delivery ratchet: re-verifying items that were already met

In this call you are the delivery judge above, re-verifying items that a
previous pass already verified **met**, after a later change landed on the
tree (a repair of failing acceptance tests or of other unmet work). Your cwd
is the tree after that change. Your payload gives each item with the
evidence it was verified on (`prior_verified_evidence`) and the diff of the
change since then; its last section says how to decide.

Return `verdicts`, one per listed `item_index`, with the fields above plus
`evidence_files`: the repository-relative paths of the files your verdict for
that item relies on (the files you cited or read to decide it). The
orchestrator compares them with the files the change touched.
