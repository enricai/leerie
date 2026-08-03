## Length limits on the confidence block

Your `confidence` block is schema-validated, and these limits are enforced —
a submission that exceeds one is **rejected outright** and you have to redo
the whole response:

- `basis` — at most **8000 characters**
- each item in `falsifiers_tested` — at most **2000 characters**
- each item in `contradictions_reconciled` — at most **2000 characters**

These are generous: real submissions typically run 400–1400 characters for
`basis` and 130–500 per list item, so ordinary detailed evidence fits with
room to spare. Write the evidence you actually have. The limits exist only to
bound pathological cases, not to make you terse — if you find yourself near
one, prefer several focused list items over one very long item.
